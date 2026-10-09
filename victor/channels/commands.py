"""Operator commands and button presses from Telegram. Only the configured chat id is obeyed."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai.draft import SourceView, generate, pick_provider, run_checks
from ..config import Settings
from ..models import Draft, GrowthSnapshot, Item, Note, Post, Run, Source, Story
from .base import DraftCard

log = logging.getLogger(__name__)

HELP = """Commands:
/status  - last run, queue, failing sources
/tonight - what is approved for the overnight window
/posted  - what went out in the last 24h
/pending - drafts waiting for you
/pause | /resume - emergency stop for all publishing
/mode manual|approval|restricted_autonomous
/night N - autonomous posts allowed per night
/block <source-key> [days] | /unblock <source-key>
/boost <source-key> [days]
/sources - source keys and health
/note <text> - save a tool note for educational posts
/growth <followers> <verified_impressions_90d> - record numbers from X analytics
/digest - weekly summary now
/story <link> [angle] - draft a story you found yourself; an X post link is quoted so its video plays
Reply to a draft card with new text to edit it. Buttons: Approve, Approve for night, Rewrite, Reject."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if (dt is None or dt.tzinfo) else dt.replace(tzinfo=timezone.utc)


def process_updates(s: Session, settings: Settings, tg, now: datetime | None = None, provider=None) -> int:
    """Pull pending Telegram updates once and act on them. Returns number handled."""
    from ..pipeline import get_state, set_state  # local import avoids a cycle

    now = now or _utcnow()
    # Always read the offset fresh from the database: a second process (the `telegram` poller) may have
    # advanced it while this session was busy, and a stale value would replay taps it already handled.
    from ..models import State

    row = s.execute(select(State).where(State.key == "telegram_offset").execution_options(populate_existing=True)).scalar_one_or_none()
    offset = int(row.value) if row and row.value else None
    updates = tg.get_updates(offset)
    handled = 0
    for u in updates:
        set_state(s, "telegram_offset", str(int(u["update_id"]) + 1))
        try:
            if "callback_query" in u:
                _handle_callback(s, settings, tg, u["callback_query"], now, provider)
                handled += 1
            elif "message" in u:
                _handle_message(s, settings, tg, u["message"], now)
                handled += 1
        except Exception as e:  # noqa: BLE001  a bad command must not break the run
            log.exception("telegram update failed")
            tg.notify(f"Command failed: {type(e).__name__}: {e}")
    return handled


def _authorized(settings: Settings, chat_id: Any) -> bool:
    return str(chat_id) == str(settings.env.telegram_chat_id)


# ----------------------------------------------------------------------------- buttons


