"""Groq (free tier). Thin alias over the OpenAI-compatible provider."""
from __future__ import annotations

from .compat import PRESETS, OpenAICompatProvider

DEFAULT_MODEL = PRESETS["groq"][1]


class GroqProvider(OpenAICompatProvider):
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 40.0):
        super().__init__("groq", PRESETS["groq"][0], api_key, model, timeout)
