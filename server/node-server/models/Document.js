const mongoose = require('mongoose');

const documentSchema = new mongoose.Schema({
    workspaceId: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Workspace',
        required: true,
        index: true
    },
    fileName: {
        type: String,
        required: true
    },
    fileType: {
        type: String,
        enum: ['csv', 'pdf', 'json', 'text', 'excel'],
        required: true
    },
    ingestionStatus: {
        type: String,
        enum: ['pending', 'processing', 'completed', 'failed'],
        default: 'pending'
    },
    storageMode: {
        type: String,
        enum: ['rag', 'sqlite', 'hybrid']
    },
    tableName: {
        type: String
    },
    vectorCount: {
        type: Number
    },
    metadata: {
        type: mongoose.Schema.Types.Mixed
    },
    // Lightweight metadata for query planning (LLM-generated)
    metadataForQuery: {
        type: {
            type: String,
            enum: ['rag', 'structured']
        },
        summary: String,
        description: String,
        tableName: String,
        columns: [String],
        keywords: [String],
        storageMode: String
    },
    createdAt: {
        type: Date,
        default: Date.now
    }
});

// Add indexes
documentSchema.index({ workspaceId: 1, ingestionStatus: 1 }); // For filtering documents by workspace and status

module.exports = mongoose.model('Document', documentSchema);