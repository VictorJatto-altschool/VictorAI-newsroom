"""Media priority at hand-off: real media > link with its own preview > rendered card."""
from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.models import Draft
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_intent import FakeTelegramIntent
from .test_pipeline import make_fetcher


def _approve_first(settings, tg, now):
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        d.media = {"mode": "render"}  # pretend no official post was found, so the card would be the fallback
        did, ref = d.id, d.channel_ref
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        return s.get(Draft, did).media


def test_link_with_preview_replaces_rendered_card(fresh_db, now, tmp_path, monkeypatch):
    monkeypatch.setattr("victor.media.fetch.MEDIA_DIR", tmp_path)
    monkeypatch.setattr("victor.pipeline._link_has_preview", lambda url, timeout=8.0: True)
    settings = _settings()
    settings.raw["publishing"]["render_clip"] = False
    settings.raw["publishing"]["link_in_post"] = True
    tg = FakeTelegramIntent()
    media = _approve_first(settings, tg, now)
    assert media["mode"] == "link" and "youtube.com" in media["url"]
    assert tg.files == [], "no card file is sent when the link carries its own preview"


def test_no_preview_means_rendered_card(fresh_db, now, tmp_path, monkeypatch):
    monkeypatch.setattr("victor.media.fetch.MEDIA_DIR", tmp_path)
    monkeypatch.setattr("victor.pipeline._link_has_preview", lambda url, timeout=8.0: False)
    settings = _settings()
    settings.raw["publishing"]["render_clip"] = False
    tg = FakeTelegramIntent()
    media = _approve_first(settings, tg, now)
    assert media["mode"] == "render" and media["path"].endswith(".png")
    assert len(tg.files) == 1
