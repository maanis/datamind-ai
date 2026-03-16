const express = require('express');
const router = express.Router();
const multer = require('multer');
const {
    createWorkspace,
    getWorkspaces,
    getDocuments,
    ingestDocument,
    deleteDocument,
    deleteWorkspace,
    deleteAllDocumentsFromWorkspace
} = require('../controllers/workspaceController');
const auth = require('../middlewares/auth');

// Multer configuration for multi-tenant ingest (supports more file types)
const multiTenantStorage = multer.memoryStorage();

const multiTenantFileFilter = (req, file, cb) => {
    const allowedExtensions = ['.csv', '.xlsx', '.xls', '.pdf', '.txt', '.json'];
    const allowedMimeTypes = [
        'text/csv',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/pdf',
        'text/plain',
        'application/json'
    ];

    const ext = '.' + file.originalname.split('.').pop().toLowerCase();

    if (allowedExtensions.includes(ext) || allowedMimeTypes.includes(file.mimetype)) {
        cb(null, true);
    } else {
        cb(new Error('Unsupported file type. Only .csv, .xlsx, .xls, .pdf, .txt, and .json files are allowed.'), false);
    }
};

const multiTenantUpload = multer({
    storage: multiTenantStorage,
    fileFilter: multiTenantFileFilter,
    limits: {
        files: 1,
        fileSize: 10 * 1024 * 1024 // 10MB limit
    }
});

// Workspace management routes
router.post('/', auth, createWorkspace);
router.get('/', auth, getWorkspaces);
router.get('/:workspaceId/documents', auth, getDocuments);
router.delete('/', auth, deleteWorkspace);

// Document management routes
router.post('/ingest', auth, multiTenantUpload.single('file'), ingestDocument);
router.delete('/document', auth, deleteDocument);
router.delete('/documents', auth, deleteAllDocumentsFromWorkspace);



module.exports = router;