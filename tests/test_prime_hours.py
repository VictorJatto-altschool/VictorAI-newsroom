from datetime import datetime, timedelta, timezone

from victor.intelligence.rules import PostingHistory, in_prime, limits_ok, next_slot

LIMITS = {"max_posts_per_day": 12, "max_posts_per_hour": 1, "min_gap_minutes": 90,
          "prime_hours": {"start": "13:00", "end": "01:00"}, "off_hours_max_posts": 3}
TZ = "Africa/Lagos"  # UTC+1
NIGHT = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)   # 04:00 Lagos: off hours
DAY = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)    # 15:00 Lagos: prime


def test_prime_window_crosses_midnight():
    assert in_prime(DAY, LIMITS, TZ)
    assert in_prime(datetime(2026, 10, 9, 23, 30, tzinfo=timezone.utc), LIMITS, TZ)  # 00:30 Lagos
    assert not in_prime(NIGHT, LIMITS, TZ)


def test_off_hours_cap_defers_to_prime_start():
    # 02:30, 01:00 and 10:00 UTC the day before: all outside 13:00-01:00 Lagos
    three_at_night = PostingHistory(posted_at=[NIGHT - timedelta(minutes=30), NIGHT - timedelta(hours=2), NIGHT - timedelta(hours=17)])
    assert "off_hours_cap" in limits_ok(NIGHT, LIMITS, three_at_night, TZ)
    nxt = next_slot(NIGHT, LIMITS, three_at_night, TZ)
    assert nxt == datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)  # 13:00 Lagos
    assert "off_hours_cap" not in limits_ok(DAY, LIMITS, three_at_night, TZ)


def test_default_reply_carries_the_link(fresh_db, settings, now):
    from victor.ai.mock import MockProvider
    from victor.channels.console import ConsoleChannel
    from victor.db import session
    from victor.models import Draft
    from victor.pipeline import run_once
    from victor.publish.mock import MockPublisher
    from sqlalchemy import select
    from .test_pipeline import make_fetcher

    settings.raw["publishing"]["prepare_media"] = False
    assert settings.raw["publishing"]["link_in_post"] is False
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(), publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        assert "http" not in d.text and d.reply_text.startswith("Source: https://")
