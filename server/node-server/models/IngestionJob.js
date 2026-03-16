const mongoose = require('mongoose');

const ingestionJobSchema = new mongoose.Schema({
    workspaceId: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Workspace',
        required: true
    },
    documentId: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Document',
        required: true
    },
    status: {
        type: String,
        enum: ['queued', 'processing', 'completed', 'failed'],
        default: 'queued'
    },
    errorMessage: {
        type: String
    },
    startedAt: {
        type: Date
    },
    completedAt: {
        type: Date
    },
    createdAt: {
        type: Date,
        default: Date.now
    }
});

// Add indexes
ingestionJobSchema.index({ workspaceId: 1, status: 1 }); // For filtering jobs by workspace and status
ingestionJobSchema.index({ documentId: 1 }); // For finding jobs by document

module.exports = mongoose.model('IngestionJob', ingestionJobSchema);