"""One newsroom cycle: collect -> filter -> cluster -> score -> draft -> route -> publish -> log."""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai.draft import ProviderError, SourceView, generate, pick_provider, run_checks
from .channels.base import DraftCard
from .channels.console import ConsoleChannel
from .channels.telegram import TelegramChannel
from .collect.fetch import FETCH_DEADLINE, FetchResult, fetch_source, make_client
from .collect.normalize import canonical_url, host_of, normalize_publisher
from .config import Settings, SourceConfig
from .db import session
from .intelligence.cluster import Candidate, best_match
from .intelligence.filter import filter_reason
from .intelligence.rules import PostingHistory, decide, in_window, limits_ok, window_opened_at
from .intelligence.score import StoryFacts, novelty_from_recent, score_story
from .ai.educational import generate_educational
from .media.extract import choose_media, find_media
from .media.fetch import prepare_media
from .models import Draft, Event, Item, Note, Post, Run, Source, State, Story
from .publish.base import PublishRequest
from .publish.mock import MockPublisher
from .publish.x import XPublisher

log = logging.getLogger(__name__)
ACTIVE_WINDOW = timedelta(hours=48)
DEFAULT_FETCH_WORKERS = 8      # sources fetched at the same time; a slow feed no longer holds up the rest
DEFAULT_DUE_GRACE_MINUTES = 5   # GitHub cron runs land 25-35 min apart; never skip a 30-min source over that jitter


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; everything we store is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def event(s: Session, kind: str, ref_type: str | None = None, ref_id: int | None = None, **detail: Any) -> None:
    s.add(Event(kind=kind, ref_type=ref_type, ref_id=ref_id, detail=detail))


def get_state(s: Session, key: str, default: str) -> str:
    row = s.get(State, key)
    return row.value if row else default


def set_state(s: Session, key: str, value: str) -> None:
    row = s.get(State, key)
    if row:
        row.value = value
    else:
        s.add(State(key=key, value=value))


# ----------------------------------------------------------------------------- sources


def sync_sources(s: Session, settings: Settings) -> dict[str, Source]:
    by_key: dict[str, Source] = {}
    for cfg in settings.sources:
        row = s.scalar(select(Source).where(Source.key == cfg.key))
        if row is None:
            row = Source(key=cfg.key, name=cfg.name, kind=cfg.kind, url=cfg.feed_url(), tier=cfg.tier,
                         category=cfg.category, enabled=cfg.enabled)
            s.add(row)
        else:
            row.name, row.kind, row.url = cfg.name, cfg.kind, cfg.feed_url()
            row.tier, row.category, row.enabled = cfg.tier, cfg.category, cfg.enabled
        by_key[cfg.key] = row
    s.flush()
    return by_key


def _due(row: Source, cfg: SourceConfig, now: datetime, grace: timedelta = timedelta(minutes=DEFAULT_DUE_GRACE_MINUTES)) -> bool:
    """A source is due once its interval has (nearly) elapsed since the last attempt.

    Scheduled runs are nominally 30 min apart but GitHub starts them minutes late, unevenly. Without a
    grace period a 30-min source attempted at 10:04 is not due at 10:31 and waits for the 11:00 run,
    doubling how long a story takes to reach the newsroom. Grace is capped at half the effective interval
    so backoff after failures still means something.
    """
    if not row.enabled:
        return False
    if row.blocked_until and _aware(row.blocked_until) > now:
        return False
    if row.last_attempt_at is None:
        return True
    backoff = min(2 ** min(row.consecutive_failures, 6), 48) if row.consecutive_failures else 1
    interval = timedelta(minutes=cfg.interval_minutes * backoff)
    return now - _aware(row.last_attempt_at) >= interval - min(grace, interval / 2)


def _collect_cfg(settings: Settings) -> dict[str, Any]:
    return settings.raw.get("collect") or {}


def fetch_all(due: list[tuple[SourceConfig, str | None, str | None]], fetcher=fetch_source,
              workers: int = DEFAULT_FETCH_WORKERS, deadline: float = FETCH_DEADLINE) -> list[FetchResult]:
    """Fetch every due source concurrently, each under a hard total deadline, in the order of `due`.

    Fetching 35+ feeds one after another could take minutes when a few sources stall; in parallel the
    whole watch list takes about as long as the slowest single feed, and that one is cut off at
    `deadline` seconds total (connect plus every read), not just per socket operation.
    One httpx.Client is shared: it is thread-safe and reuses connections. Nothing in here touches the DB.
    """
    if not due:
        return []
    workers = max(1, min(int(workers), len(due)))
    client = make_client()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch")
    started: dict[int, float] = {}  # index -> monotonic time the worker actually began; queued sources are not charged

    def run(i: int, cfg: SourceConfig, etag: str | None, lm: str | None) -> FetchResult:
        started[i] = time.monotonic()
        return fetcher(cfg, etag, lm, client=client)

    futures = [pool.submit(run, i, cfg, etag, lm) for i, (cfg, etag, lm) in enumerate(due)]
    results: list[FetchResult | None] = [None] * len(due)
    pending = set(range(len(due)))
    while pending:
        wait([futures[i] for i in pending], timeout=0.05, return_when=FIRST_COMPLETED)
        t = time.monotonic()
        for i in sorted(pending):
            fut = futures[i]
            if fut.done():
                try:
                    results[i] = fut.result()
                except Exception as e:  # noqa: BLE001  fetch_source already catches; this guards custom fetchers
                    results[i] = FetchResult(ok=False, items=[], error=f"{type(e).__name__}: {e}"[:300])
                pending.discard(i)
            elif i in started and t - started[i] >= deadline:
                log.warning("fetch timed out %s: over %.0fs budget", due[i][0].name, deadline)
                results[i] = FetchResult(ok=False, items=[], error=f"Timeout: over {deadline:.0f}s budget")
                pending.discard(i)
    # Do not wait for a stuck source: its thread ends on its own when the socket read times out.
    pool.shutdown(wait=False, cancel_futures=True)
    if all(f.done() for f in futures):
        client.close()
    return results  # type: ignore[return-value]  every slot is filled when the loop exits


