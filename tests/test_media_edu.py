from datetime import timedelta

from PIL import Image
from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.db import session
from victor.media.fetch import download_public_domain, prepare_media
from victor.media.render import facts_from_text, render_card
from victor.models import Draft, Note, Story
from victor.pipeline import draft_educational, maybe_send_digest, run_once
from victor.publish.mock import MockPublisher

from .test_pipeline import make_fetcher


def test_render_card_is_square_dark_png(tmp_path):
    p = render_card("NASA's Crew-12 returns to Earth after 237 days in orbit",
                    ["Splashdown in the Pacific this morning", "Longest Crew Dragon rotation yet"],
                    "@SI4blog_", "space", tmp_path / "c.png")
    img = Image.open(p)
    assert img.size == (1080, 1080)
    assert img.getpixel((5, 5)) == (0, 0, 0)


def test_facts_skip_hook_and_boilerplate():
    text = "JUST IN: thing happened.\n\nFact one.\n\nWhy it matters: x.\n\nFor you: y.\n\nFact two."
    assert facts_from_text(text) == ["Fact one.", "Fact two."]


def test_non_public_domain_is_never_downloaded(tmp_path):
    assert download_public_domain("https://techcrunch.com/some.jpg", "x") is None


def test_prepare_media_falls_back_to_render(tmp_path, monkeypatch):
    monkeypatch.setattr("victor.media.fetch.MEDIA_DIR", tmp_path)
    m = prepare_media({"mode": "upload", "url": "https://example.com/not-pd.jpg"}, "Headline here", "Hook\n\nA fact.",
                      "ai_models", "@x", want_clip=False)
    assert m["mode"] == "render" and m["kind"] == "image" and m["path"].endswith(".png")


def test_educational_draft_from_note_only(fresh_db, settings, now):
    settings.raw["publishing"]["prepare_media"] = False
    with session() as s:
        s.add(Note(text="Tried Cursor's agent mode on a Flask app: it wrote the tests first, then fixed two of its own bugs."))
    stats = {}
    with session() as s:
        d = draft_educational(s, settings, stats, now, provider=MockProvider(), channel=ConsoleChannel())
        assert d is not None and d.route == "review" and d.status == "pending"
        assert "Cursor" in d.text and "[DEV MODE]" in d.text
        assert d.story.category == "educational"
        assert s.scalar(select(Note)).used is True
    with session() as s:  # cap per day
        assert draft_educational(s, settings, {}, now + timedelta(hours=2), provider=MockProvider(), channel=ConsoleChannel()) is None


def test_full_run_includes_edu_and_digest(fresh_db, settings, now):
    settings.raw["publishing"]["prepare_media"] = False
    with session() as s:
        s.add(Note(text="Used Gemini to summarise a 40-page PDF; it missed the tables."))
    monday_9am_lagos = now.replace(year=2026, month=10, day=12, hour=8, minute=30)  # 09:30 Lagos
    stats = run_once(settings, monday_9am_lagos, fetcher=make_fetcher(monday_9am_lagos), provider=MockProvider(),
                     channel=ConsoleChannel(), publisher=MockPublisher(), check_links=False)
    assert stats["edu_drafts"] == 1 and stats["digest_sent"] is True
    with session() as s:
        assert maybe_send_digest(s, settings, ConsoleChannel(), monday_9am_lagos) is False  # once per week
        cats = {st.category for st in s.scalars(select(Story)).all()}
        assert "educational" in cats


def test_publish_prepares_render_media(fresh_db, settings, now, tmp_path, monkeypatch):
    monkeypatch.setattr("victor.media.fetch.MEDIA_DIR", tmp_path)
    settings.raw["publishing"]["render_clip"] = False
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(),
             publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        d.media = {"mode": "render"}
        d.status = "approved"
    run_once(settings, now + timedelta(minutes=5), fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(),
             publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        assert d.status == "simulated" and d.media["path"].endswith(".png") and d.media["rights"] == "original"
