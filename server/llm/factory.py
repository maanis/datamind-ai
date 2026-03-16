"""
LLM factory for creating provider instances.
"""

from typing import Optional
from config import LLM_PROVIDER
from llm.base import BaseLLM


def get_llm(
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1000
) -> BaseLLM:
    """
    Get an LLM instance based on provider.
    
    Args:
        provider: LLM provider name ('ollama' or 'gemini'). 
                  Defaults to LLM_PROVIDER env variable.
        model_name: Override default model name
        temperature: Generation temperature
        max_tokens: Maximum tokens to generate
        
    Returns:
        LLM instance
        
    Raises:
        ValueError: If provider is not supported
    """
    provider = (provider or LLM_PROVIDER).lower()
    
    if provider == "ollama":
        from llm.ollama import OllamaLLM
        return OllamaLLM(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens
        )
    elif provider == "gemini":
        from llm.gemini import GeminiLLM
        return GeminiLLM(
            model_name=model_name or "gemini-2.5-flash",
            temperature=temperature,
            max_tokens=max_tokens
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}. Use 'ollama' or 'gemini'.")
