"""The news sets the pace: every trending story is drafted; manual hand-offs are not throttled."""
from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.collect.fetch import FetchResult, RawItem
from victor.collect.normalize import canonical_url, extract_entities
from victor.db import session
from victor.models import Draft, Post
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_intent import FakeTelegramIntent

COMPANIES = ["OpenAI", "NASA", "NVIDIA", "Anthropic", "SpaceX", "Google", "Microsoft", "Meta", "GitHub", "Hugging Face", "Mistral", "DeepSeek"]


def busy_fetcher(now):
    """12 distinct stories, each with an official source plus two press sources."""
    def item(url, title, pub, when):
        return RawItem(None, url, canonical_url(url), title, title, pub, "", when, extract_entities(f"{pub}. {title}"))

    def fetcher(cfg, etag=None, last_modified=None, client=None):
        items = []
        for n, co in enumerate(COMPANIES):
            title = f"{co} launches Model-{n} with a {n + 1}M token context window"
            when = now - timedelta(minutes=30 + n)
            if cfg.name == "OpenAI News":
                items.append(item(f"https://{co.lower().replace(' ', '')}.com/news/model-{n}", title, co, when))
            elif cfg.name == "TechCrunch AI":
                items.append(item(f"https://techcrunch.com/{co.lower()}-model-{n}", f"{co} unveils Model-{n}: {n + 1}M context", "TechCrunch", when))
            elif cfg.name == "The Verge AI":
                items.append(item(f"https://theverge.com/{co.lower()}-model-{n}", f"{co}'s Model-{n} arrives with {n + 1}M token context", "The Verge", when))
        return FetchResult(ok=True, items=items)

    return fetcher


def test_every_trending_story_is_drafted_and_sent(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_drafts_per_run"] = 0  # uncapped for this test: every qualifying story goes out
    tg = FakeTelegramIntent()
    stats = run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg,
                     publisher=MockPublisher(), check_links=False)
    assert stats["stories_new"] == 12
    assert stats["drafts_made"] == 12, stats
    assert sum(1 for m in tg.sent if m.startswith("DRAFT#")) == 12


def test_per_cycle_cap_takes_the_strongest_first_and_keeps_the_rest(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_drafts_per_run"] = 6
    stats = run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=FakeTelegramIntent(),
                     publisher=MockPublisher(), check_links=False)
    assert stats["drafts_made"] == 6
    from victor.models import Story
    with session() as s:
        assert len(s.scalars(select(Story).where(Story.status == "discovered")).all()) == 6  # next cycle's work


def test_manual_handoffs_are_not_throttled(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_drafts_per_run"] = 0
    settings.raw["limits"]["apply_to_manual"] = False
    tg = FakeTelegramIntent()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        drafts = s.scalars(select(Draft).order_by(Draft.id)).all()
        refs = [(d.id, d.channel_ref) for d in drafts]
    for did, ref in refs:  # approve all twelve within the same minute
        tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        handed = s.scalars(select(Post).where(Post.status == "manual")).all()
        assert len(handed) == 12
    assert sum(1 for t, _ in tg.buttons if "READY TO POST" in t) == 12
