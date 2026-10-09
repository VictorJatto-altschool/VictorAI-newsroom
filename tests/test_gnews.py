import base64

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.collect.fetch import FetchResult, RawItem
from victor.collect.gnews import decode_old_format, is_gnews, resolve_gnews
from victor.collect.normalize import canonical_url, extract_entities
from victor.db import session
from victor.models import Item
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher


def test_old_format_decodes_embedded_url():
    payload = b"\x08\x13\x22" + bytes([30]) + b"https://techcrunch.com/2026/story" + b"\xd2\x01\x00"
    gid = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert decode_old_format(gid) == "https://techcrunch.com/2026/story"
    assert resolve_gnews(f"https://news.google.com/rss/articles/{gid}?oc=5", client=None) == "https://techcrunch.com/2026/story"


def test_non_google_links_pass_through():
    assert not is_gnews("https://openai.com/x")
    assert resolve_gnews("https://openai.com/x") == "https://openai.com/x"


def test_collect_resolves_wrappers_and_dedupes_against_publisher(fresh_db, settings, now):
    settings.raw["publishing"]["prepare_media"] = False
    wrapper = "https://news.google.com/rss/articles/CBMiAAAAAA?oc=5"
    real = "https://openai.com/index/gpt-6"

    def fetcher(cfg, etag=None, last_modified=None, client=None):
        if cfg.name == "OpenAI News":
            return FetchResult(ok=True, items=[RawItem(None, real, canonical_url(real), "Introducing GPT-6: a 2M token context window",
                                                      "GPT-6 ships", "OpenAI", "", now, extract_entities("OpenAI GPT-6"))])
        if cfg.name == "Google News AI labs":
            return FetchResult(ok=True, items=[RawItem(None, wrapper, canonical_url(wrapper), "Introducing GPT-6: a 2M token context window",
                                                      "GPT-6 ships", "OpenAI", "", now, extract_entities("OpenAI GPT-6"))])
        return FetchResult(ok=True, items=[])

    stats = run_once(settings, now, fetcher=fetcher, provider=MockProvider(), channel=ConsoleChannel(), publisher=MockPublisher(),
                     check_links=False, resolver=lambda urls: {u: real for u in urls})
    assert stats["gnews_resolved"] == 1
    with session() as s:
        urls = [i.original_url for i in s.scalars(select(Item)).all()]
        assert urls == [real], "the wrapper resolved to the publisher's address and was recognised as the same item"
