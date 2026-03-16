const axios = require('axios');
const FormData = require('form-data');
const mongoose = require('mongoose');

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

async function ingest(req, res) {
    try {
        const { text, input_type = 'text', workspaceId } = req.body;
        if (!text) {
            return res.status(400).json({ message: 'Text is required' });
        }
        if (!workspaceId) {
            return res.status(400).json({ message: 'Workspace ID is required' });
        }

        const response = await axios.post(`${PYTHON_API_BASE}/ingest`, {
            text,
            api_key: workspaceId,
            input_type
        });

        res.json(response.data);
    } catch (error) {
        console.error('Ingest error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
}

async function ingestFile(req, res) {
    try {
        if (!req.file) {
            return res.status(400).json({ message: 'File is required' });
        }

        const { workspaceId } = req.body;
        if (!workspaceId) {
            return res.status(400).json({ message: 'Workspace ID is required' });
        }

        const file = req.file;

        // Determine input_type based on file extension
        const ext = file.originalname.split('.').pop().toLowerCase();
        let input_type;
        if (ext === 'csv') {
            input_type = 'csv';
        } else if (ext === 'xlsx') {
            input_type = 'excel';
        } else {
            return res.status(400).json({ message: 'Unsupported file type. Only .csv and .xlsx are allowed.' });
        }

        // Create form data to send to Python API
        const formData = new FormData();
        formData.append('file', file.buffer, {
            filename: file.originalname,
            contentType: file.mimetype
        });
        formData.append('api_key', workspaceId);
        formData.append('input_type', input_type);

        const response = await axios.post(`${PYTHON_API_BASE}/ingest-file`, formData, {
            headers: {
                ...formData.getHeaders()
            }
        });

        res.json(response.data);
    } catch (error) {
        console.error('Ingest file error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
}

// Multi-tenant ingest function supporting files, raw text, and raw JSON
async function ingestDocument(req, res) {
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
}

// async function getAnswer(req, res) {
//     try {
//         const { question } = req.body;
//         if (!question) {
//             return res.status(400).json({ message: 'Question is required' });
//         }

//         const apiKey = req.params.api_key;

//         const response = await axios.post(`${PYTHON_API_BASE}/get-answer`, {
//             question,
//             api_key: apiKey
//         });

//         res.json(response.data);
//     } catch (error) {
//         console.error('Get answer error:', error.response?.data || error.message);
//         res.status(error.response?.status || 500).json({
//             message: 'Internal server error',
//             error: error.response?.data || error.message
//         });
//     }
// }

async function getAnswer(req, res) {
    try {
        const { question } = req.body;
        if (!question) {
            return res.status(400).json({ message: 'Question is required' });
        }

        const apiKey = req.params.api_key;

        const response = await axios.post(
            `${PYTHON_API_BASE}/get-answer`,
            {
                question,
                api_key: apiKey
            },
            {
                responseType: 'stream'
            }
        );

        res.setHeader('Content-Type', 'text/plain');

        response.data.on('data', (chunk) => {
            const text = chunk.toString();
            process.stdout.write(text);  // live terminal log
            res.write(text);             // forward to frontend
        });

        response.data.on('end', () => {
            console.log('\n--- Stream finished ---');
            res.end();
        });

        response.data.on('error', (err) => {
            console.error('Stream error:', err);
            res.end();
        });

    } catch (error) {
        console.error('Get answer error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
}

// Multi-tenant answer endpoint with workspace isolation
async function getAnswerMultiTenant(req, res) {
    try {
        const { question, workspaceId, documentId } = req.body;

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

        console.log('hey')

        const response = await axios.post(
            `${PYTHON_API_BASE}/get-answer-v2`,
            {
                question,
                workspace_id: workspaceId,
                document_id: documentId || null
            },
            {
                responseType: 'stream'
            }
        );

        console.log(response)

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

    } catch (error) {
        console.error('Get answer multi-tenant error:', error.message);
        const { statusCode, errorMessage, errorDetails } = extractErrorInfo(error);
        res.status(statusCode).json({
            message: errorMessage,
            error: errorDetails
        });
    }
}

// Delete document and its vectors
async function deleteDocument(req, res) {
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
}


module.exports = {
    ingest,
    ingestFile,
    getAnswer,
    getAnswerMultiTenant

};