from datetime import timedelta

from victor.collect.fetch import FetchResult, RawItem
from victor.collect.normalize import canonical_url, extract_entities
from victor.config import SourceConfig
from victor.db import session
from victor.pipeline import _resolve_gnews_links, get_state


def _batch(now, n=3):
    cfg = SourceConfig(name="Google News AI", kind="gnews", tier=2, category="ai", url="https://news.google.com/rss/search?q=ai")
    items = []
    for i in range(n):
        w = f"https://news.google.com/rss/articles/CBMi{i}AAAAAA?oc=5"
        items.append(RawItem(None, w, canonical_url(w), f"Story {i}", "", "Pub", "", now, extract_entities("ai")))
    return [cfg], [FetchResult(ok=True, items=items)]


def test_refused_batch_pauses_resolution_then_resumes(fresh_db, now):
    calls = []

    def refusing(urls):
        calls.append(len(urls)); return {u: u for u in urls}

    def working(urls):
        calls.append(len(urls)); return {u: "https://pub.example/" + u[-12:-5] for u in urls}

    with session() as s:
        cfgs, res = _batch(now)
        assert _resolve_gnews_links(s, cfgs, res, 20, resolver=refusing, now=now) == 0
        assert get_state(s, "gnews_backoff_until", "")  # paused
        cfgs, res = _batch(now)
        assert _resolve_gnews_links(s, cfgs, res, 20, resolver=working, now=now + timedelta(minutes=30)) == 0
        assert calls == [3]  # the second batch never reached Google
        cfgs, res = _batch(now)
        assert _resolve_gnews_links(s, cfgs, res, 20, resolver=working, now=now + timedelta(minutes=100)) == 3
        assert get_state(s, "gnews_backoff_until", "") == ""  # cleared once it works again
