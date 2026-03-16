const express = require('express');
const router = express.Router();
const { getProfile, updateProfile, getAllUsers } = require('../controllers/userController');
const auth = require('../middlewares/auth');

router.get('/profile', auth, getProfile);
router.put('/profile', auth, updateProfile);

router.get('/', auth, getAllUsers);

module.exports = router;