from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.db import session
from victor.models import Draft, Post
from victor.pipeline import run_once
from victor.publish.intent import copy_parts
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_freshness import FakeTelegramAll
from .test_volume import busy_fetcher


def test_copy_parts_respects_the_button_limit():
    short = "One short post."
    assert copy_parts(short) == [short]
    long = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(5))
    parts = copy_parts(long)
    assert len(parts) >= 2 and all(0 < len(p) <= 256 for p in parts)
    assert "".join(p.replace(" ", "") for p in parts) == long.replace(" ", "").replace("\n", "")


def test_card_arrives_as_ready_post_without_approve(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"].update(prepare_media=False, auto_handoff=True)
    settings.raw["limits"].update(max_drafts_per_run=1, card_every_minutes=30, apply_to_manual=False)
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    assert not [m for m in tg.sent if m.startswith("DRAFT#")], "no draft card with an Approve button"
    ready = [(t, b) for t, b in tg.buttons if t.startswith("READY TO POST")]
    assert len(ready) == 1
    text, buttons = ready[0]
    labels = [b["text"] for row in buttons for b in row]
    assert "Open in X" in labels and any(l.startswith("Copy") for l in labels) and "Posted" in labels and "Skip" in labels
    with session() as s:
        d = s.scalar(select(Draft))
        assert d.status == "handed_off" and s.scalar(select(Post)).draft_id == d.id
    run_once(settings, now + timedelta(minutes=10), fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg,
             publisher=MockPublisher(), check_links=False)
    assert len([t for t, _ in tg.buttons if t.startswith("READY TO POST")]) == 1  # the 30-minute rhythm still holds