def _handle_callback(s: Session, settings: Settings, tg, cq: dict, now: datetime, provider) -> None:
    from ..pipeline import event, set_state

    chat_id = (cq.get("message") or {}).get("chat", {}).get("id") or (cq.get("from") or {}).get("id")
    if not _authorized(settings, chat_id):
        tg.answer_callback(cq["id"], "Not authorized")
        return
    data = cq.get("data", "")
    if ":" not in data:
        tg.answer_callback(cq["id"])
        return
    action, _, raw_id = data.partition(":")
    d = s.get(Draft, int(raw_id))
    if not d:
        tg.answer_callback(cq["id"], "Draft not found")
        return
    msg_id = str((cq.get("message") or {}).get("message_id", "")) or d.channel_ref
    if action in ("approve", "night"):
        if not d.checks_passed:
            failed = [k for k, v in d.checks.items() if k != "passed" and isinstance(v, dict) and not v.get("ok")]
            detail = "; ".join(f"{k}: {v.get('missing') or v.get('hits') or v.get('value', '')}" for k, v in d.checks.items()
                               if k in failed and isinstance(v, dict))
            tg.answer_callback(cq["id"], "Failed a check, rewriting")
            tg.notify(f"Draft #{d.id} cannot post as written. Failed: {detail or ', '.join(failed)}. "
                      "Rewriting it now; the new card replaces this one.")
            new = rewrite_draft(s, settings, d, now, provider=provider, tg=tg, note=f"The previous version failed these checks: {detail}. Fix them.")
            tg.mark(msg_id, f"Rewritten as #{new.id}" if new else "Rewrite failed, try again")
            return
        if d.status not in ("pending", "blocked", "approved", "approved_night"):
            tg.answer_callback(cq["id"], f"Already {d.status}")
            return
        if d.status == "blocked":
            d.status = "pending"  # old blocks (entity cooldown) no longer apply; the human decides
        pub = settings.raw.get("publishing", {})
        if pub.get("require_take", False) and not (d.take or "").strip() and pub.get("mode", "intent") == "intent":
            # X's Original Content rules reward your own perspective. One line from you goes into the post.
            set_state(s, "awaiting_take", str(d.id))
            tg.answer_callback(cq["id"], "One line from you first")
            prompt = (f"Draft #{d.id}: type ONE line of your own take (what you think, or why it matters to your audience). "
                      f"It goes into the post above the link and is what makes the post yours under X's Original Content rules.")
            buttons = [[{"text": "Post without my take", "callback_data": f"skiptake:{d.id}"}]]
            if (d.suggested_take or "").strip():
                prompt += f"\n\nSuggestion (tap the button to copy it, then change a few words to make it yours):\n{d.suggested_take.strip()}"
                buttons.insert(0, [{"text": "Copy suggested take", "copy_text": {"text": d.suggested_take.strip()[:256]}}])
            if hasattr(tg, "send_with_buttons"):
                tg.send_with_buttons(prompt, buttons)
            else:
                tg.notify(prompt + " Send 'skip' to post without it.")
            return
        d.status = "approved" if action == "approve" else "approved_night"
        d.decided_at = now
        event(s, f"draft_{d.status}", "draft", d.id, by="telegram")
        tg.mark(msg_id, "Approved" if action == "approve" else "Approved for night")
        tg.answer_callback(cq["id"], "Approved")
        from ..pipeline import publish_approved

        st: dict = {}
        publish_approved(s, settings, st, now, channel=tg)  # manual mode: hands off to the phone right away
        if st.get("posts_deferred") and not st.get("posts_manual") and not st.get("posts_published"):
            tg.notify("Approved, but a posting limit is active right now. It will be offered at the next free slot.")
    elif action == "skiptake":
        from ..pipeline import publish_approved

        set_state(s, "awaiting_take", "")
        if d.status in ("pending", "blocked"):
            d.status, d.decided_at = "approved", now
            event(s, "draft_approved", "draft", d.id, by="telegram", take="skipped")
            tg.mark(d.channel_ref, "Approved")
            tg.mark(msg_id, "Posting without a take")
            publish_approved(s, settings, {}, now, channel=tg)
        tg.answer_callback(cq["id"], "Approved")
    elif action == "posted":
        post = s.scalar(select(Post).where(Post.draft_id == d.id).order_by(Post.id.desc()))
        if post and post.status == "manual":
            post.status, post.published_at = "published", now
            d.status, d.story.status = "published", "posted"
            event(s, "posted_manually", "post", post.id, by="telegram")
            tg.mark(msg_id, "Posted")
            tg.answer_callback(cq["id"], "Recorded. Reply to this message with the post link if you want it saved.")
        else:
            tg.answer_callback(cq["id"], "Nothing to confirm")
    elif action == "skip":
        post = s.scalar(select(Post).where(Post.draft_id == d.id).order_by(Post.id.desc()))
        if post and post.status == "manual":
            post.status = "skipped"
            d.status, d.story.status = "rejected", "skipped"
            event(s, "handoff_skipped", "post", post.id, by="telegram")
            tg.mark(msg_id, "Skipped")
        tg.answer_callback(cq["id"], "Skipped")
    elif action == "reject":
        d.status, d.decided_at = "rejected", now
        d.story.status = "skipped"
        event(s, "draft_rejected", "draft", d.id, by="telegram")
        tg.answer_callback(cq["id"], "Rejected")
        _remove_card(tg, msg_id, "Rejected")
    elif action == "rewrite":
        tg.answer_callback(cq["id"], "Rewriting")
        new = rewrite_draft(s, settings, d, now, provider=provider, tg=tg)
        if new:
            _remove_card(tg, msg_id, f"Superseded by #{new.id}")
        else:
            tg.mark(msg_id, "Rewrite failed")
    else:
        tg.answer_callback(cq["id"])