def _existing_urls(s: Session, urls: list[str]) -> set[str]:
    """One query per 500 URLs instead of one query per item."""
    seen: set[str] = set()
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        seen.update(s.scalars(select(Item.canonical_url).where(Item.canonical_url.in_(chunk))).all())
    return seen


def _resolve_gnews_links(s: Session, due_cfgs: list[SourceConfig], results: list[FetchResult], cap: int,
                         resolver=None) -> int:
    """Swap Google News wrapper links for the publisher's real address on items we have not stored yet.

    X only shows a preview card for the real address, and the real address is what lets a Google News copy
    deduplicate against the publisher's own feed item.
    """
    from .collect.gnews import is_gnews, resolve_many

    resolver = resolver or resolve_many
    raws = [raw for cfg, res in zip(due_cfgs, results) if cfg.kind == "gnews" and res.ok
            for raw in res.items if is_gnews(raw.original_url)]
    if not raws or cap <= 0:
        return 0
    known = _existing_urls(s, [r.canonical_url for r in raws])
    todo = [r for r in raws if r.canonical_url not in known][:cap]
    mapping = resolver([r.original_url for r in todo])
    n = 0
    for r in todo:
        real = mapping.get(r.original_url)
        if real and not is_gnews(real):
            r.original_url, r.canonical_url = real, canonical_url(real)
            n += 1
    return n


def collect(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
            fetcher=fetch_source, resolver=None) -> list[Item]:
    now = now or utcnow()
    rows = sync_sources(s, settings)
    new_items: list[Item] = []
    stats.update(sources_checked=0, sources_failed=0, items_seen=0, items_new=0, items_filtered=0)
    ccfg = _collect_cfg(settings)
    grace = timedelta(minutes=float(ccfg.get("due_grace_minutes", DEFAULT_DUE_GRACE_MINUTES)))
    due_cfgs = [cfg for cfg in settings.sources if _due(rows[cfg.key], cfg, now, grace)]
    # Read everything the threads need from the ORM rows here, on the session's own thread.
    due = [(cfg, rows[cfg.key].etag, rows[cfg.key].last_modified) for cfg in due_cfgs]
    for cfg in due_cfgs:
        rows[cfg.key].last_attempt_at = now
    stats["sources_checked"] = len(due)
    results = fetch_all(due, fetcher=fetcher, workers=int(ccfg.get("workers", DEFAULT_FETCH_WORKERS)),
                        deadline=float(ccfg.get("source_timeout_seconds", FETCH_DEADLINE)))
    stats["gnews_resolved"] = _resolve_gnews_links(s, due_cfgs, results, int(ccfg.get("gnews_resolve_per_cycle", 40)),
                                                   resolver=resolver)
    seen = _existing_urls(s, [raw.canonical_url for res in results for raw in res.items if raw.canonical_url])
    for cfg, res in zip(due_cfgs, results):
        row = rows[cfg.key]
        if not res.ok:
            row.consecutive_failures += 1
            row.last_error = res.error
            stats["sources_failed"] += 1
            event(s, "source_failed", "source", row.id, error=res.error, failures=row.consecutive_failures)
            continue
        row.consecutive_failures, row.last_error, row.last_success_at = 0, None, now
        row.etag, row.last_modified = res.etag, res.last_modified
        for raw in res.items:
            stats["items_seen"] += 1
            if not raw.canonical_url or raw.canonical_url in seen:
                continue
            seen.add(raw.canonical_url)
            reason = filter_reason(raw, settings.filter, now)
            item = Item(
                source=row, external_id=(raw.external_id or "")[:300] or None,
                canonical_url=raw.canonical_url, original_url=raw.original_url, title=raw.title,
                summary=raw.summary, publisher=raw.publisher or row.name, author=raw.author,
                published_at=raw.published_at or now, discovered_at=now, entities=raw.entities,
                media=find_media(f"{raw.raw_html} {raw.original_url}", raw.summary, host_of(raw.original_url)),
                filtered_reason=reason,
            )
            s.add(item)
            stats["items_new"] += 1
            if reason:
                stats["items_filtered"] += 1
            else:
                new_items.append(item)
    s.flush()
    return new_items


# ----------------------------------------------------------------------------- stories


