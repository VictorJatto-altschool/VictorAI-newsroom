"""Operator-submitted stories: a link you saw yourself becomes a story, gets drafted, and the official
X post in it (or the link itself if it is an X post) is used as the quoted reference video.

Nothing here bypasses the rules: the draft still goes through checks and the overnight rule.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..media.extract import X_POST_RE, find_media
from ..models import Item, Source, Story
from .fetch import UA
from .normalize import canonical_url, clean_text, extract_entities, host_of

log = logging.getLogger(__name__)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META = re.compile(r'<meta[^>]+(?:property|name)=["\'](og:title|og:description|description|og:video|og:video:url)["\'][^>]+content=["\']([^"\']*)["\']', re.I)
_META2 = re.compile(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\'](og:title|og:description|description|og:video|og:video:url)["\']', re.I)
OFFICIAL_HOSTS = ("nasa.gov", "whitehouse.gov", ".gov", "openai.com", "anthropic.com", "deepmind.google", "blog.google",
                  "nvidia.com", "microsoft.com", "about.fb.com", "spacex.com", "x.ai", "mistral.ai", "huggingface.co", "github.blog")


def fetch_page(url: str, client: httpx.Client | None = None) -> dict:
    """Title, description, raw html for a URL. X post URLs go through the public oEmbed endpoint."""
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": UA}, follow_redirects=True)
    try:
        m = X_POST_RE.search(url)
        if m:
            r = client.get("https://publish.twitter.com/oembed", params={"url": url, "omit_script": "true"})
            r.raise_for_status()
            data = r.json()
            text = clean_text(data.get("html", ""))
            author = data.get("author_name", m.group(1))
            return {"title": f"{author} on X: {text[:140]}", "description": text, "html": data.get("html", ""),
                    "publisher": f"{author} (X)", "x_post": f"https://x.com/{m.group(1)}/status/{m.group(2)}", "author": m.group(1)}
        r = client.get(url)
        r.raise_for_status()
        page = r.text
        meta: dict[str, str] = {}
        for k, v in _META.findall(page):
            meta.setdefault(k.lower(), htmllib.unescape(v))
        for v, k in _META2.findall(page):
            meta.setdefault(k.lower(), htmllib.unescape(v))
        t = _TITLE.search(page)
        title = clean_text(meta.get("og:title") or (t.group(1) if t else "") or url)
        desc = clean_text(meta.get("og:description") or meta.get("description") or "")
        return {"title": title, "description": desc, "html": page[:200000], "publisher": host_of(url), "x_post": "", "author": ""}
    finally:
        if own:
            client.close()


def _operator_source(s: Session, host: str) -> Source:
    tier = 1 if any(host.endswith(h) or h in host for h in OFFICIAL_HOSTS) else 3
    key = f"operator-{host.replace('.', '-')}"[:120]
    row = s.scalar(select(Source).where(Source.key == key))
    if row is None:
        row = Source(key=key, name=f"Operator link: {host}", kind="manual", url=f"https://{host}", tier=tier,
                     category="other", enabled=True)
        s.add(row)
        s.flush()
    return row


def ingest_url(s: Session, settings: Settings, url: str, note: str = "", category: str = "tech",
               now: datetime | None = None, page: dict | None = None) -> Story:
    """Create (or extend) a story from a link the operator sent. Returns the story, not yet drafted."""
    now = now or datetime.now(timezone.utc)
    page = page or fetch_page(url)
    curl = canonical_url(url)
    existing = s.scalar(select(Item).where(Item.canonical_url == curl))
    if existing and existing.story:
        story = existing.story
        story.status = "discovered"  # allow a fresh draft on operator request
        return story
    host = host_of(url)
    src = _operator_source(s, host)
    title = page["title"][:300]
    summary = (page.get("description") or "")[:2000]
    if note:
        summary = f"{summary}\n\nOperator note: {note}"[:2000]
    media = find_media(page.get("html", ""), summary, host)
    if page.get("x_post"):
        media.insert(0, {"type": "x_post", "url": page["x_post"], "author": page["author"], "rights": "quote_ok", "provider": "x"})
    item = Item(source=src, canonical_url=curl, original_url=url, title=title, summary=summary,
                publisher=page.get("publisher") or host, published_at=now, discovered_at=now,
                entities=extract_entities(f"{title}. {summary[:400]}"), media=media)
    s.add(item)
    story = Story(title=title, category=category, entities=item.entities, first_seen_at=now, last_updated_at=now,
                  source_count=1, tier1_count=1 if src.tier == 1 else 0, score=60.0, classification="trending",
                  status="discovered", score_breakdown={"operator": 60})
    s.add(story)
    item.story = story
    s.flush()
    return story