def _remove_card(tg, message_id: str | None, fallback_label: str) -> None:
    """Delete a card that no longer needs attention; if the channel cannot delete, mark it instead."""
    if hasattr(tg, "delete_message") and tg.delete_message(message_id):
        return
    tg.mark(message_id, fallback_label)


def expire_cards(s: Session, settings: Settings, tg, now: datetime | None = None) -> int:
    """Pending cards older than the TTL are removed from the chat and marked expired, so the chat shows only
    what is current. Hand-offs waiting for a Posted tap are never expired."""
    now = now or _utcnow()
    ttl = int((settings.raw.get("telegram") or {}).get("card_ttl_minutes", 30))
    if ttl <= 0:
        return 0
    cutoff = now - timedelta(minutes=ttl)
    rows = s.scalars(select(Draft).where(Draft.status == "pending", Draft.created_at < cutoff)).all()
    n = 0
    for d in rows:
        if d.channel_ref and hasattr(tg, "delete_message"):
            tg.delete_message(d.channel_ref)
        d.status = "expired"
        d.decided_at = now
        n += 1
    if n:
        from ..pipeline import event

        event(s, "cards_expired", count=n, ttl_minutes=ttl)
    return n


def _views_for(d: Draft) -> list[SourceView]:
    items = [i for i in d.story.items if i.id in set(d.source_item_ids)] or [i for i in d.story.items if not i.filtered_reason]
    items.sort(key=lambda i: i.source.tier)
    return [SourceView(i.id, i.publisher or i.source.name, i.source.tier, i.title, i.summary, i.original_url) for i in items]


def _send_card(tg, settings: Settings, d: Draft, views: list[SourceView], tags: list[str]) -> None:
    card = DraftCard(
        draft_id=d.id, story_title=d.story.title, text=d.text, why=d.why, reason=d.reason, score=d.story.score,
        classification=d.story.classification, sources=[(v.publisher, v.url) for v in views], media=d.media,
        checks=d.checks, route=d.route, dev_mode=settings.env.dev_mode, tags=tags, suggested_take=d.suggested_take or "",
    )
    d.channel_ref = tg.send_draft(card)


def new_version(s: Session, settings: Settings, old: Draft, text: str, why: str, reason: str, provider: str,
                model: str, now: datetime, tg=None, check_links: bool | None = None) -> Draft:
    from ..pipeline import event

    if check_links is None:
        check_links = bool(settings.drafting.get("check_links", True))
    views = _views_for(old)
    checks = run_checks(text, views, settings.drafting, check_links=check_links)
    d = Draft(
        story=old.story, text=text, reply_text=old.reply_text, why=why, reason=reason, provider=provider, model=model,
        suggested_take=old.suggested_take or "", created_at=now,
        source_item_ids=old.source_item_ids, media=old.media, checks=checks, checks_passed=checks["passed"],
        route="review", status="pending", version=old.version + 1, dev_mode=settings.env.dev_mode,
    )
    old.status, old.decided_at = "superseded", now
    s.add(d)
    s.flush()
    event(s, "draft_version", "draft", d.id, previous=old.id, version=d.version, provider=provider)
    if tg is not None:
        _send_card(tg, settings, d, views, [f"v{d.version}"])
    return d


def rewrite_draft(s: Session, settings: Settings, old: Draft, now: datetime, provider=None, tg=None,
                  note: str = "", check_links: bool | None = None) -> Draft | None:
    provider = provider or pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    views = _views_for(old)
    voice = settings.voice + (f"\n\nOPERATOR NOTE FOR THIS REWRITE: {note}" if note else "")
    try:
        if old.story.category == "educational" or not views:
            from ..ai.educational import generate_educational
            from ..models import Note

            text = old.story.title.removeprefix("Note: ")
            n = s.scalar(select(Note).where(Note.text.startswith(text[:60])).order_by(Note.id.desc()))
            out = generate_educational(provider, voice, n.text if n else text)
        else:
            out = generate(provider, voice, old.story.title, old.story.category, views, int(settings.drafting.get("max_chars", 280)))
    except Exception as e:  # noqa: BLE001
        log.warning("rewrite failed: %s", e)
        return None
    return new_version(s, settings, old, out.text, out.why, out.reason, out.provider, out.model, now, tg, check_links)


# ----------------------------------------------------------------------------- messages