def _refresh_story(s: Session, story: Story) -> None:
    items = [i for i in story.items if not i.filtered_reason]
    story.source_count = len({normalize_publisher(i.publisher or i.source.name) for i in items})
    story.tier1_count = sum(1 for i in items if i.source.tier == 1)
    ents: dict[str, None] = {}
    for i in sorted(items, key=lambda x: x.source.tier):
        for e in i.entities:
            ents.setdefault(e, None)
    story.entities = list(ents)[:16]
    story.last_updated_at = max((_aware(i.published_at) or _aware(i.discovered_at)) for i in items)
    tier1 = [i for i in items if i.source.tier == 1]
    if tier1:
        story.category = tier1[0].source.category
        story.title = tier1[0].title
    else:
        story.category = items[0].source.category


def cluster_items(s: Session, new_items: list[Item], stats: dict[str, Any], now: datetime | None = None) -> None:
    now = now or utcnow()
    stats.update(stories_new=0, items_attached=0)
    active = s.scalars(select(Story).where(Story.last_updated_at >= now - ACTIVE_WINDOW)).all()
    cands = [Candidate(st.id, st.title, st.entities, _aware(st.last_updated_at)) for st in active]
    by_id = {st.id: st for st in active}
    for item in sorted(new_items, key=lambda i: i.source.tier):
        when = _aware(item.published_at) or now
        match = best_match(item.title, item.entities, when, cands)
        if match:
            story = by_id[match.story_id]
            item.story = story
            _refresh_story(s, story)
            match.entities, match.title, match.last_updated_at = story.entities, story.title, _aware(story.last_updated_at)
            stats["items_attached"] += 1
            event(s, "item_attached", "story", story.id, item_id=item.id)
        else:
            story = Story(title=item.title, category=item.source.category, entities=item.entities,
                          first_seen_at=when, last_updated_at=when)
            s.add(story)
            item.story = story
            s.flush()
            _refresh_story(s, story)
            cands.append(Candidate(story.id, story.title, story.entities, _aware(story.last_updated_at)))
            by_id[story.id] = story
            stats["stories_new"] += 1
    s.flush()


def _recent_posts(s: Session, days: float, now: datetime) -> list[Post]:
    since = now - timedelta(days=days)
    return s.scalars(select(Post).where(Post.created_at >= since, Post.status.in_(("published", "mock", "manual")))).all()


def rescore(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None) -> None:
    now = now or utcnow()
    cooldown = float(settings.limits["entity_cooldown_days"])
    recent = _recent_posts(s, cooldown, now)
    rec_ents = [s.get(Story, p.story_id).entities for p in recent]
    rec_age = [(now - _aware(p.created_at)).total_seconds() / 86400 for p in recent]
    # Three queries for everything (stories, their items, the items' sources) instead of two per story:
    # over a remote database that is the difference between seconds and many minutes.
    from sqlalchemy.orm import selectinload

    active = s.scalars(
        select(Story).where(Story.last_updated_at >= now - ACTIVE_WINDOW, Story.status == "discovered")
        .options(selectinload(Story.items).selectinload(Item.source))
    ).all()
    for st in active:
        items = [i for i in st.items if not i.filtered_reason]
        if not items:
            continue
        facts = StoryFacts(
            source_count=st.source_count, tier1_count=st.tier1_count, first_seen_at=_aware(st.first_seen_at),
            last_updated_at=_aware(st.last_updated_at), category=st.category,
            novelty=novelty_from_recent(st.entities, rec_ents, rec_age, cooldown),
            max_tier=min(i.source.tier for i in items),
        )
        st.score, st.score_breakdown, st.classification = score_story(facts, settings.scoring, now)
        if any(i.source.boost_until and _aware(i.source.boost_until) > now for i in items):
            st.score = min(st.score + 10, 100.0)
            st.score_breakdown["boost"] = 10
    stats["stories_scored"] = len(active)


# ----------------------------------------------------------------------------- drafting


def _history(s: Session, settings: Settings, now: datetime) -> PostingHistory:
    lim = settings.limits
    posts = _recent_posts(s, float(lim["story_cooldown_days"]), now)
    hist = PostingHistory()
    hist.posted_at = [_aware(p.published_at or p.created_at) for p in posts]
    hist.story_ids_recent = {p.story_id for p in posts}
    ent_cut = now - timedelta(days=float(lim["entity_cooldown_days"]))
    for p in posts:
        if _aware(p.created_at) >= ent_cut:
            hist.entities_recent |= {e.lower() for e in s.get(Story, p.story_id).entities}
    tz = ZoneInfo(settings.automation.get("timezone", "UTC"))
    opened = window_opened_at(now.astimezone(tz), settings.overnight["window_start"]).astimezone(timezone.utc)
    hist.autonomous_in_window = sum(1 for p in posts if p.autonomous and _aware(p.created_at) >= opened)
    return hist


def _channel(settings: Settings):
    env = settings.env
    return TelegramChannel(env.telegram_bot_token, env.telegram_chat_id) if env.has_telegram else ConsoleChannel()


