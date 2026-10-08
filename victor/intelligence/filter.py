"""Cheap pre-AI filter. Returns a reason string when an item should be dropped, else None."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..collect.fetch import RawItem


def filter_reason(item: RawItem, cfg: dict[str, Any], now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    if len(item.title) < int(cfg.get("min_title_chars", 20)):
        return "title_too_short"
    max_age = timedelta(hours=float(cfg.get("max_age_hours", 36)))
    if item.published_at and now - item.published_at > max_age:
        return "too_old"
    if item.published_at and item.published_at - now > timedelta(hours=6):
        return "future_dated"
    blob = f"{item.title} {item.summary}".lower()
    for kw in cfg.get("drop_keywords", []):
        if kw.lower() in blob:
            return f"drop_keyword:{kw}"
    req = cfg.get("require_any_keywords") or []
    if req and not any(k.lower() in blob for k in req):
        return "no_required_keyword"
    if not looks_english(item.title):
        return "language"
    return None


_COMMON = {"the", "and", "to", "of", "in", "a", "for", "on", "with", "is", "new", "ai", "at", "by", "from", "its"}


def looks_english(text: str) -> bool:
    """Tiny heuristic: enough ASCII letters and at least one common English word in longer titles."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_ratio = sum(1 for c in letters if c.isascii()) / len(letters)
    if ascii_ratio < 0.85:
        return False
    words = [w.strip(".,:;!?'\"()").lower() for w in text.split()]
    if len(words) >= 6 and not any(w in _COMMON for w in words):
        return False
    return True
