"""End-to-end cycle with a fake fetcher, mock AI, console channel and mock publisher. No network."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.collect.fetch import FetchResult, RawItem
from victor.collect.normalize import canonical_url, extract_entities
from victor.db import session
from victor.models import Draft, Post, Story
from victor.pipeline import publish_approved, run_once
from victor.publish.mock import MockPublisher


def _item(url, title, publisher, published, summary=""):
    return RawItem(None, url, canonical_url(url), title, summary or title, publisher, "", published,
                   extract_entities(f"{publisher}. {title}"),
                   raw_html='<a href="https://x.com/OpenAI/status/1111111111">x</a>')


def make_fetcher(now):
    story_a = [
        ("OpenAI News", "https://openai.com/index/gpt-6", "Introducing GPT-6: a 2M token context window", now - timedelta(minutes=50)),
        ("OpenAI YouTube", "https://www.youtube.com/watch?v=GPT6launch0", "Introducing GPT-6", now - timedelta(minutes=48)),
        ("TechCrunch AI", "https://techcrunch.com/gpt6?utm_source=rss", "OpenAI launches GPT-6 with 2M token context", now - timedelta(minutes=40)),
        ("The Verge AI", "https://theverge.com/gpt6", "OpenAI's GPT-6 arrives with a 2M context window", now - timedelta(minutes=30)),
        ("Google News AI labs", "https://news.google.com/rss/articles/abc", "Introducing GPT-6: a 2M token context window", now - timedelta(minutes=45)),
    ]
    lone = [("Reddit r/artificial", "https://reddit.com/r/artificial/1", "I built a small tool to rename files with AI", now - timedelta(hours=2))]

    def fetcher(cfg, etag=None, last_modified=None, client=None):
        rows = [r for r in story_a + lone if r[0] == cfg.name]
        if cfg.name == "Wired AI":
            return FetchResult(ok=False, items=[], error="boom")  # one failing source must not stop the run
        items = [_item(u, t, p if "Google" not in p else "OpenAI", when) for p, u, t, when in rows]
        for it in items:  # mirror collect(): the item's own URL is part of the media scan
            if "youtube.com" in it.original_url:
                it.raw_html += f" {it.original_url}"
        return FetchResult(ok=True, items=items)

    return fetcher


def test_full_cycle_clusters_scores_drafts_and_mock_publishes(fresh_db, settings, now, capsys):
    stats = run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(),
                     publisher=MockPublisher(), check_links=False)
    assert stats["sources_failed"] == 1 and stats["items_new"] == 6
    with session() as s:
        stories = s.scalars(select(Story).order_by(Story.score.desc())).all()
        top = stories[0]
        assert "GPT-6" in top.title
        assert top.source_count == 3, "Google News copy and OpenAI's own YouTube upload are the same publisher"
        assert top.tier1_count == 2
        assert top.classification in ("hot", "breaking"), top.score_breakdown
        drafts = s.scalars(select(Draft)).all()
        assert len(drafts) == 1 and drafts[0].story_id == top.id
        d = drafts[0]
        assert d.route == "review" and d.status == "pending" and d.checks_passed and d.dev_mode
        assert d.media["mode"] == "quote" and "OpenAI" in d.media["url"]
        assert d.source_item_ids and d.reply_text.startswith("Source: https://openai.com")
        assert "Video: https://www.youtube.com/watch?v=GPT6launch0" in d.reply_text
        assert "[DEV MODE]" in capsys.readouterr().out
        d.status = "approved"
    stats2 = {}
    with session() as s:
        posts = publish_approved(s, settings, stats2, now + timedelta(minutes=5), publisher=MockPublisher(), channel=ConsoleChannel())
        assert len(posts) == 1 and posts[0].status == "mock" and stats2["posts_mock"] == 1
    with session() as s:  # second run never double-submits the same draft version
        again = publish_approved(s, settings, {}, now + timedelta(minutes=10), publisher=MockPublisher(), channel=ConsoleChannel())
        assert again == []
        assert s.scalar(select(Draft)).status == "simulated"
        assert len(s.scalars(select(Post)).all()) == 1


def test_second_run_is_idempotent(fresh_db, settings, now):
    f = make_fetcher(now)
    run_once(settings, now, fetcher=f, provider=MockProvider(), channel=ConsoleChannel(), publisher=MockPublisher(), check_links=False)
    stats = run_once(settings, now + timedelta(hours=1), fetcher=f, provider=MockProvider(), channel=ConsoleChannel(),
                     publisher=MockPublisher(), check_links=False)
    assert stats["items_new"] == 0 and stats["drafts_made"] == 0
    with session() as s:
        assert len(s.scalars(select(Draft)).all()) == 1
