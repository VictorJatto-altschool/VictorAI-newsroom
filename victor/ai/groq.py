"""Groq (OpenAI-compatible chat completions, free tier)."""
from __future__ import annotations

import httpx

from .base import ProviderError

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 40.0):
        self._key = api_key
        self.model = model
        self._timeout = timeout

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json=body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"groq network error: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"groq http {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"groq unexpected response shape: {e}") from e