def _publisher(settings: Settings, channel=None):
    """api: X API (paid). intent: hand off to the operator's phone (free). Otherwise mock."""
    from .publish.intent import ManualPublisher

    env = settings.env
    pub_cfg = settings.raw.get("publishing", {})
    mode = pub_cfg.get("mode", "intent")
    if mode == "api" and env.has_x and pub_cfg.get("enabled", False):
        return XPublisher(env.x_api_key, env.x_api_secret, env.x_access_token, env.x_access_secret,
                          username=pub_cfg.get("x_username", ""))
    if mode == "intent" and pub_cfg.get("enabled", False):
        ch = channel or _channel(settings)
        if hasattr(ch, "send_with_buttons"):
            return ManualPublisher(ch)
    return MockPublisher()


def _official_handles(settings: Settings) -> set[str]:
    return {c.x_handle.lstrip("@").lower() for c in settings.sources if c.x_handle}


def _opener(text: str) -> str:
    """The first few words of a post: what the next draft must not reuse."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return " ".join(first.split()[:4])[:40]


def _source_order(i: Item, now: datetime) -> tuple:
    """Best tier first; within a tier, written sources before videos, then newest first. Unresolved Google wrappers last."""
    is_video = 1 if i.source.kind == "youtube" or "youtube.com" in i.original_url else 0
    is_wrapper = 1 if "news.google.com" in (i.original_url or "") else 0
    return (i.source.tier, is_wrapper, is_video, -(_aware(i.published_at) or now).timestamp())


def _reply_text(views: list[SourceView], media_refs: list[dict]) -> str:
    """The article source link (never a Google wrapper when a real address exists), plus the official video link."""
    if not views:
        return ""
    primary = next((v for v in views if "news.google.com" not in v.url), views[0])
    lines = [f"Source: {primary.url}"]
    yt = next((m for m in media_refs if m.get("type") == "youtube"), None)
    if yt and yt["url"] != primary.url:
        lines.append(f"Video: {yt['url']}")
    return "\n".join(lines)


def draft_stories(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
                  provider=None, channel=None, check_links: bool = True) -> list[Draft]:
    now = now or utcnow()
    provider = provider or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    channel = channel or _channel(settings)
    hist = _history(s, settings, now)
    paused = get_state(s, "paused", str(settings.automation.get("paused", False))).lower() == "true"
    mode = get_state(s, "mode", settings.automation.get("mode", "approval"))
    night_cap = int(get_state(s, "night_cap", str(settings.overnight["night_cap"])))
    min_score = float(settings.drafting.get("min_score", 0) or 0)
    eligible = s.scalars(
        select(Story).where(Story.status == "discovered", Story.classification.in_(("breaking", "hot", "trending")),
                            Story.score >= min_score, Story.last_updated_at >= now - ACTIVE_WINDOW)
        .order_by(Story.score.desc())
    ).all()
    made: list[Draft] = []
    limit = int(settings.limits.get("max_drafts_per_run", 0) or 0)  # 0 = unlimited: the news sets the pace
    recent_openers = [_opener(d.text) for d in s.scalars(select(Draft).order_by(Draft.id.desc()).limit(10)).all()]
    stats.update(drafts_made=0, drafts_failed=0)
    for st in eligible:
        if limit and len(made) >= limit:
            break
        if st.id in hist.story_ids_recent:
            st.status = "skipped"
            event(s, "story_skipped", "story", st.id, reason="story_cooldown")
            continue
        items = sorted((i for i in st.items if not i.filtered_reason), key=lambda i: _source_order(i, now))
        views = [SourceView(i.id, i.publisher or i.source.name, i.source.tier, i.title, i.summary, i.original_url) for i in items]
        try:
            out = generate(provider, settings.voice, st.title, st.category, views, int(settings.drafting.get("max_chars", 280)),
                           classification=st.classification,
                           age_hours=(now - _aware(st.first_seen_at)).total_seconds() / 3600 if st.first_seen_at else None,
                           recent_openers=recent_openers)
            recent_openers = ([_opener(out.text)] + recent_openers)[:10]
        except Exception as e:  # noqa: BLE001  one bad provider answer must never end the cycle
            stats["drafts_failed"] += 1
            event(s, "draft_failed", "story", st.id, error=str(e)[:300], provider=provider.name)
            log.warning("draft failed for story %s: %s", st.id, e)
            if "429" in str(e) or "rate limit" in str(e).lower():
                # The provider is out of allowance: stop drafting this cycle; the stories stay eligible for the next one.
                stats["rate_limited"] = True
                event(s, "rate_limited", detail_provider=provider.name)
                break
            continue
        checks = run_checks(out.text, views, settings.drafting, check_links=check_links)
        media_refs = [m for i in items for m in (i.media or [])]
        media = choose_media(media_refs, _official_handles(settings))
        other = st.source_count - (1 if st.tier1_count else 0)
        dec = decide(now_utc=now, settings=settings.raw, story_category=st.category,
                     story_text=f"{st.title} {out.text}", story_entities=st.entities, story_id=st.id,
                     tier1_count=st.tier1_count, other_count=other, checks_passed=checks["passed"],
                     hist=hist, paused=paused, mode=mode, night_cap=night_cap)
        route = dec.route if dec.allowed else "blocked"
        draft = Draft(
            story=st, text=out.text, reply_text=_reply_text(views, media_refs), why=out.why, suggested_take=out.suggested_take,
            created_at=now,
            reason=out.reason, provider=out.provider, model=out.model, source_item_ids=[v.item_id for v in views],
            media=media, checks=checks, checks_passed=checks["passed"], route=route, dev_mode=settings.env.dev_mode,
            status="approved" if route == "autonomous" else ("blocked" if route == "blocked" else "pending"),
        )
        s.add(draft)
        st.status = "drafted"
        s.flush()
        event(s, "draft_created", "draft", draft.id, story_id=st.id, route=route, reasons=dec.reasons, score=st.score)
        card = DraftCard(
            draft_id=draft.id, story_title=st.title, text=out.text, why=out.why, reason=out.reason, score=st.score,
            suggested_take=out.suggested_take,
            classification=st.classification, sources=[(v.publisher, v.url) for v in views], media=media,
            checks=checks, route=route, dev_mode=settings.env.dev_mode, tags=dec.reasons,
        )
        draft.channel_ref = channel.send_draft(card)
        made.append(draft)
        stats["drafts_made"] += 1
    return made


def draft_one(s: Session, settings: Settings, story: Story, now: datetime, provider=None, channel=None,
              check_links: bool | None = None, official_only_quote: bool = True) -> Draft | None:
    """Draft a single story on operator request, bypassing the score threshold but not the checks or rules."""
    provider = provider or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    channel = channel or _channel(settings)
    if check_links is None:
        check_links = bool(settings.drafting.get("check_links", True))
    hist = _history(s, settings, now)
    paused = get_state(s, "paused", str(settings.automation.get("paused", False))).lower() == "true"
    mode = get_state(s, "mode", settings.automation.get("mode", "approval"))
    items = sorted((i for i in story.items if not i.filtered_reason), key=lambda i: _source_order(i, now))
    views = [SourceView(i.id, i.publisher or i.source.name, i.source.tier, i.title, i.summary, i.original_url) for i in items]
    if not views:
        return None
    try:
        out = generate(provider, settings.voice, story.title, story.category, views, int(settings.drafting.get("max_chars", 280)))
    except Exception as e:  # noqa: BLE001
        event(s, "draft_failed", "story", story.id, error=str(e)[:300], provider=provider.name)
        return None
    checks = run_checks(out.text, views, settings.drafting, check_links=check_links)
    media_refs = [m for i in items for m in (i.media or [])]
    # An operator-submitted X link may be quoted even if the author is not a configured official handle:
    # the operator chose it. Everything else keeps the official-only rule.
    handles = _official_handles(settings)
    if not official_only_quote:
        handles |= {m["author"].lower() for m in media_refs if m.get("type") == "x_post"}
    media = choose_media(media_refs, handles)
    dec = decide(now_utc=now, settings=settings.raw, story_category=story.category, story_text=f"{story.title} {out.text}",
                 story_entities=story.entities, story_id=story.id, tier1_count=story.tier1_count,
                 other_count=max(story.source_count - (1 if story.tier1_count else 0), 0), checks_passed=checks["passed"],
                 hist=hist, paused=paused, mode=mode)
    route = "review" if dec.allowed else "blocked"  # operator-submitted stories are never autonomous
    draft = Draft(story=story, text=out.text, reply_text=_reply_text(views, media_refs), why=out.why, reason=out.reason,
                  suggested_take=out.suggested_take, created_at=now,
                  provider=out.provider, model=out.model, source_item_ids=[v.item_id for v in views], media=media,
                  checks=checks, checks_passed=checks["passed"], route=route, dev_mode=settings.env.dev_mode,
                  status="blocked" if route == "blocked" else "pending")
    s.add(draft)
    story.status = "drafted"
    s.flush()
    event(s, "draft_created", "draft", draft.id, story_id=story.id, route=route, reasons=dec.reasons + ["operator"])
    card = DraftCard(draft_id=draft.id, story_title=story.title, text=out.text, why=out.why, reason=out.reason,
                     score=story.score, classification=story.classification, sources=[(v.publisher, v.url) for v in views],
                     media=media, checks=checks, route=route, dev_mode=settings.env.dev_mode, tags=dec.reasons + ["operator"],
                     suggested_take=out.suggested_take)
    draft.channel_ref = channel.send_draft(card)
    return draft


def draft_educational(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
                      provider=None, channel=None) -> Draft | None:
    """At most N per day, from the oldest unused operator note. Always routed to review."""
    now = now or utcnow()
    cfg = settings.raw.get("educational", {})
    stats["edu_drafts"] = 0
    if not cfg.get("enabled", True):
        return None
    note = s.scalars(select(Note).where(Note.used.is_(False)).order_by(Note.id)).first()
    if not note:
        return None
    since = now - timedelta(days=1)
    today = s.scalar(select(func.count(Story.id)).where(Story.category == "educational", Story.first_seen_at >= since))
    if today >= int(cfg.get("max_per_day", 1)):
        return None
    provider = provider or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    channel = channel or _channel(settings)
    try:
        out = generate_educational(provider, settings.voice, note.text)
    except Exception as e:  # noqa: BLE001
        event(s, "draft_failed", "note", note.id, error=str(e)[:300])
        return None
    story = Story(title=f"Note: {note.text[:90]}", category="educational", entities=[], first_seen_at=now,
                  last_updated_at=now, source_count=1, classification="developing", status="drafted")
    s.add(story)
    s.flush()
    checks = run_checks(out.text, [], settings.drafting, check_links=False)
    draft = Draft(story=story, text=out.text, reply_text="", why=out.why, reason=out.reason, provider=out.provider,
                  model=out.model, source_item_ids=[], media={"mode": "render", "rights": "original"}, checks=checks, created_at=now,
                  checks_passed=checks["passed"], route="review", status="pending", dev_mode=settings.env.dev_mode)
    s.add(draft)
    note.used = True
    s.flush()
    event(s, "draft_created", "draft", draft.id, story_id=story.id, route="review", reasons=["educational"], note_id=note.id)
    card = DraftCard(draft_id=draft.id, story_title=story.title, text=out.text, why=out.why, reason=out.reason, score=0,
                     classification="educational", sources=[("operator note", f"note #{note.id}")], media=draft.media,
                     checks=checks, route="review", dev_mode=settings.env.dev_mode, tags=["educational"])
    draft.channel_ref = channel.send_draft(card)
    stats["edu_drafts"] = 1
    return draft


def maybe_send_digest(s: Session, settings: Settings, channel, now: datetime) -> bool:
    from .channels.commands import digest_text

    cfg = settings.raw.get("digest", {})
    tz = ZoneInfo(settings.automation.get("timezone", "UTC"))
    local = now.astimezone(tz)
    if local.weekday() != int(cfg.get("weekday", 0)) or local.hour < int(cfg.get("hour", 8)):
        return False
    week = f"{local.isocalendar().year}-{local.isocalendar().week}"
    if get_state(s, "last_digest_week", "") == week:
        return False
    channel.notify(digest_text(s, settings, now))
    set_state(s, "last_digest_week", week)
    return True


# ----------------------------------------------------------------------------- publishing


_URL_IN_TEXT = re.compile(r"https?://|\b[a-z0-9-]+\.(com|org|ai|io|gov|net)\b", re.I)


def _api_reply_text(d: Draft, mode: str) -> str:
    """plain: name the publisher, no URL (X charges 13x more for a post containing a URL)."""
    if mode == "none":
        return ""
    if mode == "link":
        return d.reply_text
    items = [i for i in d.story.items if i.id in set(d.source_item_ids or [])] or [i for i in d.story.items if not i.filtered_reason]
    items.sort(key=lambda i: i.source.tier)
    if not items:
        return ""
    primary = items[0]
    extra = len({normalize_publisher(i.publisher or i.source.name) for i in items}) - 1
    tail = f" and {extra} other source{'s' if extra != 1 else ''}" if extra > 0 else ""
    return f"Source: {primary.publisher or primary.source.name}{tail}."


_PREVIEW_TAG = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)(?::src)?["\']', re.I)


def _link_has_preview(url: str, timeout: float = 8.0) -> bool:
    """Will X draw a card with a picture for this link? YouTube always; a page only if it declares an image tag."""
    if not url:
        return False
    if "youtube.com" in url or "youtu.be" in url:
        return True
    try:
        import httpx

        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; VictorNewsroom/0.1)"})
        return r.status_code < 400 and bool(_PREVIEW_TAG.search(r.text[:200_000]))
    except Exception:  # noqa: BLE001
        return False


def _post_link(reply_text: str) -> str:
    """From 'Source: <url>\\nVideo: <url>' pick the video link first, else the source link."""
    found = {}
    for line in (reply_text or "").splitlines():
        k, _, v = line.partition(":")
        if v.strip().startswith("http"):
            found[k.strip().lower()] = v.strip()
    return found.get("video") or found.get("source") or ""


def _estimate_cost(d: Draft, pub_cfg: dict) -> float:
    per_post = float(pub_cfg.get("cost_post_usd", 0.015))
    per_url = float(pub_cfg.get("cost_post_with_url_usd", 0.200))
    cost = per_url if _URL_IN_TEXT.search(d.text) else per_post
    mode = pub_cfg.get("api_reply", "plain")
    if mode == "link" and d.reply_text:
        cost += per_url
    elif mode == "plain":
        cost += per_post
    return round(cost, 4)


def reconcile_uncertain(s: Session, publisher, channel, now: datetime) -> int:
    """For posts whose submit result was ambiguous, ask the platform whether it exists. Never resubmit."""
    if not hasattr(publisher, "find_recent"):
        return 0
    fixed = 0
    for p in s.scalars(select(Post).where(Post.status == "uncertain")).all():
        found = publisher.find_recent(p.text)
        if found:
            p.remote_id, p.remote_url, p.status, p.published_at = found[0], found[1], "published", now
            p.draft.status, p.draft.story.status = "published", "posted"
            event(s, "reconciled", "post", p.id, url=found[1])
            channel.notify(f"Reconciled: draft #{p.draft_id} was in fact published: {found[1]}")
            fixed += 1
    return fixed


def publish_approved(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
                     publisher=None, channel=None) -> list[Post]:
    now = now or utcnow()
    channel = channel or _channel(settings)
    publisher = publisher or _publisher(settings, channel)
    paused = get_state(s, "paused", str(settings.automation.get("paused", False))).lower() == "true"
    stats.update(posts_published=0, posts_mock=0, posts_manual=0, posts_failed=0, posts_deferred=0)
    if paused:
        stats["paused"] = True
        return []
    stats["reconciled"] = reconcile_uncertain(s, publisher, channel, now)
    pub_cfg = settings.raw.get("publishing", {})
    tz = ZoneInfo(settings.automation.get("timezone", "UTC"))
    local = now.astimezone(tz)
    night = in_window(local, settings.overnight["window_start"], settings.overnight["window_end"])
    drafts = s.scalars(select(Draft).where(Draft.status.in_(("approved", "approved_night"))).order_by(Draft.created_at)).all()
    done: list[Post] = []
    manual = getattr(publisher, "name", "") == "manual"
    api = getattr(publisher, "name", "") == "x"
    budget = float(pub_cfg.get("daily_budget_usd", 0.25))
    spent_today = float(s.scalar(select(func.coalesce(func.sum(Post.cost_usd), 0.0))
                                 .where(Post.created_at >= now - timedelta(days=1))) or 0.0)
    stats["api_spent_today_usd"] = round(spent_today, 3)
    for d in drafts:
        if d.status == "approved_night" and not night and not manual:
            stats["posts_deferred"] += 1
            continue
        est = _estimate_cost(d, pub_cfg) if api else 0.0
        if api and spent_today + est > budget:
            stats["posts_deferred"] += 1
            stats["budget_blocked"] = True
            continue
        hist = _history(s, settings, now)
        enforce = not manual or bool(settings.limits.get("apply_to_manual", False))
        if enforce and limits_ok(now, settings.limits, hist):
            stats["posts_deferred"] += 1
            continue
        key = f"draft-{d.id}-v{d.version}"
        if s.scalar(select(Post.id).where(Post.idempotency_key == key)):
            continue  # already attempted; never double-submit
        post = Post(draft=d, story_id=d.story_id, idempotency_key=key, text=d.text, autonomous=(d.route == "autonomous"))
        s.add(post)
        s.flush()
        media = d.media or {}
        link = _post_link(d.reply_text) if (manual and pub_cfg.get("link_in_post", True)) else ""
        # Real photo or footage beats everything; a link that brings its own preview beats the rendered card;
        # the card is only for posts that would otherwise be bare text.
        link_preview = bool(link) and not pub_cfg.get("render_when_link_preview", False) and _link_has_preview(link)
        if pub_cfg.get("prepare_media", True) and media.get("mode") in ("upload", "render"):
            media = prepare_media(media, d.story.title, d.text, d.story.category, f"@{pub_cfg.get('x_username', '')}",
                                  want_clip=bool(pub_cfg.get("render_clip", True)), allow_render=not link_preview,
                                  allow_video_upload=bool(pub_cfg.get("upload_public_domain_video", False)))
            if media.get("mode") == "link":
                media["url"] = link
            d.media = media
        reply = d.reply_text
        text = d.text
        if api:
            reply = _api_reply_text(d, pub_cfg.get("api_reply", "plain"))
            post.cost_usd = est
            spent_today += est
        elif manual:
            if (d.take or "").strip():  # the human's own line is part of the post, above the link
                text = f"{text.rstrip()}\n\n{d.take.strip()}"
            if pub_cfg.get("link_in_post", True):
                # Posting by hand costs nothing per link, so the video link (else the source link) goes in the post itself.
                link = _post_link(d.reply_text)
                if link:
                    text = f"{text.rstrip()}\n\n{link}"
                    reply = ""
            if media.get("attribution"):  # a CC photo must carry its credit; the credit travels with the post
                text = f"{text.rstrip()}\n{media['attribution']}"
        req = PublishRequest(idempotency_key=key, text=text, reply_text=reply,
                             quote_url=media.get("url", "") if media.get("mode") == "quote" else "",
                             media_path=media.get("path", "") if media.get("mode") in ("upload", "render") else "")
        if manual:
            publisher.draft_id = d.id
            publisher.message_ref = d.channel_ref  # transform the card in place instead of adding a message
            if enforce:
                from .intelligence.rules import next_slot

                after = PostingHistory(posted_at=hist.posted_at + [now])
                nxt = next_slot(now, settings.limits, after)
                tz = ZoneInfo(settings.automation.get("timezone", "UTC"))
                publisher.footer = f"Next slot opens at {nxt.astimezone(tz):%H:%M}."
        res = publisher.publish(req)
        post.status, post.remote_id, post.remote_url, post.reply_remote_id, post.error = (
            res.status, res.remote_id, res.remote_url, res.reply_remote_id, res.error)
        if res.status == "published":
            post.published_at = now
            d.status, d.story.status = "published", "posted"
            stats["posts_published"] += 1
            channel.notify(f"Published draft #{d.id}: {res.remote_url}")
        elif res.status == "mock":
            post.published_at = now
            d.status, d.story.status = "simulated", "posted"
            stats["posts_mock"] += 1
        elif res.status == "manual":
            d.status, d.story.status = "handed_off", "posted"  # operator confirms with the Posted button
            stats["posts_manual"] += 1
        elif res.status == "uncertain":
            d.status = "uncertain"
            stats["posts_failed"] += 1
            channel.notify(f"Draft #{d.id} publish result UNCERTAIN, check X manually before retrying: {res.error}")
        else:
            d.status = "failed"
            stats["posts_failed"] += 1
            channel.notify(f"Draft #{d.id} failed to publish: {res.error}")
        d.decided_at = d.decided_at or now
        event(s, "publish_attempt", "post", post.id, status=res.status, error=res.error, autonomous=post.autonomous)
        done.append(post)
    return done


# ----------------------------------------------------------------------------- run


class _Timer:
    """Wall-clock seconds per stage, so a slow cycle shows where the time went in the Actions log."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self._t0 = time.perf_counter()

    def __call__(self, name: str):
        timer = self

        class _Stage:
            def __enter__(self_):
                self_.t = time.perf_counter()

            def __exit__(self_, *exc):
                timer.stages[name] = round(timer.stages.get(name, 0.0) + time.perf_counter() - self_.t, 2)

        return _Stage()

    def total(self) -> float:
        return round(time.perf_counter() - self._t0, 2)

    def summary(self) -> str:
        parts = " ".join(f"{k} {v:.1f}s" for k, v in self.stages.items())
        return f"cycle {self.total():.1f}s: {parts}"


