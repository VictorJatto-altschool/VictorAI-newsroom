from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.collect.manual import ingest_url
from victor.db import session
from victor.models import Draft, Story
from victor.pipeline import draft_one

X_URL = "https://x.com/WhiteHouse/status/1975900000000000000"
PAGE_X = {"title": "The White House on X: President Trump presents Elon Musk with the National Medal of Science",
          "description": "President Trump presents Elon Musk with the National Medal of Science at the White House.",
          "html": "", "publisher": "The White House (X)", "x_post": X_URL, "author": "WhiteHouse"}


def test_operator_x_link_is_quoted_and_political_story_is_reviewed(fresh_db, settings, now):
    settings.raw["drafting"]["check_links"] = False
    settings.raw["publishing"]["prepare_media"] = False
    with session() as s:
        story = ingest_url(s, settings, X_URL, note="angle: what the medal is and who got it before", category="tech",
                           now=now, page=PAGE_X)
        assert story.tier1_count == 0 and story.source_count == 1  # an X post is not an official newsroom source
        d = draft_one(s, settings, story, now, provider=MockProvider(), channel=ConsoleChannel(), official_only_quote=False)
        assert d is not None
        assert d.media["mode"] == "quote" and d.media["url"] == X_URL
        assert d.route == "review"  # "president" is a blocked keyword: never autonomous
        assert "[DEV MODE]" in d.text and d.status == "pending"


def test_same_link_twice_reuses_story(fresh_db, settings, now):
    with session() as s:
        a = ingest_url(s, settings, X_URL, now=now, page=PAGE_X)
        b = ingest_url(s, settings, X_URL, now=now, page=PAGE_X)
        assert a.id == b.id and len(s.scalars(select(Story)).all()) == 1


def test_article_link_official_host_is_tier1(fresh_db, settings, now):
    page = {"title": "NASA, Energy Department Advance Nuclear-Powered Exploration", "description": "NASA and DOE signed an MOU.",
            "html": '<a href="https://x.com/NASA/status/1975911111111111111">post</a>', "publisher": "nasa.gov", "x_post": "", "author": ""}
    with session() as s:
        story = ingest_url(s, settings, "https://www.nasa.gov/news-release/nasa-doe/", now=now, page=page, category="space")
        assert story.tier1_count == 1
        d = draft_one(s, settings, story, now, provider=MockProvider(), channel=ConsoleChannel(), check_links=False)
        assert d.media["mode"] == "quote" and "NASA" in d.media["url"]