def _handle_message(s: Session, settings: Settings, tg, m: dict, now: datetime) -> None:
    from ..pipeline import event, get_state, set_state

    if not _authorized(settings, m.get("chat", {}).get("id")):
        return
    _ = (get_state, set_state)
    text = (m.get("text") or "").strip()
    reply = m.get("reply_to_message")
    awaiting = get_state(s, "awaiting_take", "")
    if awaiting and text and not text.startswith("/") and not reply:
        d = s.get(Draft, int(awaiting))
        set_state(s, "awaiting_take", "")
        if d and d.status == "pending":
            if text.lower() != "skip":
                d.take = text[:280]
                event(s, "take_added", "draft", d.id, by="telegram")
            d.status, d.decided_at = "approved", now
            event(s, "draft_approved", "draft", d.id, by="telegram")
            tg.mark(d.channel_ref, "Approved")
            from ..pipeline import publish_approved

            publish_approved(s, settings, {}, now, channel=tg)
            return

    if reply and text and not text.startswith("/"):
        ref = str(reply.get("message_id"))
        if re.match(r"https?://(x|twitter)\.com/\w+/status/\d+", text.strip()):
            # The operator pasted the published post link under a handoff or draft message.
            mref = re.search(r"draft #(\d+)", reply.get("text") or "")
            d = s.get(Draft, int(mref.group(1))) if mref else s.scalar(select(Draft).where(Draft.channel_ref == ref))
            post = s.scalar(select(Post).where(Post.draft_id == d.id).order_by(Post.id.desc())) if d else None
            if post is None:  # fall back to the latest hand-off still missing its link
                post = s.scalar(select(Post).where(Post.status.in_(("manual", "published")), Post.remote_url.is_(None))
                                .order_by(Post.id.desc()))
                d = post.draft if post else None
            if post:
                post.remote_url = text.strip()
                if post.status == "manual":
                    post.status, post.published_at = "published", now
                    d.status, d.story.status = "published", "posted"
                tg.notify(f"Saved the link for draft #{d.id}.")
            else:
                tg.notify("I could not match that link to a draft.")
            return
        d = s.scalar(select(Draft).where(Draft.channel_ref == ref).order_by(Draft.id.desc()))
        if d:
            new = new_version(s, settings, d, text, d.why, "Edited by operator", "operator", "", now, tg)
            tg.notify(f"Draft #{new.id} created from your edit ({'checks ok' if new.checks_passed else 'checks FAILED'}).")
        else:
            tg.notify("That message is not a draft card I know.")
        return
    if not text.startswith("/"):
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower().split("@")[0]
    arg = arg.strip()
    tz = ZoneInfo(settings.automation.get("timezone", "UTC"))

    if cmd in ("/help", "/start"):
        tg.notify(HELP)
    elif cmd == "/pause":
        set_state(s, "paused", "true")
        event(s, "paused", by="telegram")
        tg.notify("PAUSED. No automatic or approved publishing until /resume.")
    elif cmd == "/resume":
        set_state(s, "paused", "false")
        event(s, "resumed", by="telegram")
        tg.notify("Resumed.")
    elif cmd == "/mode":
        if arg not in ("manual", "approval", "restricted_autonomous"):
            tg.notify("Usage: /mode manual|approval|restricted_autonomous")
        else:
            set_state(s, "mode", arg)
            event(s, "mode_changed", mode=arg, by="telegram")
            tg.notify(f"Mode: {arg}")
    elif cmd == "/night":
        if not arg.isdigit():
            tg.notify("Usage: /night N")
        else:
            set_state(s, "night_cap", arg)
            event(s, "night_cap_changed", value=int(arg), by="telegram")
            tg.notify(f"Night cap: {arg}")
    elif cmd == "/status":
        tg.notify(status_text(s, settings, now))
    elif cmd == "/pending":
        rows = s.scalars(select(Draft).where(Draft.status == "pending").order_by(Draft.id.desc()).limit(10)).all()
        tg.notify("Pending:\n" + ("\n".join(f"#{d.id} {d.story.title[:70]}" for d in rows) or "none"))
    elif cmd == "/tonight":
        rows = s.scalars(select(Draft).where(Draft.status.in_(("approved", "approved_night"))).order_by(Draft.id)).all()
        mode = get_state(s, "mode", settings.automation["mode"])
        cap = get_state(s, "night_cap", str(settings.overnight["night_cap"]))
        head = f"Mode {mode}, night cap {cap}, window {settings.overnight['window_start']}-{settings.overnight['window_end']} {tz.key}"
        tg.notify(head + "\nQueued:\n" + ("\n".join(f"#{d.id} [{d.status}] {d.text[:60]}" for d in rows) or "nothing"))
    elif cmd == "/posted":
        since = now - timedelta(hours=24)
        rows = s.scalars(select(Post).where(Post.created_at >= since).order_by(Post.id.desc())).all()
        lines = [f"#{p.id} {p.status}{' auto' if p.autonomous else ''} {p.remote_url or ''}\n  {p.text[:80]}" for p in rows]
        tg.notify("Last 24h:\n" + ("\n".join(lines) or "nothing"))
    elif cmd in ("/block", "/unblock", "/boost"):
        parts = arg.split()
        key = parts[0] if parts else ""
        days = float(parts[1]) if len(parts) > 1 and parts[1].replace(".", "").isdigit() else 7.0
        src = s.scalar(select(Source).where(Source.key == key)) if key else None
        if not src:
            tg.notify(f"Unknown source key. /sources lists them.")
        elif cmd == "/block":
            src.blocked_until = now + timedelta(days=days)
            event(s, "source_blocked", "source", src.id, days=days, by="telegram")
            tg.notify(f"Blocked {src.name} for {days:g} days")
        elif cmd == "/unblock":
            src.blocked_until = None
            tg.notify(f"Unblocked {src.name}")
        else:
            src.boost_until = now + timedelta(days=days)
            event(s, "source_boosted", "source", src.id, days=days, by="telegram")
            tg.notify(f"Boosted {src.name} for {days:g} days")
    elif cmd == "/sources":
        rows = s.scalars(select(Source).order_by(Source.tier, Source.name)).all()
        lines = []
        for r in rows:
            flag = "BLOCKED" if r.blocked_until and _aware(r.blocked_until) > now else ("BOOST" if r.boost_until and _aware(r.boost_until) > now else "")
            fail = f" fail x{r.consecutive_failures}" if r.consecutive_failures else ""
            lines.append(f"t{r.tier} {r.key}{fail} {flag}".rstrip())
        tg.notify("\n".join(lines)[:4000])
    elif cmd == "/note":
        if not arg:
            tg.notify("Usage: /note <what you tried and what happened>")
        else:
            s.add(Note(text=arg))
            tg.notify("Note saved. It will only be used for an educational post, never as news.")
    elif cmd == "/growth":
        nums = [p for p in arg.replace(",", "").split() if p.isdigit()]
        if len(nums) < 1:
            tg.notify("Usage: /growth <followers> [verified_impressions_90d]")
        else:
            snap = GrowthSnapshot(followers=int(nums[0]), verified_impressions_90d=int(nums[1]) if len(nums) > 1 else None,
                                  source="manual")
            s.add(snap)
            event(s, "growth_recorded", followers=snap.followers, impressions=snap.verified_impressions_90d, by="telegram")
            tg.notify(growth_text(s, settings))
    elif cmd == "/digest":
        tg.notify(digest_text(s, settings, now))
    elif cmd == "/story":
        from ..collect.manual import ingest_url
        from ..pipeline import draft_one

        parts = arg.split(maxsplit=1)
        url = parts[0] if parts else ""
        if not url.startswith("http"):
            tg.notify("Usage: /story <link to article or X post> [your note on the angle]")
        else:
            note = parts[1] if len(parts) > 1 else ""
            try:
                story = ingest_url(s, settings, url, note=note, now=now)
            except Exception as e:  # noqa: BLE001
                tg.notify(f"Could not read that link: {type(e).__name__}: {e}")
                return
            d = draft_one(s, settings, story, now, channel=tg, official_only_quote=False)
            if d is None:
                tg.notify("Read the link but could not draft it (AI provider failed). Try again.")
    else:
        tg.notify("Unknown command. /help")


