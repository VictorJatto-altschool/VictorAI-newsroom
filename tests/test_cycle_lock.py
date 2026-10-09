from datetime import timedelta

from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.db import session
from victor.pipeline import _acquire_cycle_lock, run_once, set_state
from victor.publish.mock import MockPublisher

from .test_pipeline import make_fetcher


def test_second_cycle_is_skipped_while_first_holds_the_lock(fresh_db, settings, now):
    settings.raw["publishing"]["prepare_media"] = False
    assert _acquire_cycle_lock(now)  # simulate a cycle in progress elsewhere
    stats = run_once(settings, now + timedelta(minutes=1), fetcher=make_fetcher(now), provider=MockProvider(),
                     channel=ConsoleChannel(), publisher=MockPublisher(), check_links=False)
    assert stats.get("skipped")


def test_stale_lock_expires(fresh_db, settings, now):
    settings.raw["publishing"]["prepare_media"] = False
    with session() as s:
        set_state(s, "cycle_lock", (now - timedelta(minutes=30)).isoformat())
    stats = run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(),
                     publisher=MockPublisher(), check_links=False)
    assert not stats.get("skipped") and stats["items_new"] == 6
