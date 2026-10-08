"""Group items into stories. Deterministic: URL, title similarity, shared entities, time window."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz

from ..collect.normalize import ENTITY_HINTS

BRANDS = {e.lower() for e in ENTITY_HINTS}
TITLE_SIM_THRESHOLD = 72  # token-set ratio 0-100
ENTITY_MIN_SHARED = 2
WINDOW = timedelta(hours=48)


@dataclass
class Candidate:
    """Minimal view of an existing story used for matching."""

    story_id: int
    title: str
    entities: list[str]
    last_updated_at: datetime


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def title_similarity(a: str, b: str) -> float:
    return fuzz.token_set_ratio(_norm(a), _norm(b))


def shared_entities(a: list[str], b: list[str]) -> set[str]:
    return {e.lower() for e in a} & {e.lower() for e in b}


def same_story(title: str, entities: list[str], when: datetime | None, cand: Candidate,
               now: datetime | None = None) -> tuple[bool, str]:
    """Return (match, reason). Conservative: needs strong title match or entity overlap plus moderate title."""
    now = now or datetime.now(timezone.utc)
    ref = when or now
    if abs(ref - cand.last_updated_at) > WINDOW:
        return False, "outside_window"
    sim = title_similarity(title, cand.title)
    shared = shared_entities(entities, cand.entities)
    # Two stories naming different known companies are different stories, however alike the headlines read.
    brands_a = {e.lower() for e in entities} & BRANDS
    brands_b = {e.lower() for e in cand.entities} & BRANDS
    if brands_a and brands_b and not (brands_a & brands_b):
        return False, f"different_brands={sorted(brands_a)}|{sorted(brands_b)}"
    if sim >= 88:
        return True, f"title_sim={sim:.0f}"
    if sim >= TITLE_SIM_THRESHOLD and len(shared) >= 1:
        return True, f"title_sim={sim:.0f}+entities={sorted(shared)}"
    if len(shared) >= ENTITY_MIN_SHARED and sim >= 55:
        return True, f"entities={sorted(shared)}+title_sim={sim:.0f}"
    return False, f"title_sim={sim:.0f},shared={len(shared)}"


def best_match(title: str, entities: list[str], when: datetime | None, candidates: list[Candidate]) -> Candidate | None:
    best: tuple[float, Candidate] | None = None
    for c in candidates:
        ok, _ = same_story(title, entities, when, c)
        if not ok:
            continue
        s = title_similarity(title, c.title) + 5 * len(shared_entities(entities, c.entities))
        if best is None or s > best[0]:
            best = (s, c)
    return best[1] if best else None
