"""
Gemini LLM provider implementation.
Uses Google's Gemini API for inference.
"""

import json
import time
import requests
from typing import Generator, Optional

from llm.base import BaseLLM
from config import GEMINI_URL, GEMINI_API_KEY, GEMINI_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS


class GeminiLLM(BaseLLM):
    """Gemini LLM provider for cloud inference."""
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 60,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ):
        super().__init__(
            model_name=model_name or GEMINI_MODEL,
            temperature=temperature if temperature is not None else LLM_TEMPERATURE,
            max_tokens=max_tokens if max_tokens is not None else LLM_MAX_TOKENS
        )
        self.api_url = GEMINI_URL
        self.api_key = GEMINI_API_KEY
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    def generate(self, prompt: str, max_tokens: Optional[int] = None) -> str:
        """
        Generate a response from Gemini with retry logic for transient errors.
        
        Args:
            prompt: The input prompt
            max_tokens: Override default max tokens
            
        Returns:
            Generated text response
        """
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": max_tokens or self.max_tokens
            }
        }
        
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout
                )
                
                # Retry on 503 (overloaded) or 429 (rate limit)
                if response.status_code in (503, 429):
                    wait_time = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    print(f"DEBUG [gemini]: API returned {response.status_code}, retrying in {wait_time}s (attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(wait_time)
                    last_error = f"Gemini API error: {response.status_code} - {response.text}"
                    continue
                
                if response.status_code != 200:
                    raise Exception(f"Gemini API error: {response.status_code} - {response.text}")
                
                data = response.json()
                
                # Extract text from Gemini response structure
                candidates = data.get("candidates", [])
                if candidates:
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
                
                return ""
                
            except requests.exceptions.RequestException as e:
                wait_time = self.retry_delay * (2 ** attempt)
                print(f"DEBUG [gemini]: Connection error, retrying in {wait_time}s (attempt {attempt + 1}/{self.max_retries}): {e}")
                time.sleep(wait_time)
                last_error = f"Failed to connect to Gemini: {str(e)}"
                continue
        
        # All retries exhausted
        raise Exception(last_error or "Gemini API failed after all retries")
    
    def stream(self, prompt: str) -> Generator[str, None, None]:
        """
        Stream response from Gemini.
        Note: Gemini streaming uses Server-Sent Events.
        
        Args:
            prompt: The input prompt
            
        Yields:
            Text chunks as they are generated
        """
        # Construct streaming URL
        stream_url = self.api_url.replace(":generateContent", ":streamGenerateContent")
        
        try:
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": self.temperature,
                    "maxOutputTokens": self.max_tokens
                }
            }
            
            response = requests.post(
                stream_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                stream=True,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                yield f"Error: Gemini returned status {response.status_code}"
                return
            
            # Gemini streams JSON objects
            buffer = ""
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    buffer += chunk
                    # Try to parse complete JSON objects
                    try:
                        # Handle array of responses
                        if buffer.startswith("["):
                            buffer = buffer[1:]
                        if buffer.startswith(","):
                            buffer = buffer[1:]
                        
                        # Find complete JSON object
                        brace_count = 0
                        obj_start = buffer.find("{")
                        if obj_start == -1:
                            continue
                            
                        for i, char in enumerate(buffer[obj_start:], obj_start):
                            if char == "{":
                                brace_count += 1
                            elif char == "}":
                                brace_count -= 1
                                if brace_count == 0:
                                    obj_str = buffer[obj_start:i+1]
                                    buffer = buffer[i+1:]
                                    
                                    data = json.loads(obj_str)
                                    candidates = data.get("candidates", [])
                                    if candidates:
                                        content = candidates[0].get("content", {})
                                        parts = content.get("parts", [])
                                        if parts:
                                            text = parts[0].get("text", "")
                                            if text:
                                                yield text
                                    break
                    except json.JSONDecodeError:
                        continue
                        
        except requests.exceptions.RequestException as e:
            yield f"Error connecting to Gemini: {str(e)}"