def _commands(s: Session, settings: Settings, channel, now: datetime, provider) -> int:
    """Pull Telegram updates; a Telegram outage or timeout must never stall collection or publishing."""
    from .channels.commands import process_updates

    if not hasattr(channel, "get_updates"):
        return 0
    try:
        return process_updates(s, settings, channel, now, provider)
    except Exception as e:  # noqa: BLE001
        log.warning("command processing failed, continuing the cycle: %s: %s", type(e).__name__, e)
        event(s, "commands_failed", error=f"{type(e).__name__}: {e}"[:300])
        return 0


def run_once(settings: Settings, now: datetime | None = None, **overrides: Any) -> dict[str, Any]:
    now = now or utcnow()
    stats: dict[str, Any] = {"dev_mode": settings.env.dev_mode}
    timer = _Timer()
    channel = overrides.get("channel") or _channel(settings)
    provider = overrides.get("provider") or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    publisher = overrides.get("publisher") or _publisher(settings)
    check_links = overrides.get("check_links", True)
    if not overrides.get("no_lock") and not _acquire_cycle_lock(now):
        return {"skipped": "another cycle is running", "dev_mode": settings.env.dev_mode}
    try:
        return _run_cycle(settings, now, stats, timer, channel, provider, publisher, check_links, overrides)
    finally:
        if not overrides.get("no_lock"):
            _release_cycle_lock()


