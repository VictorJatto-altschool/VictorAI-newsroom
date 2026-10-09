"""Google Gemini via REST (free tier). No SDK dependency.

Google retires model names often, so the default is the rolling alias and a 404 on the model falls back to
whatever flash model the key can use today.
"""
from __future__ import annotations

import logging

import httpx

from .base import ProviderError

log = logging.getLogger(__name__)
DEFAULT_MODEL = "gemini-flash-latest"  # rolling alias maintained by Google
_PREFER = ("gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-2.5-flash", "flash")
_AVOID = ("tts", "image", "transcribe", "omni", "lite", "preview", "research", "lyria", "banana")


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 40.0):
        self._key = api_key
        self.model = model
        self._timeout = timeout
        self._resolved = False

    def list_models(self) -> list[str]:
        try:
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                          params={"key": self._key, "pageSize": 100}, timeout=self._timeout)
            if r.status_code != 200:
                return []
            return [m["name"].split("/")[-1] for m in r.json().get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    def pick_fallback_model(self) -> str | None:
        ids = [m for m in self.list_models() if not any(a in m for a in _AVOID)]
        for pref in _PREFER:
            hit = next((m for m in ids if pref in m), None)
            if hit:
                return hit
        return ids[0] if ids else None

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        try:
            return self._complete(system, user, max_tokens)
        except ProviderError as e:
            msg = str(e)
            if "404" in msg and not self._resolved:
                self._resolved = True
                alt = self.pick_fallback_model()
                if alt and alt != self.model:
                    log.warning("gemini model %s unavailable; switching to %s", self.model, alt)
                    self.model = alt
                    return self._complete(system, user, max_tokens)
            raise

    def _complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": max(max_tokens, 2048),
                                 "responseMimeType": "application/json"},
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
