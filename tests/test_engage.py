from datetime import timedelta

from victor.ai.draft import SourceView
from victor.ai.mock import MockProvider
from victor.ai.reply import generate_replies
from victor.channels.commands import people_message, process_updates, send_reminders
from victor.db import session
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_freshness import FakeTelegramAll
from .test_volume import busy_fetcher


def test_generate_replies_one_per_story():
    v = [SourceView(1, "Publisher", 1, "Title", "Summary", "https://example.com/a")]
    out = generate_replies(MockProvider(), "voice", [("Story A", "ai", v), ("Story B", "space", v)])
    assert len(out) == 2 and all(0 < len(r) <= 240 and "http" not in r for r in out)


def test_engage_sends_targets_with_copy_buttons(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    before = len(tg.buttons)
    tg.push_cmd("/engage 3")
    with session() as s:
        process_updates(s, settings, tg, now + timedelta(minutes=1), provider=MockProvider())
    cards = [(t, b) for t, b in tg.buttons[before:] if t.startswith("REPLY TARGET")]
    assert 1 <= len(cards) <= 3
    text, buttons = cards[0]
    assert "Suggested reply:" in text
    assert buttons[0][0]["url"].startswith("http")
    assert buttons[1][0]["copy_text"]["text"].startswith("[DEV MODE] Reply 1")
    assert any("follow back" in m for m in tg.sent)


def test_people_message_links_to_x_people_search():
    text, buttons = people_message(_settings())
    urls = [b["url"] for row in buttons for b in row]
    assert all(u.startswith("https://x.com/search?q=") for u in urls)
    assert sum("f=user" in u for u in urls) >= 6 and "follow back" in text.lower()


def test_reply_reminder_includes_engage(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["reminders"] = {"enabled": True, "slot": False, "replies_at": "00:00", "growth_weekday": 9}
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        sent = send_reminders(s, settings, tg, now + timedelta(minutes=1))
    assert "replies" in sent
    assert any(t.startswith("REPLY TARGET") for t, _ in tg.buttons)
    assert any("Reply window" in m for m in tg.sent)
    with session() as s:  # once a day only
        assert "replies" not in send_reminders(s, settings, tg, now + timedelta(minutes=2))