CYCLE_LOCK_MINUTES = 4  # a cycle takes about a minute; anything older than this is a dead holder


def _acquire_cycle_lock(now: datetime) -> bool:
    """Two cycles must never overlap (the loop job and the backup cron share one database)."""
    with session() as s:
        row = s.get(State, "cycle_lock")
        if row and row.value:
            try:
                held_since = datetime.fromisoformat(row.value)
            except ValueError:
                held_since = None
            if held_since and now - held_since < timedelta(minutes=CYCLE_LOCK_MINUTES):
                return False
        set_state(s, "cycle_lock", now.isoformat())
    return True


def _release_cycle_lock() -> None:
    try:
        with session() as s:
            set_state(s, "cycle_lock", "")
    except Exception:  # noqa: BLE001  the lock expires on its own after CYCLE_LOCK_MINUTES anyway
        log.warning("could not release cycle lock")


def _run_cycle(settings, now, stats, timer, channel, provider, publisher, check_links, overrides) -> dict[str, Any]:
    with session() as s:
        run = Run(started_at=now)
        s.add(run)
        s.flush()
        try:
            with timer("commands"):
                stats["commands_handled"] = _commands(s, settings, channel, now, provider)
            # Commit after every stage: a hosted Postgres pooler drops transactions that stay open for minutes,
            # and a stage that fails must not throw away the work of the stages before it.
            with timer("collect"):
                new_items = collect(s, settings, stats, now, fetcher=overrides.get("fetcher", fetch_source),
                                    resolver=overrides.get("resolver"))
            s.commit()
            with timer("cluster"):
                cluster_items(s, new_items, stats, now)
            s.commit()
            with timer("score"):
                rescore(s, settings, stats, now)
            s.commit()
            with timer("draft"):
                draft_stories(s, settings, stats, now, provider=provider, channel=channel, check_links=check_links)
                draft_educational(s, settings, stats, now, provider=provider, channel=channel)
            s.commit()
            with timer("publish"):
                publish_approved(s, settings, stats, now, publisher=publisher, channel=channel)
                stats["digest_sent"] = maybe_send_digest(s, settings, channel, now)
            s.commit()
            run.ok = True  # before the dashboard snapshot, so the page shows the cycle as ok
            if settings.raw.get("dashboard", {}).get("enabled", True) and overrides.get("dashboard", True):
                from .dashboard import write_dashboard

                with timer("dashboard"):
                    write_dashboard(s, settings, now)
            with timer("commands"):  # pick up anything that arrived during the cycle
                stats["commands_handled"] += _commands(s, settings, channel, now, provider)
        except Exception as e:  # noqa: BLE001
            run.error = f"{type(e).__name__}: {e}"[:1000]
            log.exception("run failed")
            raise
        finally:
            stats["timings"] = {**timer.stages, "total": timer.total()}
            log.info("%s", timer.summary())
            run.finished_at = utcnow()
            run.stats = stats
    return stats
