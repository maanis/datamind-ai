/**
 * routes/stream.js
 *
 * Routes for real-time pipeline streaming via SSE.
 *
 * POST /query/stream          — Start a streaming query, returns { sessionId }
 * GET  /query/stream/:id      — SSE endpoint, frontend subscribes here
 * POST /query/stream/:id/event — Internal: Python calls this to append events
 */

const express = require('express');
const router = express.Router();
const auth = require('../middlewares/auth');
const {
    initiateStreamQuery,
    subscribeToStream,
    appendStreamEvent
} = require('../controllers/streamController');

// Start a streaming query (authenticated)
router.post('/', auth, initiateStreamQuery);

// SSE subscription (authenticated)
router.get('/:sessionId', auth, subscribeToStream);

// Internal event callback (called by Python — no user auth, but could add API key check)
router.post('/:sessionId/event', appendStreamEvent);

module.exports = router;
