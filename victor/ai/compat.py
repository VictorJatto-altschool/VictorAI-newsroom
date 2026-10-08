"""Any OpenAI-compatible chat endpoint: Groq, GitHub Models, OpenRouter, Mistral, Cerebras, Cloudflare, Ollama.

One class, configured by base URL + key + model. This is how the newsroom stays free whichever provider
is giving away inference this month.
"""
from __future__ import annotations

import httpx

from .base import ProviderError

PRESETS = {
    # name: (base_url, default model). Keys come from the environment.
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "github": ("https://models.github.ai/inference", "openai/gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
}


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.name = name
        self._base = base_url.rstrip("/")
        self._key = api_key
        self.model = model
        self._timeout = timeout

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": max_tokens,
        }
        if self.name not in ("ollama",):
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://x.com/SI4blog_"
            headers["X-Title"] = "Victor AI & Tech newsroom"
        try:
            r = httpx.post(f"{self._base}/chat/completions", headers=headers, json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} network error: {e}") from e
        if r.status_code != 200:
            raise ProviderError(f"{self.name} http {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name} unexpected response shape: {e}") from e
