"""Find permitted media references inside collected content. Never downloads anything."""
from __future__ import annotations

import re

X_POST_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d{5,25})")
YT_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})")
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)

PUBLIC_DOMAIN_HOSTS = ("nasa.gov", "images-assets.nasa.gov", "esa.int", "noaa.gov", "nih.gov", "usgs.gov")


def find_media(raw_html: str, text: str, publisher_host: str = "") -> list[dict]:
    """Return media refs with a rights status the publisher can act on."""
    blob = f"{raw_html} {text}"
    out: list[dict] = []
    seen: set[str] = set()
    for m in X_POST_RE.finditer(blob):
        url = f"https://x.com/{m.group(1)}/status/{m.group(2)}"
        if url not in seen:
            seen.add(url)
            out.append({"type": "x_post", "url": url, "author": m.group(1), "rights": "quote_ok", "provider": "x"})
    for m in YT_RE.finditer(blob):
        vid = m.group(1)
        url = f"https://www.youtube.com/watch?v={vid}"
        if url not in seen:
            seen.add(url)
            out.append({"type": "youtube", "url": url, "video_id": vid, "rights": "link_ok", "provider": "youtube"})
    for m in IMG_RE.finditer(raw_html or ""):
        url = m.group(1)
        if url in seen or url.startswith("data:"):
            continue
        seen.add(url)
        rights = "public_domain" if any(h in url for h in PUBLIC_DOMAIN_HOSTS) else "unknown"
        out.append({"type": "image", "url": url, "rights": rights, "provider": publisher_host or "article"})
    return out[:10]


def choose_media(media: list[dict], official_authors: set[str] | None = None) -> dict:
    """Pick the publishing mode: quote an official X post, else upload permitted media, else render own clip."""
    official_authors = {a.lower() for a in (official_authors or set())}
    for m in media:
        if m["type"] == "x_post" and (not official_authors or m["author"].lower() in official_authors):
            return {"mode": "quote", "url": m["url"], "rights": "quote_ok"}
    for m in media:
        if m["type"] == "image" and m["rights"] == "public_domain":
            return {"mode": "upload", "url": m["url"], "rights": "public_domain"}
    yt = next((m for m in media if m["type"] == "youtube"), None)
    return {"mode": "render", "url": yt["url"] if yt else "", "rights": "original"}
