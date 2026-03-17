const axios = require('axios');
const mongoose = require('mongoose');

// Import models
const Workspace = require('../models/Workspace');

const PYTHON_API_BASE = process.env.PYTHON_API_BASE || 'http://localhost:8000';

// Helper function to extract safe error information
function extractErrorInfo(error) {
    let statusCode = 500;
    let errorMessage = 'Internal server error';
    let errorDetails = error.message;

    if (error.response) {
        statusCode = error.response.status || 500;
        errorMessage = error.response.statusText || 'API Error';

        try {
            if (typeof error.response.data === 'string') {
                errorDetails = error.response.data;
            } else if (error.response.data && typeof error.response.data === 'object') {
                if (error.response.data.message) {
                    errorDetails = error.response.data.message;
                } else if (error.response.data.error) {
                    errorDetails = error.response.data.error;
                } else if (error.response.data.detail) {
                    errorDetails = error.response.data.detail;
                } else {
                    errorDetails = error.response.statusText || 'Unknown API error';
                }
            }
        } catch (e) {
            errorDetails = error.response.statusText || 'API request failed';
        }
    }

    return { statusCode, errorMessage, errorDetails };
}

/**
 * Handle query using the refactored Python /query endpoint.
 * Supports:
 * - LLM abstraction (Ollama/Gemini)
 * - Intent classification (semantic/structured/hybrid/clarification)
 * - Tool execution (search, SQL, clarification)
 * - Conversation memory (last 6 messages)
 */
const handleQuery = async (req, res) => {
    try {
        const { question, workspaceId, documentId, stream = false } = req.body;

        if (!question) {
            return res.status(400).json({ message: 'Question is required' });
        }
        if (!workspaceId || !mongoose.Types.ObjectId.isValid(workspaceId)) {
            return res.status(400).json({ message: 'Valid workspace ID is required' });
        }

        // Verify workspace belongs to authenticated user
        const workspace = await Workspace.findOne({
            _id: workspaceId,
            userId: req.user._id
        });

        if (!workspace) {
            return res.status(404).json({ message: 'Workspace not found or access denied' });
        }

        if (stream) {
            // Handle streaming response
            const response = await axios.post(
                `${PYTHON_API_BASE}/query`,
                {
                    workspace_id: workspaceId,
                    question: question,
                    document_id: documentId || null,
                    stream: true
                },
                {
                    responseType: 'stream'
                }
            );

            res.setHeader('Content-Type', 'text/plain');

            response.data.on('data', (chunk) => {
                const text = chunk.toString();
                process.stdout.write(text);
                res.write(text);
            });

            response.data.on('end', () => {
                console.log('\n--- Stream finished ---');
                res.end();
            });

            response.data.on('error', (err) => {
                console.error('Stream error:', err);
                res.end();
            });
        } else {
            // Handle non-streaming response
            const response = await axios.post(`${PYTHON_API_BASE}/query`, {
                workspace_id: workspaceId,
                question: question,
                document_id: documentId || null,
                stream: false
            });

            res.json(response.data);
        }

    } catch (error) {
        console.error('Query error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
};

/**
 * Clear conversation memory for a workspace.
 */
const clearMemory = async (req, res) => {
    try {
        const { workspaceId } = req.body;

        if (!workspaceId || !mongoose.Types.ObjectId.isValid(workspaceId)) {
            return res.status(400).json({ message: 'Valid workspace ID is required' });
        }

        // Verify workspace belongs to authenticated user
        const workspace = await Workspace.findOne({
            _id: workspaceId,
            userId: req.user._id
        });

        if (!workspace) {
            return res.status(404).json({ message: 'Workspace not found or access denied' });
        }

        const response = await axios.post(`${PYTHON_API_BASE}/clear-memory`, {
            workspace_id: workspaceId
        });

        res.json(response.data);

    } catch (error) {
        console.error('Clear memory error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
};

/**
 * Get conversation memory for a workspace.
 */
const getMemory = async (req, res) => {
    try {
        const { workspaceId } = req.params;

        if (!workspaceId || !mongoose.Types.ObjectId.isValid(workspaceId)) {
            return res.status(400).json({ message: 'Valid workspace ID is required' });
        }

        // Verify workspace belongs to authenticated user
        const workspace = await Workspace.findOne({
            _id: workspaceId,
            userId: req.user._id
        });

        if (!workspace) {
            return res.status(404).json({ message: 'Workspace not found or access denied' });
        }

        // Return the conversation memory from the workspace
        res.json({
            workspaceId: workspaceId,
            memory: workspace.conversationMemory || { messages: [] }
        });

    } catch (error) {
        console.error('Get memory error:', error.message);
        res.status(500).json({
            message: 'Internal server error',
            error: error.message
        });
    }
};

module.exports = {
    handleQuery,
    clearMemory,
    getMemory
};
