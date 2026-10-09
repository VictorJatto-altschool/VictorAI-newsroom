"""Score stories from counts. No model involved. Every factor is explainable."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class StoryFacts:
    source_count: int  # distinct publishers
    tier1_count: int
    first_seen_at: datetime
    last_updated_at: datetime
    category: str
    novelty: float  # 0-1, 1 = nothing similar posted recently
    max_tier: int = 4  # best (lowest) tier present


def velocity(first_seen_at: datetime, source_count: int, now: datetime | None = None) -> float:
    """Additional sources per hour since first seen, capped. 0-1. A lone source has no velocity."""
    now = now or datetime.now(timezone.utc)
    hours = max((now - first_seen_at).total_seconds() / 3600.0, 0.25)
    per_hour = max(source_count - 1, 0) / hours
    return min(per_hour / 4.0, 1.0)  # 4 extra sources/hour = max


def score_story(f: StoryFacts, cfg: dict[str, Any], now: datetime | None = None) -> tuple[float, dict[str, float], str]:
    w = cfg["weights"]
    cat_w = cfg.get("category_weights", {})
    parts = {
        "source_count": w["source_count"] * min(f.source_count / 6.0, 1.0),
        "velocity": w["velocity"] * velocity(f.first_seen_at, f.source_count, now),
        "tier1_present": w["tier1_present"] * _authority(f),
        "novelty": w["novelty"] * max(0.0, min(f.novelty, 1.0)),
        "category_weight": w["category_weight"] * float(cat_w.get(f.category, cat_w.get("other", 0.3))),
    }
    # Single tier-4 source alone can never look important.
    if f.source_count <= 1 and f.tier1_count == 0:
        parts = {k: v * 0.5 for k, v in parts.items()}
    total = round(sum(parts.values()), 1)
    label = classify(total, cfg["thresholds"])
    # One source is never "trending" on its own, unless it is an official announcement that is still fresh.
    now = now or datetime.now(timezone.utc)
    fresh_hours = float(cfg.get("official_fresh_hours", 24))
    fresh_official = f.tier1_count > 0 and (now - f.first_seen_at) <= timedelta(hours=fresh_hours)
    if f.source_count < 2 and not fresh_official and label in ("breaking", "hot", "trending"):
        label = "developing"
    return total, {k: round(v, 1) for k, v in parts.items()}, label


def _authority(f: StoryFacts) -> float:
    """How much to trust the sourcing. An official source is best, but seven independent outlets are not nothing."""
    if f.tier1_count > 0:
        return 1.0
    if f.max_tier <= 2:
        return 0.6
    if f.max_tier == 3:
        return 0.45 if f.source_count >= 2 else 0.3
    return 0.35 if f.source_count >= 3 else 0.0  # community/aggregator only: needs several outlets to count


def classify(score: float, thresholds: dict[str, Any]) -> str:
    if score >= thresholds["breaking"]:
        return "breaking"
    if score >= thresholds["hot"]:
        return "hot"
    if score >= thresholds["trending"]:
        return "trending"
    if score >= thresholds["developing"]:
        return "developing"
    return "ignore"


def novelty_from_recent(entities: list[str], recent_entity_lists: list[list[str]], days_ago: list[float],
                        cooldown_days: float) -> float:
    """1.0 if no recent post is about the same thing. A post counts as 'the same thing' only when it shares most
    of this story's entities, not just one broad name like 'Microsoft' or 'Trump'. Never drops below 0.4."""
    ents = {e.lower() for e in entities}
    if not ents:
        return 1.0
    best = 1.0
    for rec, age in zip(recent_entity_lists, days_ago):
        shared = ents & {e.lower() for e in rec}
        if len(shared) >= 2 and len(shared) / len(ents) >= 0.5:
            best = min(best, max(0.4, min(age / cooldown_days, 1.0)))
    return best


def stale(last_updated_at: datetime, hours: float = 36, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now - last_updated_at > timedelta(hours=hours)
