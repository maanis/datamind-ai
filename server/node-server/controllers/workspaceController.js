const axios = require('axios');
const FormData = require('form-data');
const mongoose = require('mongoose');
const path = require('path');

// Import models
const Workspace = require('../models/Workspace');
const Document = require('../models/Document');
const IngestionJob = require('../models/IngestionJob');

const PYTHON_API_BASE = process.env.PYTHON_API_BASE || 'http://localhost:8000';

// Helper function to extract safe error information
function extractErrorInfo(error) {
    let statusCode = 500;
    let errorMessage = 'Internal server error';
    let errorDetails = error.message;

    if (error.response) {
        statusCode = error.response.status || 500;
        errorMessage = error.response.statusText || 'API Error';

        // Safely extract response data without circular references
        try {
            if (typeof error.response.data === 'string') {
                errorDetails = error.response.data;
            } else if (error.response.data && typeof error.response.data === 'object') {
                // Try to extract common error fields
                if (error.response.data.message) {
                    errorDetails = error.response.data.message;
                } else if (error.response.data.error) {
                    errorDetails = error.response.data.error;
                } else if (error.response.data.detail) {
                    errorDetails = error.response.data.detail;
                } else {
                    // If we can't safely extract, just use the status text
                    errorDetails = error.response.statusText || 'Unknown API error';
                }
            }
        } catch (e) {
            // If anything goes wrong with data extraction, use safe defaults
            errorDetails = error.response.statusText || 'API request failed';
        }
    }

    return { statusCode, errorMessage, errorDetails };
}

const createWorkspace = async (req, res) => {
    try {
        const { workspaceName } = req.body;

        if (!workspaceName) {
            return res.status(400).json({ message: 'Workspace name is required' });
        }

        // Check if user already has a workspace with this name
        const existingWorkspace = await Workspace.findOne({
            userId: req.user._id,
            name: workspaceName
        });

        if (existingWorkspace) {
            return res.status(400).json({ message: 'Workspace name already exists for this user' });
        }

        // Generate unique vector collection name
        const vectorCollection = `user_${req.user._id}_${workspaceName.replace(/\s+/g, '_').toLowerCase()}`;

        // Create new Workspace document (sqliteDbPath will be set after save to use workspace._id)
        const workspace = new Workspace({
            name: workspaceName,
            userId: req.user._id,
            vectorCollection: vectorCollection
        });

        await workspace.save();

        // Update sqliteDbPath with workspace._id (matches Python pattern: sqlite_data/<workspaceId>/)
        workspace.sqliteDbPath = path.join(__dirname, '../../sqlite_data', workspace._id.toString());
        await workspace.save();

        res.json({
            message: 'Workspace created',
            workspace: {
                _id: workspace._id,
                name: workspace.name,
                vectorCollection: workspace.vectorCollection,
                createdAt: workspace.createdAt
            }
        });
    } catch (error) {
        res.status(500).json({ message: 'Server error', error: error.message });
    }
};

const getWorkspaces = async (req, res) => {
    try {
        const workspaces = await Workspace.find({ userId: req.user._id })
            .select('name vectorCollection totalVectors totalDocuments createdAt')
            .sort({ createdAt: -1 });

        res.json({ workspaces });
    } catch (error) {
        res.status(500).json({ message: 'Server error', error: error.message });
    }
};

const getDocuments = async (req, res) => {
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

        const documents = await Document.find({ workspaceId })
            .select('fileName fileType ingestionStatus createdAt')
            .sort({ createdAt: -1 });

        res.json({ documents });
    } catch (error) {
        res.status(500).json({ message: 'Server error', error: error.message });
    }
};

