/**
 * controllers/streamController.js
 *
 * Real-time pipeline streaming via Server-Sent Events (SSE).
 *
 * FLOW:
 * 1. Frontend calls POST /query/stream → gets back { sessionId }
 * 2. Frontend opens SSE connection: GET /query/stream/:sessionId
 * 3. Node.js polls MongoDB StreamEvent doc every 300ms
 * 4. Each new event pushed to frontend via SSE
 * 5. Python FastAPI writes events to MongoDB during query processing
 * 6. When Python sends "done" event → SSE connection closes
 * 7. StreamEvent auto-deletes after 1 hour (TTL index)
 *
 * UX RESULT:
 * User sees: "🔍 Searching workspace..." → "⚡ Running SQL..." → "✍️ Generating answer..."
 * Instead of just a spinner for 10 seconds.
 *
 * Like Claude's "thinking" indicator, but for YOUR backend steps.
 */

const axios = require('axios');
const { v4: uuidv4 } = require('uuid');
const mongoose = require('mongoose');

const Workspace = require('../models/Workspace');
const StreamEvent = require('../models/StreamEvent');

const PYTHON_API_BASE = process.env.PYTHON_API_BASE || 'http://localhost:8000';

// How often to poll MongoDB for new events (ms)
const POLL_INTERVAL_MS = 250;

// Max time to keep SSE connection open before timeout
const SSE_TIMEOUT_MS = 120_000; // 2 minutes


/**
 * POST /query/stream
 *
 * Kicks off a streaming query. Returns sessionId immediately.
 * Frontend uses sessionId to open SSE connection.
 *
 * Body: { question, workspaceId, documentId? }
 * Returns: { sessionId, message }
 */
const initiateStreamQuery = async (req, res) => {
    try {
        const { question, workspaceId, documentId } = req.body;

        if (!question) {
            return res.status(400).json({ message: 'Question is required' });
        }
        if (!workspaceId || !mongoose.Types.ObjectId.isValid(workspaceId)) {
            return res.status(400).json({ message: 'Valid workspace ID is required' });
        }

        // Verify workspace ownership
        const workspace = await Workspace.findOne({
            _id: workspaceId,
            userId: req.user._id
        });

        if (!workspace) {
            return res.status(404).json({ message: 'Workspace not found or access denied' });
        }

        // Generate unique session ID for this query
        const sessionId = uuidv4();

        // Create stream document in MongoDB (events will be appended by Python)
        await StreamEvent.create({
            sessionId,
            workspaceId,
            userId: req.user._id,
            events: [{
                type: 'step',
                message: '🧠 Processing your question...',
                tool: 'system',
                timestamp: new Date()
            }],
            status: 'active'
        });

        // Fire-and-forget: call Python API (it will write events to MongoDB)
        // We don't await this — it runs in background while SSE streams events
        _callPythonStreamQuery({
            sessionId,
            workspaceId,
            question,
            documentId: documentId || null
        }).catch(err => {
            console.error(`[stream] Python call failed for session ${sessionId}:`, err.message);
            // Write error event to MongoDB so SSE can close gracefully
            _appendErrorEvent(sessionId, err.message);
        });

        // Return sessionId immediately — frontend opens SSE with this
        return res.json({
            sessionId,
            message: 'Query started. Connect to SSE endpoint to receive events.'
        });

    } catch (error) {
        console.error('[initiateStreamQuery] Error:', error.message);
        return res.status(500).json({ message: 'Failed to start streaming query', error: error.message });
    }
};


/**
 * GET /query/stream/:sessionId
 *
 * SSE endpoint. Client connects here and receives real-time events.
 * Polls MongoDB every 250ms for new events and pushes them to client.
 *
 * SSE event format:
 *   data: {"type":"step","message":"🔍 Searching...","tool":"semantic_search"}\n\n
 *   data: {"type":"answer_chunk","message":"The answer is..."}\n\n
 *   data: {"type":"done","message":"Complete"}\n\n
 */