# ----------------------------------------------------------------------------- reports


def status_text(s: Session, settings: Settings, now: datetime) -> str:
    from ..pipeline import get_state

    last = s.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
    paused = get_state(s, "paused", str(settings.automation.get("paused", False))).lower() == "true"
    mode = get_state(s, "mode", settings.automation["mode"])
    pending = s.scalar(select(func.count(Draft.id)).where(Draft.status == "pending"))
    queued = s.scalar(select(func.count(Draft.id)).where(Draft.status.in_(("approved", "approved_night"))))
    posted24 = s.scalar(select(func.count(Post.id)).where(Post.created_at >= now - timedelta(hours=24),
                                                          Post.status.in_(("published", "mock"))))
    failing = s.scalars(select(Source).where(Source.consecutive_failures > 0)).all()
    lines = [
        f"{'PAUSED' if paused else 'running'} | mode {mode} | {'DEV MODE' if settings.env.dev_mode else 'production'}",
        f"last run: {(_aware(last.started_at).astimezone(ZoneInfo(settings.automation['timezone'])).strftime('%d %b %H:%M') if last else 'never')}"
        + (f" ok" if last and last.ok else (" FAILED" if last else "")),
        f"pending {pending} | queued {queued} | posted 24h {posted24}",
    ]
    if last and last.stats:
        st = last.stats
        lines.append(f"last cycle: {st.get('items_new', 0)} new items, {st.get('stories_new', 0)} new stories, {st.get('drafts_made', 0)} drafts")
    if failing:
        lines.append("failing: " + ", ".join(f"{f.key} x{f.consecutive_failures}" for f in failing[:8]))
    return "\n".join(lines)


