"""Copy-editing before a post reaches the phone: a deterministic tidy, then one careful pass by the model."""
from __future__ import annotations

import json
import logging
import re

from .base import AIProvider, clean_post_text

log = logging.getLogger(__name__)

PROOFREAD_SYSTEM = (
    "You are a careful copy editor for posts on X. Fix spelling, grammar, capitalisation and punctuation ONLY. "
    "Keep every fact, number, name, word choice, line break and the overall length exactly as they are. "
    "Do not add, remove or reorder sentences. Do not change the tone. Do not add emoji, hashtags or URLs. "
    "If the text is already correct, return it unchanged. "
    'Respond with JSON only: {"post": string}'
)

_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")
_MISSING_SPACE_AFTER = re.compile(r"([,;:!?])(?=[A-Za-z])")
_PERIOD_NO_SPACE = re.compile(r"([a-z]{3,})\.(?=[A-Za-z]{2,})")  # "done.next" / "done.Next", never "e.g.", "3.5" or "U.S."
_DOUBLE_PUNCT = re.compile(r"([,;:])\1+")
_TRIPLE_DOTS = re.compile(r"\.{4,}")
_SPACED_QUOTES = re.compile(r'"\s+([^"]*?)\s+"')


def tidy_punctuation(text: str) -> str:
    """Mechanical fixes that never change meaning: spacing around punctuation, doubled commas, stray spaces in quotes."""
    t = clean_post_text(text)
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    t = _MISSING_SPACE_AFTER.sub(r"\1 ", t)
    t = _PERIOD_NO_SPACE.sub(lambda m: m.group(1) + ". ", t)
    # A new sentence starts with a capital: only after a real word plus full stop, never after "e.g." or "U.S.".
    t = re.sub(r"(?<=[a-z]{3})([.!?]) ([a-z])(?=[a-z])", lambda m: m.group(1) + " " + m.group(2).upper(), t)
    t = _DOUBLE_PUNCT.sub(r"\1", t)
    t = _TRIPLE_DOTS.sub("...", t)
    t = _SPACED_QUOTES.sub(r'"\1"', t)
    lines: list[str] = []
    prev = ""  # the previous non-empty line
    for ln in t.split("\n"):
        if prev.endswith((".", "!", "?")) and ln[:1].islower() and not ln.startswith(_LOWER_BRANDS):
            ln = ln[0].upper() + ln[1:]
        lines.append(ln)
        if ln.strip():
            prev = ln.rstrip()
    return "\n".join(lines).strip()


_LOWER_BRANDS = ("iPhone", "iOS", "iPad", "macOS", "eBay", "xAI", "arXiv", "iMessage", "watchOS", "tvOS")


def proofread(provider: AIProvider, text: str) -> str:
    """One model pass for grammar and punctuation. Any failure, or any rewrite that strays, returns the tidied original."""
    base = tidy_punctuation(text)
    if not base:
        return base
    try:
        raw = provider.complete(PROOFREAD_SYSTEM, "PROOFREAD\n" + base, max_tokens=900)
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return base
        fixed = clean_post_text(str(json.loads(m.group(0)).get("post", "")))
    except Exception as e:  # noqa: BLE001  proofreading must never cost us the post
        log.info("proofread skipped: %s", e)
        return base
    if not fixed:
        return base
    # Guard rails: the editor may only touch small things. Length and the set of digits must survive.
    if abs(len(fixed) - len(base)) > max(12, len(base) // 10):
        return base
    if re.findall(r"\d+", fixed) != re.findall(r"\d+", base):
        return base
    if fixed.count("\n") != base.count("\n"):
        return base
    return fixed
