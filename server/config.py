import os
from dotenv import load_dotenv

load_dotenv()

# Environment variables
os.environ["OMP_NUM_THREADS"] = "1"

# Device setup
DEVICE = "cpu"

# Qdrant configuration
QDRANT_URL = os.getenv("QDRANT_URL")

# MongoDB configuration
MONGODB_URI = os.getenv("MONGODB_URI")

# SQLite storage directory (for structured data)
SQLITE_DIR = os.getenv("SQLITE_DIR")

# =============================================================================
# LLM CONFIGURATION
# Change LLM_PROVIDER to switch between providers globally
# =============================================================================

# LLM Provider: "ollama" or "gemini"
# Set via environment variable or change default here
LLM_PROVIDER = os.getenv("LLM_PROVIDER")

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

# Gemini Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# LLM Generation Settings (used across all providers)
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS"))