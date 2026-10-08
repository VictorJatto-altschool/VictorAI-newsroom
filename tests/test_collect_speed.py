"""Collection latency: sources are fetched in parallel and a late cron start never skips a cycle."""
from __future__ import annotations

import threading
import time
from datetime import timedelta

from sqlalchemy import select

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
    """GitHub cron runs land 25-35 min apart. A 30-min source must be fetched on every one of them."""
    for elapsed in (25, 27, 30, 35):
        cfg, row = _src(last_attempt=now - timedelta(minutes=elapsed))
        assert _due(row, cfg, now), f"{elapsed} min elapsed should be due"
    cfg, row = _src(last_attempt=now - timedelta(minutes=24))
    assert not _due(row, cfg, now), "tolerance is 5 minutes, not a licence to refetch every run"


def test_due_grace_is_capped_so_backoff_still_bites(now):
    cfg, row = _src(failures=1, last_attempt=now - timedelta(minutes=56))  # backoff x2 -> 60 min, grace 5
    assert _due(row, cfg, now)
    cfg, row = _src(failures=1, last_attempt=now - timedelta(minutes=50))
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


def test_fetch_all_cuts_off_a_dead_source_without_delaying_the_others():
    """One source that never answers is reported as failed after the deadline; the rest come back intact."""
    due = [(SourceConfig(name=n, kind="rss", tier=3, category="tech", url=f"https://{n}.test/f"), None, None)
           for n in ("fast-a", "dead", "fast-b")]

    def fetcher(cfg, etag=None, last_modified=None, client=None):
        if cfg.name == "dead":
            time.sleep(3)  # longer than the deadline below; would also exceed any per-read socket timeout
        return FetchResult(ok=True, items=[], error=cfg.name)

    t0 = time.perf_counter()
    results = fetch_all(due, fetcher=fetcher, workers=8, deadline=0.3)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.5, f"a dead source held the cycle for {elapsed:.1f}s"
    assert results[0].ok and results[0].error == "fast-a"
    assert results[2].ok and results[2].error == "fast-b"
    assert not results[1].ok and "budget" in results[1].error


def test_dead_source_backs_off_like_any_failure(fresh_db, settings, now):
    settings.raw["collect"] = {"workers": 4, "source_timeout_seconds": 0.2}

    def fetcher(cfg, etag=None, last_modified=None, client=None):
        if cfg.name == "OpenAI News":
            time.sleep(1.5)
        return FetchResult(ok=True, items=[])

    stats: dict = {}
    with session() as s:
        collect(s, settings, stats, now, fetcher=fetcher)
        row = s.scalar(select(Source).where(Source.name == "OpenAI News"))
        assert stats["sources_failed"] == 1 and row.consecutive_failures == 1 and "budget" in row.last_error


def test_run_once_reports_stage_timings(fresh_db, settings, now):
    from victor.ai.mock import MockProvider
    from victor.channels.console import ConsoleChannel
    from victor.pipeline import run_once
    from victor.publish.mock import MockPublisher

    stats = run_once(settings, now, fetcher=lambda cfg, etag=None, last_modified=None, client=None: FetchResult(ok=True, items=[]),
                     provider=MockProvider(), channel=ConsoleChannel(), publisher=MockPublisher(), check_links=False, dashboard=False)
    t = stats["timings"]
    assert {"collect", "cluster", "score", "draft", "publish", "total"} <= set(t)
    assert all(isinstance(v, float) and v >= 0 for v in t.values())
    assert t["total"] >= max(t["collect"], t["draft"])
