"""Google Gemini via REST (free tier). No SDK dependency."""
from __future__ import annotations

import httpx

from .base import ProviderError

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 40.0):
        self._key = api_key
        self.model = model
        self._timeout = timeout

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"},
        }
        try:
            r = httpx.post(url, params={"key": self._key}, json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini network error: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"gemini http {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ProviderError(f"gemini unexpected response: {type(e).__name__}: {r.text[:120]!r}") from e