const subscribeToStream = async (req, res) => {
    const { sessionId } = req.params;

    if (!sessionId) {
        return res.status(400).json({ message: 'Session ID required' });
    }

    // Verify session belongs to this user
    const streamDoc = await StreamEvent.findOne({ sessionId });
    if (!streamDoc) {
        return res.status(404).json({ message: 'Stream session not found' });
    }

    if (streamDoc.userId.toString() !== req.user._id.toString()) {
        return res.status(403).json({ message: 'Access denied' });
    }

    // Set SSE headers
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no'); // Disable Nginx buffering
    res.flushHeaders();

    let sentEventCount = 0;
    let isConnected = true;
    let pollTimer = null;
    let timeoutTimer = null;

    const sendEvent = (event) => {
        if (!isConnected) return;
        try {
            res.write(`data: ${JSON.stringify(event)}\n\n`);
        } catch (e) {
            isConnected = false;
        }
    };

    const cleanup = () => {
        isConnected = false;
        if (pollTimer) clearTimeout(pollTimer);
        if (timeoutTimer) clearTimeout(timeoutTimer);
    };

    // Client disconnected
    req.on('close', cleanup);

    // Global timeout
    timeoutTimer = setTimeout(() => {
        if (isConnected) {
            sendEvent({ type: 'error', message: 'Query timeout. Please try again.' });
            res.end();
        }
        cleanup();
    }, SSE_TIMEOUT_MS);

    // Poll MongoDB for new events
    const poll = async () => {
        if (!isConnected) return;

        try {
            const doc = await StreamEvent.findOne({ sessionId }, { events: 1, status: 1 });

            if (!doc) {
                sendEvent({ type: 'error', message: 'Stream session expired' });
                res.end();
                cleanup();
                return;
            }

            // Send any new events since last poll
            const newEvents = doc.events.slice(sentEventCount);
            for (const event of newEvents) {
                sendEvent(event);
                sentEventCount++;
            }

            // Check if stream is done
            if (doc.status === 'completed' || doc.status === 'error') {
                res.end();
                cleanup();
                return;
            }

            // Continue polling
            if (isConnected) {
                pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
            }

        } catch (err) {
            console.error('[SSE poll] Error:', err.message);
            if (isConnected) {
                pollTimer = setTimeout(poll, POLL_INTERVAL_MS * 2);
            }
        }
    };

    // Start polling immediately
    poll();
};


/**
 * POST /query/stream/:sessionId/event
 *
 * Internal endpoint called by Python FastAPI to append events.
 * NOT exposed to frontend — internal only.
 * 
 * Python calls this for each pipeline step:
 *   { type, message, tool, data? }
 */
const appendStreamEvent = async (req, res) => {
    const { sessionId } = req.params;
    const { type, message, tool, data, status, finalAnswer, intent } = req.body;

    try {
        const update = {
            $push: {
                events: { type, message, tool: tool || 'system', data: data || null, timestamp: new Date() }
            }
        };

        // Update status if provided
        if (status) update.$set = { status };
        if (finalAnswer) {
            if (!update.$set) update.$set = {};
            update.$set.finalAnswer = finalAnswer;
        }
        if (intent) {
            if (!update.$set) update.$set = {};
            update.$set.intent = intent;
        }

        await StreamEvent.updateOne({ sessionId }, update);
        return res.json({ ok: true });

    } catch (err) {
        console.error('[appendStreamEvent] Error:', err.message);
        return res.status(500).json({ message: err.message });
    }
};


// ---------------------------------------------------------------------------
// INTERNAL HELPERS
// ---------------------------------------------------------------------------

/**
 * Call Python /query-stream endpoint.
 * Python will write events to MongoDB via /query/stream/:sessionId/event
 */
async function _callPythonStreamQuery({ sessionId, workspaceId, question, documentId }) {
    const response = await axios.post(`${PYTHON_API_BASE}/query-stream`, {
        workspace_id: workspaceId,
        question,
        document_id: documentId,
        session_id: sessionId,
        // Python will call back Node.js to write events
        event_callback_url: `${process.env.NODE_API_BASE || 'http://localhost:3000'}/query/stream/${sessionId}/event`
    }, {
        timeout: 120_000 // 2 minute timeout for complex queries
    });

    return response.data;
}

async function _appendErrorEvent(sessionId, errorMessage) {
    try {
        await StreamEvent.updateOne(
            { sessionId },
            {
                $push: {
                    events: {
                        type: 'error',
                        message: `Error: ${errorMessage}`,
                        tool: 'system',
                        timestamp: new Date()
                    }
                },
                $set: { status: 'error' }
            }
        );
    } catch (e) {
        console.error('[_appendErrorEvent]', e.message);
    }
}


module.exports = {
    initiateStreamQuery,
    subscribeToStream,
    appendStreamEvent
};
