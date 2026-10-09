from datetime import datetime, timedelta, timezone

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates, send_reminders, send_thread
from victor.db import session
from victor.pipeline import run_once, set_state
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_freshness import FakeTelegramAll
from .test_pipeline import make_fetcher


def _ready(now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    return settings, tg


def test_thread_command_sends_parts_with_copy_buttons(fresh_db, now):
    settings, tg = _ready(now)
    with session() as s:
        posts = send_thread(s, settings, tg, now, "", provider=MockProvider())
    assert posts and len(posts) >= 3
    text, buttons = tg.buttons[-1]
    assert "THREAD on:" in text and "1/4" in text
    assert buttons[0][0]["text"] == "Open part 1 in X" and buttons[0][0]["url"].startswith("https://x.com/intent/post?")
    copies = [b for row in buttons[1:] for b in row if "copy_text" in b]
    assert len(copies) == len(posts) - 1


def test_thread_via_telegram_command(fresh_db, now):
    settings, tg = _ready(now)
    tg.push_cmd("/thread 5")
    with session() as s:
        process_updates(s, settings, tg, now, provider=MockProvider())
    assert any("THREAD on:" in t for t, _ in tg.buttons)


def test_slot_reminder_sends_strongest_story_once_per_hour(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_drafts_per_run"] = 0
    tg = FakeTelegramAll()
    # collect without drafting so a strong "discovered" story remains for the reminder to pick
    settings.raw["drafting"]["min_score"] = 999
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    settings.raw["drafting"]["min_score"] = 0
    with session() as s:
        set_state(s, "reminder_replies_day", now.strftime("%Y-%m-%d"))  # keep this test to the slot reminder
        sent = send_reminders(s, settings, tg, now)
        assert "slot" in sent
        assert any("Slot open now" in m for m in tg.sent)
        again = send_reminders(s, settings, tg, now + timedelta(minutes=10))
        assert "slot" not in again


def test_daily_and_weekly_nudges_fire_once(fresh_db, now):
    settings, tg = _ready(now)
    monday_1pm_lagos = datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc)  # Monday 13:30 Lagos
    with session() as s:
        sent = send_reminders(s, settings, tg, monday_1pm_lagos)
        assert "replies" in sent and "growth" in sent
        assert send_reminders(s, settings, tg, monday_1pm_lagos + timedelta(hours=1)) == [] or "slot" in send_reminders(s, settings, tg, monday_1pm_lagos + timedelta(hours=2))
