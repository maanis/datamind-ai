const express = require('express');
const router = express.Router();
const jwt = require('jsonwebtoken');
const { register, login } = require('../controllers/authController');

// Token verification middleware
const verifyToken = (req, res, next) => {
    const authHeader = req.headers.authorization;
    const token = authHeader && authHeader.split(' ')[1]; // Bearer TOKEN

    if (!token) {
        return res.status(401).json({ message: 'Access token required' });
    }

    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET || 'your-secret-key');
        req.user = decoded;
        next();
    } catch (error) {
        return res.status(403).json({ message: 'Invalid or expired token' });
    }
};

router.post('/register', register);
router.post('/login', login);
router.post('/verify', verifyToken, (req, res) => {
    // If we reach here, token is valid
    res.json({
        valid: true,
        user: {
            userId: req.user.userId
        }
    });
});

module.exports = router;