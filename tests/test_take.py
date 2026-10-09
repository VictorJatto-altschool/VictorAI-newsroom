"""Approve asks for the operator's own line, and that line goes into the post (X Original Content rules)."""
from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.models import Draft, Post
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_intent import FakeTelegramIntent
from .test_pipeline import make_fetcher


def _setup(now, require_take=True):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["publishing"]["require_take"] = require_take
    tg = FakeTelegramIntent()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        return settings, tg, d.id, d.channel_ref


def test_approve_asks_for_take_then_hands_off_with_it(fresh_db, now):
    settings, tg, did, ref = _setup(now)
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        assert s.get(Draft, did).status == "pending"
    prompt_text, prompt_buttons = tg.buttons[-1]
    flat = [b for row in prompt_buttons for b in row]
    assert "ONE line" in prompt_text
    assert any(b.get("callback_data") == f"skiptake:{did}" for b in flat)
    assert any("copy_text" in b for b in flat), "the suggested take is copyable with one tap"
    tg.push_cmd("My take: this is the first time a lab has shipped a 2M window to everyone on day one.")
    with session() as s:
        process_updates(s, settings, tg, now)
        d = s.get(Draft, did)
        assert d.status == "handed_off" and d.take.startswith("My take")
        assert s.scalar(select(Post).where(Post.draft_id == did)).status == "manual"
    text, buttons = tg.buttons[-1]
    assert "My take: this is the first time" in text
    assert "My+take" in buttons[0][0]["url"]  # the take travels into the X composer (form-encoded spaces)


def test_skip_posts_without_take(fresh_db, now):
    settings, tg, did, ref = _setup(now)
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
    tg.push_cmd("skip")
    with session() as s:
        process_updates(s, settings, tg, now)
        d = s.get(Draft, did)
        assert d.status == "handed_off" and d.take == ""


def test_require_take_off_hands_off_immediately(fresh_db, now):
    settings, tg, did, ref = _setup(now, require_take=False)
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        assert s.get(Draft, did).status == "handed_off"
