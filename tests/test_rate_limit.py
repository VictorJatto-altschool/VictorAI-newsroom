from victor.ai.base import ProviderError
from victor.channels.console import ConsoleChannel
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_volume import busy_fetcher


class RateLimited:
    name, model = "groq", "x"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, max_tokens=600):
        self.calls += 1
        raise ProviderError("groq http 429: rate limit reached")


def test_rate_limit_stops_drafting_for_the_cycle_and_keeps_stories_eligible(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    p = RateLimited()
    stats = run_once(settings, now, fetcher=busy_fetcher(now), provider=p, channel=ConsoleChannel(), publisher=MockPublisher(), check_links=False)
    assert stats["rate_limited"] is True
    assert p.calls == 1, "one 429 is enough; the other stories must not be hammered"
    assert stats["drafts_failed"] == 1
    from sqlalchemy import select
    from victor.db import session
    from victor.models import Story
    with session() as s:
        assert all(st.status == "discovered" for st in s.scalars(select(Story)).all()), "stories stay eligible for the next cycle"
