from datetime import timedelta

from victor.ai.connect import ANGLES, generate_connect_post
from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.pipeline import get_state

from .test_commands import _settings
from .test_freshness import FakeTelegramAll


def test_generate_connect_post_is_clean():
    text = generate_connect_post(MockProvider(), "voice", ANGLES[0], max_chars=500)
    assert 0 < len(text) <= 500 and "http" not in text and "#" not in text


def test_connect_command_sends_post_with_buttons_and_rotates(fresh_db, now):
    settings = _settings()
    tg = FakeTelegramAll()
    tg.push_cmd("/connect")
    tg.push_cmd("/connect robotics")
    with session() as s:
        process_updates(s, settings, tg, now + timedelta(minutes=1), provider=MockProvider())
        assert get_state(s, "connect_count", "0") == "2"
    cards = [(t, b) for t, b in tg.buttons if t.startswith("CONNECT POST")]
    assert len(cards) == 2 and cards[0][0] != cards[1][0]
    text, buttons = cards[0]
    assert buttons[0][0]["url"].startswith("https://x.com/intent/post?text=")
    assert "copy_text" in buttons[1][0]
