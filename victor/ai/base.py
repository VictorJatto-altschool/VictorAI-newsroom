"""AI provider interface. Providers only turn a prompt into JSON text; all rules live elsewhere."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class DraftOutput:
    text: str
    why: str
    reason: str
    provider: str
    model: str
    raw: str = ""


class AIProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str: ...


class ProviderError(RuntimeError):
    pass


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_draft_json(raw: str, provider: str, model: str) -> DraftOutput:
    """Providers are asked for JSON; tolerate prose around it, fail loudly on garbage."""
    m = _JSON_BLOCK.search(raw or "")
    if not m:
        raise ProviderError("no JSON object in model output")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ProviderError(f"bad JSON from model: {e}") from e
    text = str(data.get("post", "")).strip()
    if not text:
        raise ProviderError("model returned empty post")
    return DraftOutput(
        text=text,
        why=str(data.get("why_it_matters", "")).strip(),
        reason=str(data.get("reason", "")).strip(),
        provider=provider,
        model=model,
        raw=raw,
    )
