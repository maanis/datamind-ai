const mongoose = require('mongoose');

const usageSchema = new mongoose.Schema({
    workspaceId: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Workspace',
        required: true,
        unique: true
    },
    totalTokensUsed: {
        type: Number,
        default: 0
    },
    totalQueries: {
        type: Number,
        default: 0
    },
    totalVectors: {
        type: Number,
        default: 0
    },
    lastUpdated: {
        type: Date,
        default: Date.now
    }
});

// Add indexes
usageSchema.index({ workspaceId: 1 }); // Already unique, but good to have

module.exports = mongoose.model('Usage', usageSchema);