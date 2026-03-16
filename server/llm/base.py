"""
Base LLM abstraction class.
All LLM providers must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Generator, Optional


class BaseLLM(ABC):
    """Abstract base class for LLM providers."""
    
    def __init__(self, model_name: str, temperature: float = 0.2, max_tokens: int = 1000):
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
    
    @abstractmethod
    def generate(self, prompt: str, max_tokens: Optional[int] = None) -> str:
        """
        Generate a response from the LLM.
        
        Args:
            prompt: The input prompt
            max_tokens: Override default max tokens
            
        Returns:
            Generated text response
        """
        pass
    
    @abstractmethod
    def stream(self, prompt: str) -> Generator[str, None, None]:
        """
        Stream response from the LLM.
        
        Args:
            prompt: The input prompt
            
        Yields:
            Text chunks as they are generated
        """
        pass
    
    def generate_json(self, prompt: str) -> dict:
        """
        Generate and parse JSON response.
        Adds JSON formatting instruction to prompt.
        
        Args:
            prompt: The input prompt
            
        Returns:
            Parsed JSON dictionary
        """
        import json
        
        json_prompt = f"{prompt}\n\nRespond with valid JSON only. No markdown, no explanation."
        response = self.generate(json_prompt, max_tokens=1000)
        
        # Clean response - extract JSON if wrapped
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to find JSON object in response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(response[start:end])
            raise ValueError(f"Failed to parse JSON from response: {response[:200]}")
