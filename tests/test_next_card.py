from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.models import Story
from victor.pipeline import get_state, next_card_time, run_once, set_state
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_freshness import FakeTelegramAll
from .test_volume import busy_fetcher


def _fmt():
    settings = _settings()
    settings.raw["publishing"].update(prepare_media=False, auto_handoff=True)
    settings.raw["limits"].update(max_drafts_per_run=1, card_every_minutes=30, apply_to_manual=False)
    return settings


def test_next_card_time_follows_the_rhythm_and_the_cycle(fresh_db, now):
    settings = _fmt()
    with session() as s:
        assert next_card_time(s, settings, now) == now  # nothing sent yet, no cycle known: now
        set_state(s, "last_card_at", now.isoformat())
        set_state(s, "next_cycle_at", (now + timedelta(minutes=10)).isoformat())
        set_state(s, "cycle_minutes", "15")
        # 30 minutes after the last card is 12:30; cycles run 12:10, 12:25, 12:40: the card comes at 12:40
        assert next_card_time(s, settings, now) == now + timedelta(minutes=40)
        set_state(s, "last_card_at", (now - timedelta(hours=1)).isoformat())
        assert next_card_time(s, settings, now) == now + timedelta(minutes=10)  # slot open: the very next cycle


def test_slot_falls_back_to_the_strongest_story_when_nothing_reaches_the_bar(fresh_db, now):
    settings = _fmt()
    settings.raw["drafting"]["min_score"] = 95  # nothing in the fixture reaches this
    settings.raw["drafting"]["fallback_min_score"] = 45
    tg = FakeTelegramAll()
    stats = run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(),
                     check_links=False)
    assert stats["drafts_made"] == 1 and stats.get("fallback_used")
    assert any(t.startswith("READY TO POST") for t, _ in tg.buttons)


def test_empty_slot_sends_one_notice_with_the_next_check_time(fresh_db, now):
    settings = _fmt()
    settings.raw["drafting"]["min_score"] = 95
    settings.raw["drafting"]["fallback_min_score"] = 95
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    notices = [m for m in tg.sent if m.startswith("Slot passed with nothing strong enough")]
    assert len(notices) == 1 and "Next check at" in notices[0]
    run_once(settings, now + timedelta(minutes=15), fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg,
             publisher=MockPublisher(), check_links=False)
    assert len([m for m in tg.sent if m.startswith("Slot passed")]) == 1  # not repeated within the slot


def test_next_command_quotes_the_real_card_time_and_takes_the_slot(fresh_db, now):
    settings = _fmt()
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        set_state(s, "next_cycle_at", (now + timedelta(minutes=5)).isoformat())
    tg.push_cmd("/next")
    with session() as s:
        process_updates(s, settings, tg, now + timedelta(minutes=1), provider=MockProvider())
    msg = [m for m in tg.sent if "Strongest story right now" in m or m.startswith("Next card")][-1]
    assert "Sent above. Next card:" in msg  # a fresh story, not the one already handed over
    assert len([t for t, _ in tg.buttons if t.startswith("READY TO POST")]) == 2
    with session() as s:
        assert get_state(s, "last_card_at", "") == (now + timedelta(minutes=1)).isoformat()  # /next took the slot
        assert len(s.scalars(select(Story).where(Story.status != "discovered")).all()) >= 2