def growth_text(s: Session, settings: Settings) -> str:
    snaps = s.scalars(select(GrowthSnapshot).order_by(GrowthSnapshot.at.desc()).limit(30)).all()
    if not snaps:
        return "No growth numbers yet. /growth <followers> <verified_impressions_90d>"
    g = settings.raw.get("growth", {})
    tf, ti = int(g.get("follower_target", 500)), int(g.get("impressions_target_90d", 500000))
    cur = snaps[0]
    lines = [f"Followers {cur.followers}/{tf} ({max(tf - cur.followers, 0)} to go)"]
    if cur.verified_impressions_90d is not None:
        lines.append(f"Verified impressions 90d {cur.verified_impressions_90d:,}/{ti:,}")
    older = [x for x in snaps if (_aware(cur.at) - _aware(x.at)).days >= 7]
    if older:
        prev = older[0]
        days = max((_aware(cur.at) - _aware(prev.at)).days, 1)
        rate = (cur.followers - prev.followers) / days
        lines.append(f"{rate:+.1f} followers/day over last {days} days")
        if rate > 0 and cur.followers < tf:
            lines.append(f"At this rate the follower target is ~{int((tf - cur.followers) / rate)} days away (estimate, not a promise)")
    lines.append("These are manual entries from X analytics; targets are configurable, verify against X's current program rules.")
    return "\n".join(lines)


def digest_text(s: Session, settings: Settings, now: datetime) -> str:
    since = now - timedelta(days=7)
    posts = s.scalars(select(Post).where(Post.created_at >= since, Post.status.in_(("published", "mock")))).all()
    stories = s.scalar(select(func.count(Story.id)).where(Story.first_seen_at >= since))
    items = s.scalar(select(func.count(Item.id)).where(Item.discovered_at >= since))
    rejected = s.scalar(select(func.count(Draft.id)).where(Draft.decided_at >= since, Draft.status == "rejected"))
    auto = sum(1 for p in posts if p.autonomous)
    cats: dict[str, int] = {}
    for p in posts:
        st = s.get(Story, p.story_id)
        cats[st.category] = cats.get(st.category, 0) + 1
    mix = ", ".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])) or "none"
    top = s.scalars(select(Story).where(Story.first_seen_at >= since).order_by(Story.score.desc()).limit(3)).all()
    lines = [
        f"Week to {now.astimezone(ZoneInfo(settings.automation['timezone'])):%d %b}:",
        f"{items} items -> {stories} stories -> {len(posts)} posts ({auto} overnight), {rejected} rejected",
        f"Mix: {mix}",
        "Top stories: " + "; ".join(f"{t.title[:50]} ({t.score:.0f})" for t in top),
        growth_text(s, settings),
    ]
    return "\n".join(lines)
