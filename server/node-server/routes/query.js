const express = require('express');
const router = express.Router();
const { handleQuery, clearMemory, getMemory } = require('../controllers/queryController');
const auth = require('../middlewares/auth');

/**
 * POST /query
 * Main query endpoint with:
 * - LLM abstraction (Ollama/Gemini)
 * - Intent classification (semantic/structured/hybrid/clarification)
 * - Tool execution (search, SQL, clarification)
 * - Conversation memory (last 6 messages)
 */
router.post('/', auth, handleQuery);

/**
 * POST /query/clear-memory
 * Clear conversation memory for a workspace
 */
router.post('/clear-memory', auth, clearMemory);

/**
 * GET /query/memory/:workspaceId
 * Get conversation memory for a workspace
 */
router.get('/memory/:workspaceId', auth, getMemory);

module.exports = router;