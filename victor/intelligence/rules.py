"""Editorial rules enforced in code: overnight rule, posting limits, routing. The model never decides these."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class PostingHistory:
    """What has already gone out, needed to enforce limits."""

    posted_at: list[datetime] = field(default_factory=list)  # all posts, UTC
    autonomous_in_window: int = 0
    story_ids_recent: set[int] = field(default_factory=set)
    entities_recent: set[str] = field(default_factory=set)


@dataclass
class Decision:
    allowed: bool
    route: str  # autonomous | review | blocked
    reasons: list[str]


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(local_now: datetime, start: str, end: str) -> bool:
    s, e = _parse_hhmm(start), _parse_hhmm(end)
    t = local_now.timetz().replace(tzinfo=None)
    if s <= e:
        return s <= t < e
    return t >= s or t < e  # crosses midnight


def window_opened_at(local_now: datetime, start: str) -> datetime:
    s = _parse_hhmm(start)
    opened = local_now.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
    if opened > local_now:
        opened -= timedelta(days=1)
    return opened


def limits_ok(now_utc: datetime, limits: dict[str, Any], hist: PostingHistory) -> list[str]:
    problems = []
    day_ago, hour_ago = now_utc - timedelta(days=1), now_utc - timedelta(hours=1)
    if sum(1 for t in hist.posted_at if t > day_ago) >= int(limits["max_posts_per_day"]):
        problems.append("daily_limit")
    if sum(1 for t in hist.posted_at if t > hour_ago) >= int(limits["max_posts_per_hour"]):
        problems.append("hourly_limit")
    if hist.posted_at:
        gap = (now_utc - max(hist.posted_at)).total_seconds() / 60
        if gap < float(limits["min_gap_minutes"]):
            problems.append("min_gap")
    return problems


def next_slot(now_utc: datetime, limits: dict[str, Any], hist: PostingHistory) -> datetime:
    """Earliest moment the limits allow another post. `now_utc` itself when they allow one now."""
    posted = sorted(hist.posted_at)
    candidates = [now_utc]
    if posted:
        candidates.append(posted[-1] + timedelta(minutes=float(limits["min_gap_minutes"])))
        per_hour, per_day = int(limits["max_posts_per_hour"]), int(limits["max_posts_per_day"])
        last_hour = [t for t in posted if t > now_utc - timedelta(hours=1)]
        if len(last_hour) >= per_hour:
            candidates.append(last_hour[-per_hour] + timedelta(hours=1))
        last_day = [t for t in posted if t > now_utc - timedelta(days=1)]
        if len(last_day) >= per_day:
            candidates.append(last_day[-per_day] + timedelta(days=1))
    return max(candidates)


def contains_blocked(text: str, blocked: list[str]) -> str | None:
    low = text.lower()
    for kw in blocked:
        if kw.lower() in low:
            return kw
    return None


def decide(
    *,
    now_utc: datetime,
    settings: dict[str, Any],
    story_category: str,
    story_text: str,
    story_entities: list[str],
    story_id: int,
    tier1_count: int,
    other_count: int,
    checks_passed: bool,
    hist: PostingHistory,
    paused: bool,
    mode: str,
    night_cap: int | None = None,
) -> Decision:
    """Route a passing draft. 'autonomous' only when every overnight condition holds."""
    reasons: list[str] = []
    auto = settings["automation"]
    ov = settings["overnight_rule"]
    limits = settings["limits"]

    if paused:
        return Decision(False, "blocked", ["paused"])
    if story_id in hist.story_ids_recent:
        return Decision(False, "blocked", ["story_cooldown"])  # the same story again: a true duplicate
    # Everything below is a tag for the human to see, never a block. Only the automatic path treats
    # a failed check or a recently covered company as a reason not to post.
    if {e.lower() for e in story_entities} & hist.entities_recent:
        reasons.append("entity_cooldown")
    if not checks_passed:
        reasons.append("checks_failed")

    if mode == "manual":
        return Decision(True, "review", reasons + ["mode_manual"])
    if mode != "restricted_autonomous":
        return Decision(True, "review", reasons + ["mode_approval"])
    if reasons:
        return Decision(True, "review", reasons)

    tz = ZoneInfo(auto.get("timezone", "UTC"))
    local = now_utc.astimezone(tz)
    if not in_window(local, ov["window_start"], ov["window_end"]):
        return Decision(True, "review", ["outside_window"])
    cap = int(ov["night_cap"]) if night_cap is None else int(night_cap)
    if hist.autonomous_in_window >= cap:
        return Decision(True, "review", ["night_cap_reached"])
    if tier1_count < int(ov["min_tier1_sources"]):
        return Decision(True, "review", ["needs_tier1"])
    if other_count < int(ov["min_other_sources"]):
        return Decision(True, "review", ["needs_corroboration"])
    if story_category not in ov["allowed_categories"]:
        return Decision(True, "review", [f"category_not_allowed:{story_category}"])
    kw = contains_blocked(story_text, ov["blocked_keywords"])
    if kw:
        return Decision(True, "review", [f"sensitive:{kw}"])
    lim = limits_ok(now_utc, limits, hist)
    if lim:
        return Decision(True, "review", lim)
    return Decision(True, "autonomous", ["overnight_rule_met"])
