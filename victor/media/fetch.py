"""Obtain a media file for a draft according to its rights-checked media mode.

upload  -> download a public-domain image (NASA etc.) to data/media
render  -> make our own card + clip
quote   -> nothing to fetch; the X post is quoted
Optional free lookups: NASA image library (no key), YouTube Data API (key) for an official video link in the reply.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from ..collect.normalize import normalize_publisher
from ..config import DATA_DIR
from .extract import PUBLIC_DOMAIN_HOSTS
from .render import facts_from_text, render_card, render_clip

log = logging.getLogger(__name__)
MEDIA_DIR = DATA_DIR / "media"
_SAFE = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SAFE.sub("-", s.lower()).strip("-")[:60] or "media"


def download_public_domain(url: str, name: str, client: httpx.Client | None = None) -> Path | None:
    """Only hosts on the public-domain allowlist are ever downloaded."""
    if not any(h in url for h in PUBLIC_DOMAIN_HOSTS):
        log.info("refusing to download non-public-domain media: %s", url)
        return None
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    try:
        r = client.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        ext = ".png" if "png" in ctype else ".jpg"
        if len(r.content) > 5_000_000:
            log.info("image too large for X upload: %s", url)
            return None
        p = MEDIA_DIR / f"{_slug(name)}{ext}"
        p.write_bytes(r.content)
        return p
    except httpx.HTTPError as e:
        log.warning("media download failed %s: %s", url, e)
        return None
    finally:
        if own:
            client.close()


def nasa_image_search(query: str, client: httpx.Client | None = None) -> dict | None:
    """NASA image library, public domain, no key needed."""
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        r = client.get("https://images-api.nasa.gov/search", params={"q": query, "media_type": "image", "page_size": 5})
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        for it in items:
            links = it.get("links") or []
            data = (it.get("data") or [{}])[0]
            if links:
                return {"type": "image", "url": links[0]["href"], "rights": "public_domain", "provider": "nasa",
                        "title": data.get("title", "")}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("nasa search failed: %s", e)
    finally:
        if own:
            client.close()
    return None


def youtube_official_video(query: str, api_key: str, official_names: set[str], client: httpx.Client | None = None) -> dict | None:
    """YouTube Data API search; only returns a hit whose channel matches a tier-1 publisher name."""
    if not api_key:
        return None
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = client.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": query, "type": "video", "order": "date", "maxResults": 10, "key": api_key})
        r.raise_for_status()
        for it in r.json().get("items", []):
            sn = it.get("snippet", {})
            if normalize_publisher(sn.get("channelTitle", "")) in official_names:
                vid = it["id"]["videoId"]
                return {"type": "youtube", "url": f"https://www.youtube.com/watch?v={vid}", "video_id": vid,
                        "rights": "link_ok", "provider": "youtube", "channel": sn.get("channelTitle", "")}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("youtube search failed: %s", e)
    finally:
        if own:
            client.close()
    return None


def prepare_media(draft_media: dict, headline: str, post_text: str, category: str, handle: str,
                  want_clip: bool = True) -> dict:
    """Return the media dict with a local `path` filled in where a file is needed. Never raises."""
    media = dict(draft_media or {})
    mode = media.get("mode", "render")
    try:
        if mode == "render" and category == "space" and not media.get("url"):
            hit = nasa_image_search(headline)  # public domain, no key
            if hit:
                media.update(mode="upload", url=hit["url"], rights="public_domain", provider="nasa", title=hit.get("title", ""))
                mode = "upload"
        if mode == "upload" and media.get("url"):
            p = download_public_domain(media["url"], headline)
            if p:
                media["path"] = str(p)
                return media
            mode = "render"
        if mode == "render":
            card = render_card(headline, facts_from_text(post_text), handle, category.replace("_", " "),
                               MEDIA_DIR / f"{_slug(headline)}.png")
            clip = render_clip(card) if want_clip else None
            media.update(mode="render", path=str(clip or card), rights="original", kind="video" if clip else "image")
    except Exception as e:  # noqa: BLE001  media must never block a post
        log.warning("media preparation failed: %s", e)
        media.setdefault("path", "")
    return media