// Multi-tenant ingest function supporting files, raw text, and raw JSON
const ingestDocument = async (req, res) => {
    try {
        const { workspaceId, rawText, rawJson } = req.body;

        // Validate workspace ID
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

        // Determine input type and validate
        let inputType = null;
        let fileName = null;
        let fileType = null;

        if (req.file) {
            // File upload
            inputType = 'file';
            fileName = req.file.originalname;
            const ext = fileName.split('.').pop().toLowerCase();

            switch (ext) {
                case 'csv':
                    fileType = 'csv';
                    break;
                case 'pdf':
                    fileType = 'pdf';
                    break;
                case 'json':
                    fileType = 'json';
                    break;
                case 'txt':
                    fileType = 'text';
                    break;
                case 'xlsx':
                case 'xls':
                    fileType = 'excel';
                    break;
                default:
                    return res.status(400).json({ message: 'Unsupported file type' });
            }
        } else if (rawText) {
            // Raw text input
            inputType = 'raw_text';
            fileName = 'raw_text_input.txt';
            fileType = 'text';
        } else if (rawJson) {
            // Raw JSON input
            inputType = 'raw_json';
            fileName = 'raw_json_input.json';
            fileType = 'json';
        } else {
            return res.status(400).json({
                message: 'No input provided. Provide file, rawText, or rawJson.'
            });
        }

        // Create Document record
        const document = new Document({
            workspaceId: workspaceId,
            fileName: fileName,
            fileType: fileType,
            ingestionStatus: 'processing'
        });

        await document.save();

        // Create IngestionJob record
        const ingestionJob = new IngestionJob({
            workspaceId: workspaceId,
            documentId: document._id,
            status: 'queued'
        });

        await ingestionJob.save();

        // Prepare data to send to Python API
        const formData = new FormData();

        if (inputType === 'file') {
            // Send file
            formData.append('file', req.file.buffer, {
                filename: req.file.originalname,
                contentType: req.file.mimetype
            });
        } else if (inputType === 'raw_text') {
            // Send raw text
            formData.append('raw_text', rawText);
        } else if (inputType === 'raw_json') {
            // Send raw JSON
            formData.append('raw_json', rawJson);
        }

        formData.append('workspaceId', workspaceId);
        formData.append('documentId', document._id.toString());

        // Fire and forget - don't wait for response
        axios.post(process.env.PYTHON_INGEST_URL || `${PYTHON_API_BASE}/ingest-document`, formData, {
            headers: {
                ...formData.getHeaders()
            },
            timeout: 60000 // 60 second timeout for large files
        }).catch(error => {
            console.error('Python ingestion API error:', error.message);
            // Note: We don't update the database here as this is async
        });

        // Immediately return response
        res.json({
            success: true,
            message: 'Ingestion started',
            documentId: document._id,
            jobId: ingestionJob._id,
            inputType: inputType
        });

    } catch (error) {
        console.error('Ingest document error:', error.message);
        res.status(500).json({
            message: 'Internal server error',
            error: error.message
        });
    }
};

// Multi-tenant answer endpoint with workspace isolation


// Delete document and its vectors
const deleteDocument = async (req, res) => {
    try {
        const { workspaceId, documentId } = req.body;

        if (!workspaceId || !mongoose.Types.ObjectId.isValid(workspaceId)) {
            return res.status(400).json({ message: 'Valid workspace ID is required' });
        }
        if (!documentId || !mongoose.Types.ObjectId.isValid(documentId)) {
            return res.status(400).json({ message: 'Valid document ID is required' });
        }

        // Verify workspace belongs to authenticated user
        const workspace = await Workspace.findOne({
            _id: workspaceId,
            userId: req.user._id
        });

        if (!workspace) {
            return res.status(404).json({ message: 'Workspace not found or access denied' });
        }

        // Verify document belongs to workspace
        const document = await Document.findOne({
            _id: documentId,
            workspaceId: workspaceId
        });

        if (!document) {
            return res.status(404).json({ message: 'Document not found' });
        }

        // Call Python API to delete vectors
        const response = await axios.post(`${PYTHON_API_BASE}/delete-document`, {
            workspace_id: workspaceId,
            document_id: documentId
        });

        // Delete document from MongoDB
        await Document.deleteOne({ _id: documentId });

        // Delete associated ingestion jobs
        await IngestionJob.deleteMany({ documentId: documentId });

        res.json({
            success: true,
            message: 'Document deleted successfully',
            pythonResponse: response.data
        });

    } catch (error) {
        console.error('Delete document error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
};

// Delete entire workspace and all its data
const deleteWorkspace = async (req, res) => {
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

        // Call Python API to delete entire collection
        const response = await axios.delete(`${PYTHON_API_BASE}/delete-workspace`, {
            data: {
                workspace_id: workspaceId
            }
        });

        // Delete all documents in the workspace
        await Document.deleteMany({ workspaceId: workspaceId });

        // Delete all ingestion jobs in the workspace
        await IngestionJob.deleteMany({ workspaceId: workspaceId });

        // Delete the workspace itself
        await Workspace.deleteOne({ _id: workspaceId });

        res.json({
            success: true,
            message: 'Workspace deleted successfully',
            pythonResponse: response.data
        });

    } catch (error) {
        console.error('Delete workspace error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
};

// Delete all documents from a workspace
const deleteAllDocumentsFromWorkspace = async (req, res) => {
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

        // Call Python API to delete all vectors in the collection
        const response = await axios.delete(`${PYTHON_API_BASE}/delete-all-documents`, {
            data: {
                workspace_id: workspaceId
            }
        });

        // Get all document IDs in the workspace
        const documents = await Document.find({ workspaceId: workspaceId }, '_id');

        // Delete all documents
        await Document.deleteMany({ workspaceId: workspaceId });

        // Delete all ingestion jobs for these documents
        const documentIds = documents.map(doc => doc._id);
        await IngestionJob.deleteMany({ documentId: { $in: documentIds } });

        // Update workspace stats
        await Workspace.findByIdAndUpdate(workspaceId, {
            totalVectors: 0,
            totalDocuments: 0
        });

        res.json({
            success: true,
            message: 'All documents deleted successfully',
            deletedCount: documents.length,
            pythonResponse: response.data
        });

    } catch (error) {
        console.error('Delete all documents error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
};

module.exports = {
    createWorkspace,
    getWorkspaces,
    getDocuments,
    ingestDocument,
    deleteDocument,
    deleteWorkspace,
    deleteAllDocumentsFromWorkspace
};