"""Any OpenAI-compatible chat endpoint: Groq, GitHub Models, OpenRouter, Mistral, Cerebras, Cloudflare, Ollama.

One class, configured by base URL + key + model. This is how the newsroom stays free whichever provider
is giving away inference this month.
"""
from __future__ import annotations

import httpx

from .base import ProviderError

PRESETS = {
    # name: (base_url, default model). Keys come from the environment.
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "github": ("https://models.github.ai/inference", "openai/gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
}


# Free providers rotate their catalogues often. When the configured model vanishes, pick the best one still listed.
MODEL_PREFERENCE = ["gpt-oss-120b", "llama-4", "llama-3.3-70b", "llama-3.1-70b", "qwen3", "gpt-oss-20b", "mixtral", "llama", "gpt-4o-mini", "gpt-4.1"]
_EXCLUDE = ("whisper", "guard", "orpheus", "tts", "embed", "safeguard", "allam", "vision")


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.name = name
        self._base = base_url.rstrip("/")
        self._key = api_key
        self.model = model
        self._timeout = timeout
        self._resolved = False

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        if self.name == "openrouter":
            h["HTTP-Referer"] = "https://x.com/SI4blog_"
            h["X-Title"] = "Victor AI & Tech newsroom"
        return h

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self._base}/models", headers=self._headers(), timeout=self._timeout)
            if r.status_code != 200:
                return []
            return [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        except (httpx.HTTPError, ValueError):
            return []

    def pick_fallback_model(self) -> str | None:
        ids = [m for m in self.list_models() if not any(x in m.lower() for x in _EXCLUDE)]
        for pref in MODEL_PREFERENCE:
            hit = next((m for m in ids if pref in m.lower()), None)
            if hit:
                return hit
        return ids[0] if ids else None

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        try:
            return self._complete(system, user, max_tokens)
        except ProviderError as e:
            if ("model" in str(e).lower() and ("not exist" in str(e) or "not found" in str(e) or "decommissioned" in str(e))
                    and not self._resolved):
                self._resolved = True
                alt = self.pick_fallback_model()
                if alt and alt != self.model:
                    self.model = alt
                    return self._complete(system, user, max_tokens)
            raise

    def _complete(self, system: str, user: str, max_tokens: int = 600, json_mode: bool = True) -> str:
        reasoning = "gpt-oss" in self.model.lower() or "qwen3" in self.model.lower() or "deepseek-r1" in self.model.lower()
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": max(max_tokens, 1500) if reasoning else max_tokens,  # reasoning tokens count too
        }
        if reasoning and self.name == "groq":
            body["reasoning_effort"] = "low"
        if json_mode and self.name not in ("ollama",):
            body["response_format"] = {"type": "json_object"}
        try:
            r = httpx.post(f"{self._base}/chat/completions", headers=self._headers(), json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} network error: {e}") from e
        if r.status_code == 400 and json_mode and "json" in r.text.lower():
            # Some models cannot do strict JSON mode; our parser tolerates prose around the object.
            return self._complete(system, user, max_tokens, json_mode=False)
        if r.status_code != 200:
            raise ProviderError(f"{self.name} http {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as e:  # ValueError covers a non-JSON 200 body
            raise ProviderError(f"{self.name} unexpected response: {type(e).__name__}: {r.text[:120]!r}") from e
