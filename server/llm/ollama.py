"""
llm/ollama.py

Ollama LLM provider.

IMPORTANT: Uses /api/chat endpoint (NOT /api/generate).
qwen2.5:7b and other tool-capable models require /api/chat.
/api/generate is the legacy endpoint — does NOT support tool calling.

Env vars:
  OLLAMA_URL   = http://localhost:11434   (base URL, no path)
  OLLAMA_MODEL = qwen2.5:7b
"""

import json
import requests
from typing import Generator, List, Optional

from llm.base import BaseLLM
from config import OLLAMA_URL, OLLAMA_MODEL, LLM_TEMPERATURE, LLM_MAX_TOKENS


def _normalize_ollama_base_url(url: str) -> str:
    """Strip trailing slash and any /api/* path — we add the path ourselves."""
    url = url.rstrip("/")
    # Remove /api/generate or /api/chat if accidentally included
    for suffix in ["/api/generate", "/api/chat", "/api"]:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


class OllamaLLM(BaseLLM):
    """
    Ollama LLM via /api/chat endpoint.
    Compatible with qwen2.5:7b, llama3, mistral, and all chat-format models.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
    ):
        super().__init__(
            model_name=model_name or OLLAMA_MODEL,
            temperature=temperature if temperature is not None else LLM_TEMPERATURE,
            max_tokens=max_tokens if max_tokens is not None else LLM_MAX_TOKENS,
        )
        base = _normalize_ollama_base_url(OLLAMA_URL or "https://fibrinous-bathless-julianna.ngrok-free.dev")
        self.chat_url = f"{base}/api/chat"
        self.timeout = timeout
        # Helpful when Ollama is exposed via ngrok/browser gateways.
        self.headers = {"ngrok-skip-browser-warning": "true"}

    # ------------------------------------------------------------------
    # GENERATE (blocking)
    # ------------------------------------------------------------------

    def generate(self, prompt: str, max_tokens: Optional[int] = None) -> str:
        """
        Send prompt to Ollama /api/chat and return the assistant reply.
        Uses single user message — no tool schema needed for plain generation.
        """
        payload = {
            "model": self.model_name,
            "stream": False,
            "prompt": prompt,
            "options": {
                "num_predict": max_tokens or self.max_tokens,
                "temperature": self.temperature,
            },
        }

        def _do_request():
            try:
                r = requests.post(
                    self.chat_url,
                    json=payload,
                    headers=self.headers,
                    timeout=self.timeout,
                )
            except requests.exceptions.ConnectionError as e:
                raise Exception(
                    f"Cannot connect to Ollama at {self.chat_url}. "
                    f"Is Ollama running? Start it with: ollama serve\n{e}"
                )
            except requests.exceptions.Timeout:
                raise Exception(
                    f"Ollama request timed out after {self.timeout}s. "
                    f"Model '{self.model_name}' may be too slow — try qwen2.5:0.5b for dev."
                )
            if r.status_code != 200:
                raise Exception(
                    f"Ollama /api/chat returned HTTP {r.status_code}: {r.text[:300]}"
                )
            return r.json()

        data = _do_request()
        # Some servers return done_reason=load first (model warm-up) with empty response.
        for _ in range(2):
            if data.get("done_reason") == "load":
                data = _do_request()
                continue
            break

        # Postman-style response on /api/chat: { "response": "..." }
        # Fallback to chat-style message.content when returned.
        msg = data.get("message", {})
        content = data.get("response", "") or msg.get("content", "")

        if not content:
            # Fallback: check if model used tool_calls (shouldn't happen in plain gen mode)
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                try:
                    args = tool_calls[0].get("function", {}).get("arguments", {})
                    if isinstance(args, dict):
                        content = args.get("answer", args.get("text", str(args)))
                    else:
                        content = str(args)
                except Exception:
                    pass

        return content.strip()

    # ------------------------------------------------------------------
    # STREAM
    # ------------------------------------------------------------------

    def stream(self, prompt: str) -> Generator[str, None, None]:
        """Stream response from Ollama /api/chat."""
        payload = {
            "model": self.model_name,
            "stream": True,
            "prompt": prompt,
            "options": {
                "num_predict": self.max_tokens,
                "temperature": self.temperature,
            },
        }

        # Retry streaming once if first attempt only triggers model load.
        for attempt in range(2):
            try:
                resp = requests.post(
                    self.chat_url,
                    json=payload,
                    headers=self.headers,
                    stream=True,
                    timeout=self.timeout,
                )
            except requests.exceptions.RequestException as e:
                yield f"Error connecting to Ollama: {e}"
                return

            if resp.status_code != 200:
                yield f"Error: Ollama returned HTTP {resp.status_code}"
                return

            got_content = False
            saw_load_only = False
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk_data = json.loads(line)
                    chunk = chunk_data.get("response", "") or chunk_data.get("message", {}).get("content", "")
                    if chunk:
                        got_content = True
                        yield chunk
                    if chunk_data.get("done_reason") == "load" and not got_content:
                        saw_load_only = True
                    if chunk_data.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue

            resp.close()
            if saw_load_only and not got_content and attempt == 0:
                continue
            return
