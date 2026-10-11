from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates, send_reply_for_link
from victor.db import session

from .test_commands import _settings
from .test_freshness import FakeTelegramAll

PAGE = {"title": "What to know about the landmark Warner Bros. deal", "description": "Paramount agreed to buy WBD for $110 billion.",
        "html": "", "publisher": "techcrunch.com", "x_post": "https://x.com/TechCrunch/status/1976000000000000000",
        "author": "TechCrunch", "article_url": "https://techcrunch.com/2026/10/11/wbd/"}


def test_reply_for_link_sends_one_reply_with_copy_button(fresh_db, now):
    settings = _settings()
    tg = FakeTelegramAll()
    with session() as s:
        reply = send_reply_for_link(s, settings, tg, now, "https://x.com/TechCrunch/status/1976000000000000000",
                                    note="focus on what it means for streaming prices", provider=MockProvider(), page=PAGE)
    assert reply and len(reply) <= 240
    text, buttons = tg.buttons[-1]
    assert text.startswith("REPLY to: What to know about the landmark Warner Bros. deal")
    assert buttons[0][0]["url"] == PAGE["x_post"] and buttons[1][0]["copy_text"]["text"] == reply


def test_reply_command_without_link_explains_usage(fresh_db, now):
    settings = _settings()
    tg = FakeTelegramAll()
    tg.push_cmd("/reply")
    with session() as s:
        process_updates(s, settings, tg, now, provider=MockProvider())
    assert any(m.startswith("Usage: /reply") for m in tg.sent)
