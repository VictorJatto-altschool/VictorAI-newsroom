"""Fetch feeds. Conditional GET, timeouts, one failure never stops the run."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

from ..config import SourceConfig
from .normalize import (
    canonical_url,
    clean_text,
    clean_title,
    extract_entities,
    gnews_unwrap_publisher,
    host_of,
    parse_time,
)

log = logging.getLogger(__name__)
UA = "VictorNewsroom/0.1 (+https://x.com/SI4blog_; polite RSS collector)"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)  # per operation; the 15 s total budget per source is enforced in pipeline.fetch_all
FETCH_DEADLINE = 15.0  # seconds a single source may take in total, connect + every read


@dataclass
class RawItem:
    external_id: str | None
    original_url: str
    canonical_url: str
    title: str
    summary: str
    publisher: str
    author: str
    published_at: datetime | None
    entities: list[str]
    raw_html: str = ""


@dataclass
class FetchResult:
    ok: bool
    items: list[RawItem]
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    error: str | None = None


def make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True)


def fetch_source(
    src: SourceConfig,
    etag: str | None = None,
    last_modified: str | None = None,
    client: httpx.Client | None = None,
) -> FetchResult:
    own = client is None
    client = client or make_client()
    try:
        if src.kind == "hf":
            return _fetch_hf(src, client)
        return _fetch_feed(src, client, etag, last_modified)
    except Exception as e:  # noqa: BLE001  one bad source must not stop the run
        log.warning("fetch failed %s: %s", src.name, e)
        return FetchResult(ok=False, items=[], error=f"{type(e).__name__}: {e}"[:300])
    finally:
        if own:
            client.close()


def parse_feed_bytes(src: SourceConfig, content: bytes) -> list[RawItem]:
    """Parse feed bytes into RawItems. Separated so tests can run without network."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.bozo_exception}")
    items: list[RawItem] = []
    default_pub = clean_text(parsed.feed.get("title", "")) or host_of(src.feed_url())
    for e in parsed.entries:
        link = e.get("link") or ""
        if not link:
            continue
        publisher = gnews_unwrap_publisher(e) if src.kind == "gnews" else default_pub
        title = clean_title(e.get("title"), publisher)
        content_blocks = e.get("content") or []
        summary_html = e.get("summary") or (content_blocks[0].get("value", "") if content_blocks else "")
        summary = clean_text(summary_html)[:2000]
        raw_html = (e.get("summary") or "") + " ".join(c.get("value", "") for c in content_blocks)
        items.append(
            RawItem(
                external_id=e.get("id") or e.get("guid"),
                original_url=link,
                canonical_url=canonical_url(link),
                title=title,
                summary=summary,
                publisher=publisher,
                author=clean_text(e.get("author", ""))[:200],
                published_at=parse_time(e),
                entities=extract_entities(f"{publisher}. {title}. {summary[:400]}"),
                raw_html=raw_html[:20000],
            )
        )
    return items


def _fetch_feed(src: SourceConfig, client: httpx.Client, etag, last_modified) -> FetchResult:
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    r = client.get(src.feed_url(), headers=headers)
    if r.status_code == 304:
        return FetchResult(ok=True, items=[], not_modified=True, etag=etag, last_modified=last_modified)
    r.raise_for_status()
    items = parse_feed_bytes(src, r.content)
    return FetchResult(ok=True, items=items, etag=r.headers.get("ETag"), last_modified=r.headers.get("Last-Modified"))


def _fetch_hf(src: SourceConfig, client: httpx.Client) -> FetchResult:
    r = client.get(src.url)
    r.raise_for_status()
    data = json.loads(r.content)
    items: list[RawItem] = []
    for m in data:
        mid = m.get("modelId") or m.get("id")
        if not mid:
            continue
        url = f"https://huggingface.co/{mid}"
        created = m.get("createdAt") or m.get("lastModified")
        try:
            pub = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
        except ValueError:
            pub = None
        tags = ", ".join((m.get("tags") or [])[:8])
        items.append(
            RawItem(
                external_id=mid,
                original_url=url,
                canonical_url=canonical_url(url),
                title=f"{mid} is trending on Hugging Face",
                summary=f"Trending model {mid}. Downloads: {m.get('downloads', 0)}. Likes: {m.get('likes', 0)}. Tags: {tags}",
                publisher="Hugging Face",
                author=mid.split("/")[0],
                published_at=pub or datetime.now(timezone.utc),
                entities=extract_entities(mid.replace("/", " ").replace("-", " ")),
            )
        )
    return FetchResult(ok=True, items=items)
