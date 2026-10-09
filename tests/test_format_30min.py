from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.models import Draft
from victor.pipeline import get_state, run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_freshness import FakeTelegramAll
from .test_volume import busy_fetcher


def _fmt():
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"].update(max_drafts_per_run=1, card_every_minutes=30, apply_to_manual=False)
    return settings


def test_one_card_per_30_minutes_strongest_first(fresh_db, now):
    settings = _fmt()
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    assert len([m for m in tg.sent if m.startswith("DRAFT#")]) == 1
    with session() as s:
        assert get_state(s, "last_card_at", "")
    run_once(settings, now + timedelta(minutes=10), fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg,
             publisher=MockPublisher(), check_links=False)
    assert len([m for m in tg.sent if m.startswith("DRAFT#")]) == 1  # too soon: no second card
    run_once(settings, now + timedelta(minutes=31), fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg,
             publisher=MockPublisher(), check_links=False)
    assert len([m for m in tg.sent if m.startswith("DRAFT#")]) == 2


def test_approve_becomes_ready_post_at_once_with_take_button(fresh_db, now):
    settings = _fmt()
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft)); did, ref = d.id, d.channel_ref
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now + timedelta(minutes=1))
        assert s.get(Draft, did).status == "handed_off"
    text, buttons = tg.buttons[-1]
    assert text.startswith("READY TO POST") and "Next slot opens" not in text
    assert any(b.get("copy_text") for row in buttons for b in row), "the suggested take is offered as a Copy button"


def test_story_command_is_a_ready_post_immediately(fresh_db, now, monkeypatch):
    settings = _fmt()
    tg = FakeTelegramAll()
    from victor import pipeline
    from victor.collect import manual
    from victor.models import Story

    def fake_ingest(s, settings, url, note="", now=None):
        st = Story(title="Operator story about a rocket", category="space", entities=["rocket"], source_count=1, score=70,
                   classification="hot", status="discovered")
        s.add(st); s.flush()
        return st

    def fake_draft_one(s, settings, story, now, provider=None, channel=None, **kw):
        d = Draft(story=story, text="Operator post text that is fine.", provider="mock", checks={"passed": True}, checks_passed=True,
                  status="pending", channel_ref="777", created_at=now, suggested_take="My take.")
        s.add(d); s.flush()
        return d

    monkeypatch.setattr(manual, "ingest_url", fake_ingest)
    monkeypatch.setattr(pipeline, "draft_one", fake_draft_one)
    tg.push_cmd("/story https://example.com/rocket")
    with session() as s:
        process_updates(s, settings, tg, now, provider=MockProvider())
        d = s.scalar(select(Draft))
        assert d is not None and d.status == "handed_off"
    assert any(t.startswith("READY TO POST") for t, _ in tg.buttons)
