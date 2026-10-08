"""CLI: python -m victor <command>."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import timezone

from sqlalchemy import func, select

from .config import load_settings
from .db import init_engine, session
from .models import Draft, Event, Item, Note, Post, Run, Source, Story
from .pipeline import event, get_state, run_once, set_state, utcnow


def _setup(args) -> tuple:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    settings = load_settings()
    init_engine(settings.env.database_url)
    return settings


def cmd_run(args):
    settings = _setup(args)
    if settings.env.dev_mode:
        print("[DEV MODE] running without production keys; AI/Telegram/X use mocks where keys are missing")
    stats = run_once(settings, check_links=not args.no_link_check)
    for k, v in stats.items():
        print(f"  {k:<18} {v}")


def cmd_status(args):
    settings = _setup(args)
    with session() as s:
        last = s.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
        print(f"mode: {get_state(s, 'mode', settings.automation['mode'])}   paused: {get_state(s, 'paused', 'false')}   dev_mode: {settings.env.dev_mode}")
        if last:
            print(f"last run: {last.started_at} ok={last.ok} stats={last.stats}")
        counts = {
            "sources": s.scalar(select(func.count(Source.id))),
            "items": s.scalar(select(func.count(Item.id))),
            "stories": s.scalar(select(func.count(Story.id))),
            "drafts_pending": s.scalar(select(func.count(Draft.id)).where(Draft.status == "pending")),
            "posts": s.scalar(select(func.count(Post.id))),
        }
        print(counts)
        failing = s.scalars(select(Source).where(Source.consecutive_failures > 0)).all()
        for f in failing:
            print(f"  source failing: {f.name} x{f.consecutive_failures}: {f.last_error}")


def cmd_stories(args):
    _setup(args)
    with session() as s:
        rows = s.scalars(select(Story).order_by(Story.score.desc()).limit(args.limit)).all()
        for st in rows:
            print(f"#{st.id:<4} {st.score:5.1f} {st.classification:<10} {st.status:<10} src={st.source_count} t1={st.tier1_count} [{st.category}] {st.title[:80]}")


def cmd_drafts(args):
    _setup(args)
    with session() as s:
        rows = s.scalars(select(Draft).order_by(Draft.id.desc()).limit(args.limit)).all()
        for d in rows:
            print(f"#{d.id:<4} {d.status:<10} route={d.route:<10} checks={'ok' if d.checks_passed else 'FAIL'} story={d.story_id} {d.provider}\n    {d.text[:200].replace(chr(10), ' / ')}\n")


def _decide(args, status: str):
    _setup(args)
    with session() as s:
        d = s.get(Draft, args.draft_id)
        if not d:
            sys.exit(f"draft {args.draft_id} not found")
        if status != "rejected" and not d.checks_passed:
            sys.exit(f"draft {args.draft_id} failed checks: {[k for k, v in d.checks.items() if k != 'passed' and not v.get('ok')]}")
        d.status = status
        d.decided_at = utcnow()
        event(s, f"draft_{status}", "draft", d.id, by="cli")
        print(f"draft {d.id} -> {status}")


def cmd_approve(args):
    _decide(args, "approved")


def cmd_night(args):
    _decide(args, "approved_night")


def cmd_reject(args):
    _decide(args, "rejected")


def cmd_pause(args):
    _setup(args)
    with session() as s:
        set_state(s, "paused", "true")
        event(s, "paused", by="cli")
    print("paused: no automatic publishing until `resume`")


def cmd_resume(args):
    _setup(args)
    with session() as s:
        set_state(s, "paused", "false")
        event(s, "resumed", by="cli")
    print("resumed")


def cmd_mode(args):
    _setup(args)
    with session() as s:
        set_state(s, "mode", args.mode)
        event(s, "mode_changed", mode=args.mode, by="cli")
    print(f"mode -> {args.mode}")


def cmd_note(args):
    _setup(args)
    with session() as s:
        s.add(Note(text=args.text))
    print("note saved")


def cmd_posted(args):
    _setup(args)
    with session() as s:
        rows = s.scalars(select(Post).order_by(Post.id.desc()).limit(args.limit)).all()
        for p in rows:
            print(f"#{p.id:<4} {p.status:<10} auto={p.autonomous} {p.remote_url or ''} {p.error or ''}\n    {p.text[:160].replace(chr(10), ' / ')}")


def cmd_events(args):
    _setup(args)
    with session() as s:
        for e in s.scalars(select(Event).order_by(Event.id.desc()).limit(args.limit)).all():
            print(f"{e.at.replace(tzinfo=timezone.utc):%Y-%m-%d %H:%M} {e.kind:<16} {e.ref_type or '':<6} {e.ref_id or '':<5} {e.detail}")


def cmd_story(args):
    """Hand the newsroom a link you found: it becomes a story and is drafted right away."""
    from .collect.manual import ingest_url
    from .pipeline import draft_one

    settings = _setup(args)
    with session() as s:
        story = ingest_url(s, settings, args.url, note=args.note or "", category=args.category)
        d = draft_one(s, settings, story, utcnow(), official_only_quote=False)
        if d is None:
            sys.exit("could not draft (provider failed)")
        print(f"draft #{d.id} route={d.route} media={d.media.get('mode')} {d.media.get('url', '')}")


def cmd_serve(args):
    """Serve the static dashboard on localhost."""
    import http.server
    import functools

    from .config import ROOT

    _setup(args)
    docs = ROOT / "docs"
    if not (docs / "index.html").exists():
        from .dashboard import write_dashboard

        with session() as s:
            write_dashboard(s, load_settings())
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(docs))
    print(f"dashboard: http://localhost:{args.port}/   (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


def cmd_loop(args):
    """Run a cycle every N minutes while the laptop is on, with Telegram polled in between."""
    import time

    from .ai.draft import pick_provider
    from .channels.commands import process_updates
    from .channels.telegram import TelegramChannel

    settings = _setup(args)
    tg = TelegramChannel(settings.env.telegram_bot_token, settings.env.telegram_chat_id) if settings.env.has_telegram else None
    provider = pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    print(f"loop: cycle every {args.minutes} min; Telegram {'on' if tg else 'off'}; Ctrl+C to stop")
    while True:
        try:
            stats = run_once(settings)
            print(f"{utcnow():%H:%M} cycle ok: {stats.get('items_new', 0)} new items, {stats.get('drafts_made', 0)} drafts, "
                  f"{stats.get('posts_published', 0)} published, {stats.get('posts_mock', 0)} simulated")
        except Exception as e:  # noqa: BLE001
            print(f"{utcnow():%H:%M} cycle FAILED: {type(e).__name__}: {e}")
        deadline = time.time() + args.minutes * 60
        while time.time() < deadline:
            if tg:
                with session() as s:
                    process_updates(s, settings, tg, provider=provider)
            time.sleep(3)


def cmd_dashboard(args):
    from .dashboard import write_dashboard

    settings = _setup(args)
    with session() as s:
        p = write_dashboard(s, settings)
    print(p)


def cmd_render(args):
    """Render the card (and clip if ffmpeg exists) for a draft so you can look at it before anything posts."""
    from .media.fetch import prepare_media

    settings = _setup(args)
    with session() as s:
        d = s.get(Draft, args.draft_id)
        if not d:
            sys.exit(f"draft {args.draft_id} not found")
        pub = settings.raw.get("publishing", {})
        media = prepare_media({"mode": "render"}, d.story.title, d.text, d.story.category,
                              f"@{pub.get('x_username', '')}", want_clip=not args.image_only)
        d.media = {**(d.media or {}), **media}
    print(f"{media.get('kind', 'image')}: {media.get('path')}")


def cmd_telegram(args):
    """Long-poll Telegram locally so buttons and commands work while you test (Ctrl+C to stop)."""
    import time

    from .ai.draft import pick_provider
    from .channels.commands import process_updates
    from .channels.telegram import TelegramChannel

    settings = _setup(args)
    if not settings.env.has_telegram:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set")
    tg = TelegramChannel(settings.env.telegram_bot_token, settings.env.telegram_chat_id)
    provider = pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    tg.notify("Newsroom console attached. /help for commands.")
    print("listening for Telegram commands; Ctrl+C to stop")
    while True:
        with session() as s:
            n = process_updates(s, settings, tg, provider=provider)
        if n:
            print(f"handled {n} update(s)")
        time.sleep(2)


def cmd_test_ai(args):
    from .ai.draft import SourceView, generate, pick_provider

    settings = _setup(args)
    provider = pick_provider(settings.env, settings.drafting.get("provider_order", ["mock"]))
    print(f"provider: {provider.name} ({provider.model})")
    src = [SourceView(0, "NASA", 1, "NASA's Crew-12 returns to Earth after 237 days in orbit",
                      "Crew-12 splashed down in the Pacific Ocean after a 237-day mission aboard the International Space Station.",
                      "https://www.nasa.gov/")]
    out = generate(provider, settings.voice, src[0].title, "space", src)
    print(out.text, "\n--\nwhy:", out.why, "\nreason:", out.reason)


def cmd_test_x(args):
    settings = _setup(args)
    if not settings.env.has_x:
        sys.exit("X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET are not all set")
    import tweepy

    e = settings.env
    client = tweepy.Client(consumer_key=e.x_api_key, consumer_secret=e.x_api_secret,
                           access_token=e.x_access_token, access_token_secret=e.x_access_secret)
    try:
        me = client.get_me()
        print(f"authenticated as @{me.data.username} (id {me.data.id})")
        print("publishing.enabled in settings.yaml:", settings.raw.get("publishing", {}).get("enabled", False))
    except tweepy.errors.TweepyException as err:
        sys.exit(f"X API rejected the credentials or the tier does not allow this call: {err}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="victor", description="Victor AI & Tech newsroom")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one newsroom cycle")
    r.add_argument("--no-link-check", action="store_true")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    for name, fn in (("stories", cmd_stories), ("drafts", cmd_drafts), ("posted", cmd_posted), ("events", cmd_events)):
        sp = sub.add_parser(name)
        sp.add_argument("--limit", type=int, default=20)
        sp.set_defaults(fn=fn)
    for name, fn in (("approve", cmd_approve), ("night", cmd_night), ("reject", cmd_reject)):
        sp = sub.add_parser(name)
        sp.add_argument("draft_id", type=int)
        sp.set_defaults(fn=fn)
    sub.add_parser("pause").set_defaults(fn=cmd_pause)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)
    m = sub.add_parser("mode")
    m.add_argument("mode", choices=["manual", "approval", "restricted_autonomous"])
    m.set_defaults(fn=cmd_mode)
    n = sub.add_parser("note")
    n.add_argument("text")
    n.set_defaults(fn=cmd_note)
    sub.add_parser("dashboard", help="write the static dashboard to docs/index.html").set_defaults(fn=cmd_dashboard)
    st = sub.add_parser("story", help="draft a story from a link you found")
    st.add_argument("url")
    st.add_argument("--note", default="")
    st.add_argument("--category", default="tech")
    st.set_defaults(fn=cmd_story)
    sv = sub.add_parser("serve", help="serve the dashboard at http://localhost:8787/")
    sv.add_argument("--port", type=int, default=8787)
    sv.set_defaults(fn=cmd_serve)
    lp = sub.add_parser("loop", help="run cycles continuously while the laptop is on")
    lp.add_argument("--minutes", type=int, default=20)
    lp.set_defaults(fn=cmd_loop)
    rd = sub.add_parser("render", help="render the card/clip for a draft")
    rd.add_argument("draft_id", type=int)
    rd.add_argument("--image-only", action="store_true")
    rd.set_defaults(fn=cmd_render)
    sub.add_parser("telegram", help="long-poll Telegram for buttons and commands").set_defaults(fn=cmd_telegram)
    sub.add_parser("test-ai", help="draft one sample post with the configured provider").set_defaults(fn=cmd_test_ai)
    sub.add_parser("test-x", help="check X credentials").set_defaults(fn=cmd_test_x)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
