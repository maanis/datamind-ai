"""
LLM abstraction layer for multi-tenant RAG platform.
Supports Ollama and Gemini providers.
"""

from llm.factory import get_llm
from llm.base import BaseLLM

__all__ = ["get_llm", "BaseLLM"]
