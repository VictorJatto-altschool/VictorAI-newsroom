"""One newsroom cycle: collect -> filter -> cluster -> score -> draft -> route -> publish -> log."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.draft import ProviderError, SourceView, generate, pick_provider, run_checks
from .channels.base import DraftCard
from .channels.console import ConsoleChannel
from .channels.telegram import TelegramChannel
from .collect.fetch import FetchResult, fetch_source, make_client
from .collect.normalize import host_of, normalize_publisher
from .config import Settings, SourceConfig
from .db import session
from .intelligence.cluster import Candidate, best_match
from .intelligence.filter import filter_reason
from .intelligence.rules import PostingHistory, decide, in_window, limits_ok, window_opened_at
from .intelligence.score import StoryFacts, novelty_from_recent, score_story
from .media.extract import choose_media, find_media
from .models import Draft, Event, Item, Post, Run, Source, State, Story
from .publish.base import PublishRequest
from .publish.mock import MockPublisher
from .publish.x import XPublisher

log = logging.getLogger(__name__)
ACTIVE_WINDOW = timedelta(hours=48)


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


def _due(row: Source, cfg: SourceConfig, now: datetime) -> bool:
    if not row.enabled:
        return False
    if row.blocked_until and _aware(row.blocked_until) > now:
        return False
    if row.last_attempt_at is None:
        return True
    backoff = min(2 ** min(row.consecutive_failures, 6), 48) if row.consecutive_failures else 1
    return now - _aware(row.last_attempt_at) >= timedelta(minutes=cfg.interval_minutes * backoff)


def collect(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
            fetcher=fetch_source) -> list[Item]:
    now = now or utcnow()
    rows = sync_sources(s, settings)
    new_items: list[Item] = []
    stats.update(sources_checked=0, sources_failed=0, items_seen=0, items_new=0, items_filtered=0)
    with make_client() as client:
        for cfg in settings.sources:
            row = rows[cfg.key]
            if not _due(row, cfg, now):
                continue
            stats["sources_checked"] += 1
            row.last_attempt_at = now
            res: FetchResult = fetcher(cfg, row.etag, row.last_modified, client=client)
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
                if not raw.canonical_url:
                    continue
                if s.scalar(select(Item.id).where(Item.canonical_url == raw.canonical_url)):
                    continue
                reason = filter_reason(raw, settings.filter, now)
                item = Item(
                    source=row, external_id=(raw.external_id or "")[:300] or None,
                    canonical_url=raw.canonical_url, original_url=raw.original_url, title=raw.title,
                    summary=raw.summary, publisher=raw.publisher or row.name, author=raw.author,
                    published_at=raw.published_at or now, discovered_at=now, entities=raw.entities,
                    media=find_media(raw.raw_html, raw.summary, host_of(raw.original_url)),
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
    return s.scalars(select(Post).where(Post.created_at >= since, Post.status.in_(("published", "mock")))).all()


def rescore(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None) -> None:
    now = now or utcnow()
    cooldown = float(settings.limits["entity_cooldown_days"])
    recent = _recent_posts(s, cooldown, now)
    rec_ents = [s.get(Story, p.story_id).entities for p in recent]
    rec_age = [(now - _aware(p.created_at)).total_seconds() / 86400 for p in recent]
    active = s.scalars(select(Story).where(Story.last_updated_at >= now - ACTIVE_WINDOW, Story.status == "discovered")).all()
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


def _publisher(settings: Settings):
    env = settings.env
    pub_cfg = settings.raw.get("publishing", {})
    if env.has_x and pub_cfg.get("enabled", False):
        return XPublisher(env.x_api_key, env.x_api_secret, env.x_access_token, env.x_access_secret,
                          username=pub_cfg.get("x_username", ""))
    return MockPublisher()


def _official_handles(settings: Settings) -> set[str]:
    return {c.x_handle.lstrip("@").lower() for c in settings.sources if c.x_handle}


def draft_stories(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
                  provider=None, channel=None, check_links: bool = True) -> list[Draft]:
    now = now or utcnow()
    provider = provider or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    channel = channel or _channel(settings)
    hist = _history(s, settings, now)
    paused = get_state(s, "paused", str(settings.automation.get("paused", False))).lower() == "true"
    mode = get_state(s, "mode", settings.automation.get("mode", "approval"))
    night_cap = int(get_state(s, "night_cap", str(settings.overnight["night_cap"])))
    eligible = s.scalars(
        select(Story).where(Story.status == "discovered", Story.classification.in_(("breaking", "hot", "trending")),
                            Story.last_updated_at >= now - ACTIVE_WINDOW).order_by(Story.score.desc())
    ).all()
    made: list[Draft] = []
    limit = int(settings.limits["max_drafts_per_run"])
    stats.update(drafts_made=0, drafts_failed=0)
    for st in eligible:
        if len(made) >= limit:
            break
        if st.id in hist.story_ids_recent:
            st.status = "skipped"
            event(s, "story_skipped", "story", st.id, reason="story_cooldown")
            continue
        items = sorted((i for i in st.items if not i.filtered_reason), key=lambda i: (i.source.tier, -(_aware(i.published_at) or now).timestamp()))
        views = [SourceView(i.id, i.publisher or i.source.name, i.source.tier, i.title, i.summary, i.original_url) for i in items]
        try:
            out = generate(provider, settings.voice, st.title, st.category, views)
        except ProviderError as e:
            stats["drafts_failed"] += 1
            event(s, "draft_failed", "story", st.id, error=str(e)[:300], provider=provider.name)
            log.warning("draft failed for story %s: %s", st.id, e)
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
            story=st, text=out.text, reply_text=f"Source: {views[0].url}" if views else "", why=out.why,
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
            classification=st.classification, sources=[(v.publisher, v.url) for v in views], media=media,
            checks=checks, route=route, dev_mode=settings.env.dev_mode, tags=dec.reasons,
        )
        draft.channel_ref = channel.send_draft(card)
        made.append(draft)
        stats["drafts_made"] += 1
    return made


# ----------------------------------------------------------------------------- publishing


def publish_approved(s: Session, settings: Settings, stats: dict[str, Any], now: datetime | None = None,
                     publisher=None, channel=None) -> list[Post]:
    now = now or utcnow()
    publisher = publisher or _publisher(settings)
    channel = channel or _channel(settings)
    paused = get_state(s, "paused", str(settings.automation.get("paused", False))).lower() == "true"
    stats.update(posts_published=0, posts_mock=0, posts_failed=0, posts_deferred=0)
    if paused:
        stats["paused"] = True
        return []
    tz = ZoneInfo(settings.automation.get("timezone", "UTC"))
    local = now.astimezone(tz)
    night = in_window(local, settings.overnight["window_start"], settings.overnight["window_end"])
    drafts = s.scalars(select(Draft).where(Draft.status.in_(("approved", "approved_night"))).order_by(Draft.created_at)).all()
    done: list[Post] = []
    for d in drafts:
        if d.status == "approved_night" and not night:
            stats["posts_deferred"] += 1
            continue
        hist = _history(s, settings, now)
        if limits_ok(now, settings.limits, hist):
            stats["posts_deferred"] += 1
            continue
        key = f"draft-{d.id}-v{d.version}"
        if s.scalar(select(Post.id).where(Post.idempotency_key == key)):
            continue  # already attempted; never double-submit
        post = Post(draft=d, story_id=d.story_id, idempotency_key=key, text=d.text, autonomous=(d.route == "autonomous"))
        s.add(post)
        s.flush()
        req = PublishRequest(idempotency_key=key, text=d.text, reply_text=d.reply_text,
                             quote_url=d.media.get("url", "") if d.media.get("mode") == "quote" else "")
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


def run_once(settings: Settings, now: datetime | None = None, **overrides: Any) -> dict[str, Any]:
    from .channels.commands import process_updates

    now = now or utcnow()
    stats: dict[str, Any] = {"dev_mode": settings.env.dev_mode}
    channel = overrides.get("channel") or _channel(settings)
    provider = overrides.get("provider") or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    publisher = overrides.get("publisher") or _publisher(settings)
    check_links = overrides.get("check_links", True)
    with session() as s:
        run = Run(started_at=now)
        s.add(run)
        s.flush()
        try:
            if hasattr(channel, "get_updates"):
                stats["commands_handled"] = process_updates(s, settings, channel, now, provider)
            new_items = collect(s, settings, stats, now, fetcher=overrides.get("fetcher", fetch_source))
            cluster_items(s, new_items, stats, now)
            rescore(s, settings, stats, now)
            draft_stories(s, settings, stats, now, provider=provider, channel=channel, check_links=check_links)
            publish_approved(s, settings, stats, now, publisher=publisher, channel=channel)
            if hasattr(channel, "get_updates"):  # pick up anything that arrived during the cycle
                stats["commands_handled"] += process_updates(s, settings, channel, now, provider)
            run.ok = True
        except Exception as e:  # noqa: BLE001
            run.error = f"{type(e).__name__}: {e}"[:1000]
            log.exception("run failed")
            raise
        finally:
            run.finished_at = utcnow()
            run.stats = stats
    return stats
