const mongoose = require('mongoose');

const messageSchema = new mongoose.Schema({
    role: {
        type: String,
        enum: ['user', 'assistant'],
        required: true
    },
    content: {
        type: String,
        required: true
    }
}, { _id: false });

const conversationMemorySchema = new mongoose.Schema({
    messages: {
        type: [messageSchema],
        default: []
    }
}, { _id: false });

const workspaceSchema = new mongoose.Schema({
    name: {
        type: String,
        required: true,
        trim: true
    },
    userId: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'User',
        required: true,
        index: true
    },
    vectorCollection: {
        type: String,
        required: true
    },
    sqliteDbPath: {
        type: String
    },
    totalVectors: {
        type: Number,
        default: 0
    },
    totalDocuments: {
        type: Number,
        default: 0
    },
    conversationMemory: {
        type: conversationMemorySchema,
        default: () => ({ messages: [] })
    },
    createdAt: {
        type: Date,
        default: Date.now
    }
});

// Add indexes
workspaceSchema.index({ userId: 1, name: 1 }); // Compound index for user workspaces

module.exports = mongoose.model('Workspace', workspaceSchema);