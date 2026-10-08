"""Collection latency: sources are fetched in parallel and a late cron start never skips a cycle."""
from __future__ import annotations

import threading
import time
from datetime import timedelta

from victor.collect.fetch import FetchResult, RawItem
from victor.collect.normalize import canonical_url, extract_entities
from victor.config import SourceConfig
from victor.db import session
from victor.models import Source
from victor.pipeline import _due, collect, fetch_all


def _src(name="OpenAI News", interval=30, failures=0, last_attempt=None):
    cfg = SourceConfig(name=name, kind="rss", tier=1, category="ai_models", url="https://example.test/feed", interval_minutes=interval)
    row = Source(key=cfg.key, name=name, kind="rss", url=cfg.url, tier=1, category="ai_models", enabled=True,
                 consecutive_failures=failures, last_attempt_at=last_attempt)
    return cfg, row


def test_due_tolerates_cron_jitter(now):
    """Run 1 started at :04, run 2 at :31 -> 27 min elapsed. The 30-min source must still be fetched."""
    cfg, row = _src(last_attempt=now - timedelta(minutes=27))
    assert _due(row, cfg, now)
    cfg, row = _src(last_attempt=now - timedelta(minutes=15))
    assert not _due(row, cfg, now), "grace is a few minutes, not a licence to refetch every run"


def test_due_grace_is_capped_so_backoff_still_bites(now):
    cfg, row = _src(failures=1, last_attempt=now - timedelta(minutes=52))  # backoff x2 -> 60 min, grace 10
    assert _due(row, cfg, now)
    cfg, row = _src(failures=1, last_attempt=now - timedelta(minutes=45))
    assert not _due(row, cfg, now)
    cfg, row = _src(interval=4, last_attempt=now - timedelta(minutes=2))  # grace capped at half the interval
    assert _due(row, cfg, now)
    cfg, row = _src(interval=4, last_attempt=now - timedelta(minutes=1))
    assert not _due(row, cfg, now)


def test_fetch_all_runs_sources_concurrently_and_keeps_order():
    names = [f"Feed {i}" for i in range(8)]
    due = [(SourceConfig(name=n, kind="rss", tier=3, category="tech", url=f"https://{i}.test/f"), None, None)
           for i, n in enumerate(names)]
    active, peak, lock = 0, 0, threading.Lock()

    def slow_fetcher(cfg, etag=None, last_modified=None, client=None):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.15)
        with lock:
            active -= 1
        return FetchResult(ok=True, items=[], error=cfg.name)

    t0 = time.perf_counter()
    results = fetch_all(due, fetcher=slow_fetcher, workers=8)
    elapsed = time.perf_counter() - t0
    assert [r.error for r in results] == names, "results come back in watch-list order"
    assert peak > 1, "fetches overlapped"
    assert elapsed < 0.15 * len(due) / 2, f"took {elapsed:.2f}s, barely faster than serial"


def test_fetch_all_single_worker_still_works():
    due = [(SourceConfig(name="A", kind="rss", tier=3, category="tech", url="https://a.test/f"), None, None)]
    res = fetch_all(due, fetcher=lambda cfg, etag=None, last_modified=None, client=None: FetchResult(ok=True, items=[]), workers=1)
    assert len(res) == 1 and res[0].ok
    assert fetch_all([], fetcher=None) == []


def test_collect_dedupes_within_a_run_and_against_the_db(fresh_db, settings, now):
    url = "https://openai.com/index/same-story"

    def fetcher(cfg, etag=None, last_modified=None, client=None):
        if cfg.name not in ("OpenAI News", "TechCrunch AI"):
            return FetchResult(ok=True, items=[])
        raw = RawItem(None, url, canonical_url(url), "OpenAI ships a new model today", "OpenAI ships a new model today",
                      "OpenAI", "", now - timedelta(minutes=10), extract_entities("OpenAI. new model"))
        return FetchResult(ok=True, items=[raw])

    stats: dict = {}
    with session() as s:
        new = collect(s, settings, stats, now, fetcher=fetcher)
        assert len(new) == 1 and stats["items_seen"] == 2 and stats["items_new"] == 1
        s.commit()
    stats = {}
    with session() as s:
        new = collect(s, settings, stats, now + timedelta(minutes=31), fetcher=fetcher)
        assert new == [] and stats["items_new"] == 0 and stats["sources_checked"] > 0
