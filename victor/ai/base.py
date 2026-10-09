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

# Characters models love and X renders as odd gaps or breaks: zero-width marks, exotic spaces and hyphens.
_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿­]")
_SPACES = re.compile(r"[           　]")
_HYPHENS = re.compile(r"[‑‐‒⁃]")
_DASHES = re.compile(r"\s*[–—]\s*")


def clean_post_text(text: str) -> str:
    """Plain characters only, one space between words, at most one blank line between paragraphs, no trailing spaces."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _ZERO_WIDTH.sub("", t)
    t = _SPACES.sub(" ", t)
    t = _HYPHENS.sub("-", t)
    t = _DASHES.sub(", ", t)  # the house style has no em dashes
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "" and (not out or out[-1] == ""):
            continue  # collapse runs of blank lines
        out.append(ln)
    return "\n".join(out).strip()


def parse_draft_json(raw: str, provider: str, model: str) -> DraftOutput:
    """Providers are asked for JSON; tolerate prose around it, fail loudly on garbage."""
    m = _JSON_BLOCK.search(raw or "")
    if not m:
        raise ProviderError("no JSON object in model output")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ProviderError(f"bad JSON from model: {e}") from e
    text = clean_post_text(str(data.get("post", "")))
    if not text:
        raise ProviderError("model returned empty post")
    return DraftOutput(
        text=text,
        why=clean_post_text(str(data.get("why_it_matters", ""))),
        reason=str(data.get("reason", "")).strip(),
        provider=provider,
        model=model,
        raw=raw,
    )
