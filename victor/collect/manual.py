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


_HREF = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
_PARA = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SKIP_HOSTS = ("x.com", "twitter.com", "t.co/i/", "pic.twitter.com", "pic.x.com", "publish.twitter.com")


_ARTICLE = re.compile(r"<article[^>]*>(.*?)</article>", re.I | re.S)
_PROMO = ("register now", "book exhibit", "get your pass", "subscribe", "sign up", "newsletter", "cookie", "by clicking",
          "logo", "toggle", "search submit", "read more", "all rights reserved", "terms of service")


def article_excerpt(page: str, limit: int = 1500) -> str:
    """The opening paragraphs of an article, tags stripped: enough for the model to know what actually happened."""
    body = _ARTICLE.search(page)
    scope = body.group(1) if body else page  # the <article> element when the site has one, else the whole page
    out: list[str] = []
    total = 0
    for raw in _PARA.findall(scope):
        text = clean_text(htmllib.unescape(_TAG.sub(" ", raw)))
        low = text.lower()
        if len(text) < 60 or any(p in low for p in _PROMO):
            continue
        words = text.split()
        shouting = sum(1 for w in words if w.isupper() and len(w) > 2)
        if shouting >= 4 and shouting / max(1, len(words)) > 0.25:
            continue  # menus and calls to action shout; article paragraphs only name the odd CNN or NASA
        out.append(text)
        total += len(text)
        if total >= limit:
            break
    return "\n".join(out)[:limit]


_ZW = re.compile(r"[​‌‍⁠﻿]")
_BARE_URL = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(/[^\s<>\"'…]*)", re.I)


def _linked_article(oembed_html: str, client: httpx.Client) -> str:
    """The article an X post links to. Empty when the post links nowhere outside X.

    oEmbed gives the picture link as a real hyperlink but shows the article address only as text, with
    zero-width characters inside it, so both the hrefs and the bare addresses in the text are tried."""
    candidates: list[str] = []
    for href in _HREF.findall(oembed_html or ""):
        if "t.co/" in href or not any(h in href for h in _SKIP_HOSTS):
            candidates.append(href)
    text = _ZW.sub("", _TAG.sub(" ", htmllib.unescape(oembed_html or "")))
    for host, path in _BARE_URL.findall(text):
        url = f"https://{host}{path}"
        if not any(h in url for h in _SKIP_HOSTS) and "." in host and len(path) > 1:
            candidates.append(url)
    for cand in candidates:
        try:
            r = client.get(cand)
            final = str(r.url)
        except httpx.HTTPError:
            continue
        if r.status_code >= 400 or any(h in final for h in _SKIP_HOSTS):
            continue
        return final
    return ""


def fetch_page(url: str, client: httpx.Client | None = None, _depth: int = 0) -> dict:
    """Title, description, raw html for a URL. X post URLs go through the public oEmbed endpoint.

    An X post that links to an article (an outlet's teaser for its own story) is read as that article: the
    article is the source, the X post stays as the quoted reference so its preview or video plays."""
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
            x_post = f"https://x.com/{m.group(1)}/status/{m.group(2)}"
            post = {"title": f"{author} on X: {text[:140]}", "description": text, "html": data.get("html", ""),
                    "publisher": f"{author} (X)", "x_post": x_post, "author": m.group(1), "article_url": ""}
            article_url = _linked_article(data.get("html", ""), client) if _depth == 0 else ""
            if article_url:
                try:
                    art = fetch_page(article_url, client, _depth=1)
                except httpx.HTTPError as e:
                    log.info("linked article unreadable (%s); using the X post alone", e)
                    return post
                desc = art["description"]
                if art.get("excerpt"):
                    desc = f"{desc}\n\nFrom the article:\n{art['excerpt']}"
                desc += f"\n\nThe X post by {author} that linked to it said: {text[:300]}"
                return {"title": art["title"], "description": desc[:2400], "html": art["html"], "publisher": art["publisher"],
                        "x_post": x_post, "author": m.group(1), "article_url": article_url}
            return post
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
        return {"title": title, "description": desc, "html": page[:200000], "publisher": host_of(url), "x_post": "", "author": "",
                "article_url": "", "excerpt": article_excerpt(page)}
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
    # An outlet's X post that links to its article is filed under the article: that is the story, and it is
    # how the same article arriving through the outlet's feed is recognised as the same item.
    link = page.get("article_url") or url
    curl = canonical_url(link)
    existing = s.scalar(select(Item).where(Item.canonical_url == curl))
    if existing and existing.story:
        story = existing.story
        story.status = "discovered"  # allow a fresh draft on operator request
        if page.get("x_post") and not any(m.get("type") == "x_post" for m in (existing.media or [])):
            existing.media = [{"type": "x_post", "url": page["x_post"], "author": page["author"], "rights": "quote_ok",
                               "provider": "x"}] + list(existing.media or [])
        return story
    host = host_of(link)
    src = _operator_source(s, host)
    title = page["title"][:300]
    summary = (page.get("description") or "")[:2400]
    if page.get("excerpt") and "From the article" not in summary:
        summary = f"{summary}\n\nFrom the article:\n{page['excerpt']}"[:2400]
    if note:
        summary = f"{summary}\n\nOperator note: {note}"[:2400]
    media = find_media(page.get("html", ""), summary, host)
    if page.get("x_post"):
        media.insert(0, {"type": "x_post", "url": page["x_post"], "author": page["author"], "rights": "quote_ok", "provider": "x"})
    item = Item(source=src, canonical_url=curl, original_url=link, title=title, summary=summary,
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
