from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import clear_chat, process_updates
from victor.db import session
from victor.intelligence.rules import PostingHistory, next_slot
from victor.models import BotMessage, Draft
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_expiry import FakeTelegramDel
from .test_pipeline import make_fetcher

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
LIMITS = {"max_posts_per_day": 12, "max_posts_per_hour": 1, "min_gap_minutes": 90}


def test_next_slot_now_when_nothing_posted():
    assert next_slot(NOW, LIMITS, PostingHistory()) == NOW


def test_next_slot_respects_gap_hour_and_day():
    h = PostingHistory(posted_at=[NOW - timedelta(minutes=10)])
    assert next_slot(NOW, LIMITS, h) == NOW + timedelta(minutes=80)  # 90-minute gap wins over the hourly rule
    day = PostingHistory(posted_at=[NOW - timedelta(hours=i * 2) for i in range(12)])  # 12 in the last day
    assert next_slot(NOW, LIMITS, day) == NOW - timedelta(hours=22) + timedelta(days=1)


def test_queued_approval_says_when(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["apply_to_manual"] = True
    settings.raw["limits"]["max_posts_per_hour"] = 1
    from tests.test_intent import FakeTelegramIntent

    tg = FakeTelegramIntent()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft)); did, ref = d.id, d.channel_ref
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)  # first post: handed off at once
    text, _ = tg.buttons[-1]
    assert "Next slot opens at" in text
    with session() as s:  # a second approved draft within the hour is queued with a time
        from victor.models import Story
        st = Story(title="Second story", category="tech", entities=[], source_count=2, score=70, classification="hot", status="drafted")
        s.add(st); s.flush()
        d2 = Draft(story=st, text="Second post text that is fine.", provider="mock", checks={"passed": True}, checks_passed=True,
                   status="pending", channel_ref="555", created_at=now)
        s.add(d2); s.flush(); d2id = d2.id
    tg.push_button(f"approve:{d2id}", "555")
    with session() as s:
        process_updates(s, settings, tg, now + timedelta(minutes=5))
    assert any("Next hand-off at" in m for m in tg.sent)


def test_clearchat_deletes_everything_but_handoffs(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramDel()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        s.add(BotMessage(message_id="901", kind="sendMessage", created_at=now))
        s.add(BotMessage(message_id="902", kind="sendMessage", created_at=now))
        d = s.scalar(select(Draft)); d.status = "handed_off"; d.channel_ref = "902"
    with session() as s:
        n = clear_chat(s, settings, tg, now + timedelta(minutes=1))
    assert n == 1 and "901" in tg.deleted and "902" not in tg.deleted
