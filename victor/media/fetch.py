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


def nasa_video_search(query: str, max_bytes: int = 45_000_000, client: httpx.Client | None = None) -> dict | None:
    """Real NASA footage, public domain. Returns the smallest mp4 rendition of the best match."""
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    try:
        r = client.get("https://images-api.nasa.gov/search", params={"q": query, "media_type": "video", "page_size": 5})
        r.raise_for_status()
        for it in r.json().get("collection", {}).get("items", []):
            data = (it.get("data") or [{}])[0]
            manifest = client.get(it.get("href", ""))
            if manifest.status_code != 200:
                continue
            files = [u for u in manifest.json() if u.lower().endswith(".mp4")]
            for pref in ("~mobile.mp4", "~preview.mp4", "~small.mp4", ".mp4"):
                url = next((u for u in files if u.lower().endswith(pref)), None)
                if url:
                    head = client.head(url)
                    size = int(head.headers.get("content-length", "0") or 0)
                    if 0 < size <= max_bytes:
                        return {"type": "video", "url": url, "rights": "public_domain", "provider": "nasa",
                                "title": data.get("title", ""), "bytes": size}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("nasa video search failed: %s", e)
    finally:
        if own:
            client.close()
    return None


def download_public_domain_video(url: str, name: str, client: httpx.Client | None = None) -> Path | None:
    if not any(h in url for h in PUBLIC_DOMAIN_HOSTS):
        return None
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    try:
        r = client.get(url)
        r.raise_for_status()
        p = MEDIA_DIR / f"{_slug(name)}-footage.mp4"
        p.write_bytes(r.content)
        return p
    except httpx.HTTPError as e:
        log.warning("video download failed %s: %s", url, e)
        return None
    finally:
        if own:
            client.close()


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


LEADERS = [
    "Elon Musk", "Donald Trump", "Sam Altman", "Jensen Huang", "Mark Zuckerberg", "Satya Nadella", "Sundar Pichai",
    "Dario Amodei", "Demis Hassabis", "Tim Cook", "Jeff Bezos", "Greg Brockman", "Mira Murati", "Ilya Sutskever",
    "Yann LeCun", "Andrej Karpathy", "Arthur Mensch", "Aravind Srinivas", "Alexandr Wang", "Lisa Su", "Pat Gelsinger",
    "Brett Adcock", "Peter Beck", "Marc Raibert", "Jim Fan", "Masayoshi Son", "Liang Wenfeng", "Jack Ma", "Robin Li",
    "Cristiano Amon", "Rene Haas", "Elizabeth Warren", "JD Vance", "Ursula von der Leyen", "Narendra Modi", "Bola Tinubu",
]
_OK_LICENSES = ("public domain", "pd", "cc0", "cc by", "cc-by", "cc by-sa", "cc-by-sa", "attribution")


def person_in(text: str) -> str | None:
    """Whole-word match on the full name; surname alone only when it is long enough to be unambiguous."""
    low = (text or "").lower()
    for name in LEADERS:
        if re.search(rf"\b{re.escape(name.lower())}\b", low):
            return name
    for name in LEADERS:
        surname = name.split()[-1].lower()
        if len(surname) >= 5 and re.search(rf"\b{re.escape(surname)}\b", low):
            return name
    return None


def wiki_person_image(name: str, client: httpx.Client | None = None) -> dict | None:
    """Lead photo of the person's Wikipedia article, only if its Commons licence allows reuse. Attribution recorded."""
    import html as htmllib
    from urllib.parse import unquote

    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "VictorNewsroom/0.1 (+https://x.com/SI4blog_)"})
    try:
        s = client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{name.replace(' ', '_')}").json()
        src = (s.get("originalimage") or {}).get("source")
        if not src:
            return None
        title = unquote(src.split("?")[0].rsplit("/", 1)[-1])  # drop tracking query, keep the Commons file name
        meta = client.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "titles": f"File:{title}", "prop": "imageinfo", "iiprop": "extmetadata|url", "format": "json"}).json()
        page = next(iter(meta.get("query", {}).get("pages", {}).values()), {})
        info = (page.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata", {})
        lic = ext.get("LicenseShortName", {}).get("value", "")
        if not lic or not any(k in lic.lower() for k in _OK_LICENSES):
            return None
        artist = re.sub(r"<[^>]+>", "", htmllib.unescape(ext.get("Artist", {}).get("value", ""))).strip()[:60]
        thumb = (s.get("thumbnail") or {}).get("source", src)
        url = info.get("url") or src
        attribution = f"Photo: {artist + ', ' if artist else ''}{lic}, via Wikimedia Commons"
        return {"type": "image", "url": url, "rights": "cc", "license": lic, "artist": artist, "attribution": attribution,
                "provider": "wikimedia", "thumb": thumb}
    except (httpx.HTTPError, ValueError, KeyError, StopIteration) as e:
        log.info("wiki image lookup failed for %s: %s", name, e)
        return None
    finally:
        if own:
            client.close()


def download_licensed(url: str, name: str, client: httpx.Client | None = None) -> Path | None:
    """Download a Wikimedia Commons file we have already licence-checked."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "VictorNewsroom/0.1 (+https://x.com/SI4blog_)"})
    try:
        r = client.get(url)
        r.raise_for_status()
        if len(r.content) > 5_000_000:
            return None
        ext = ".png" if "png" in r.headers.get("content-type", "") else ".jpg"
        p = MEDIA_DIR / f"{_slug(name)}-photo{ext}"
        p.write_bytes(r.content)
        return p
    except httpx.HTTPError as e:
        log.warning("licensed download failed %s: %s", url, e)
        return None
    finally:
        if own:
            client.close()


def prepare_media(draft_media: dict, headline: str, post_text: str, category: str, handle: str,
                  want_clip: bool = True, lookup_person=wiki_person_image, lookup_video=None,
                  allow_render: bool = True, allow_video_upload: bool = False) -> dict:
    """allow_render=False: the post already has a link with its own preview, so if no real media is found,
    return mode 'link' instead of rendering a card."""
    if lookup_video is None:
        lookup_video = nasa_video_search
    """Return the media dict with a local `path` filled in where a file is needed. Never raises."""
    media = dict(draft_media or {})
    mode = media.get("mode", "render")
    try:
        if mode in ("render", "upload") and category == "space" and lookup_video is not None and allow_video_upload:
            vid = lookup_video(headline)  # real NASA footage first: native video beats everything for reach
            if vid:
                p = download_public_domain_video(vid["url"], headline)
                if p:
                    media.update(mode="upload", url=vid["url"], rights="public_domain", path=str(p), kind="video",
                                 provider="nasa", title=vid.get("title", ""))
                    return media
        if mode == "render" and category == "space" and not media.get("url"):
            hit = nasa_image_search(headline)  # public domain, no key
            if hit:
                media.update(mode="upload", url=hit["url"], rights="public_domain", provider="nasa", title=hit.get("title", ""))
                mode = "upload"
        if mode == "render" and not media.get("url"):
            person = person_in(headline) or person_in(post_text)
            hit = lookup_person(person) if person else None  # real photo, licence-checked, attribution kept
            if hit:
                p = download_licensed(hit["url"], headline)
                if p:
                    media.update(mode="upload", url=hit["url"], rights="cc", path=str(p), kind="image",
                                 attribution=hit["attribution"], license=hit["license"], provider="wikimedia")
                    return media
        if mode == "upload" and media.get("url"):
            p = download_public_domain(media["url"], headline)
            if p:
                media["path"] = str(p)
                return media
            mode = "render"
        if mode == "render" and not allow_render:
            media.update(mode="link", path="", rights="n/a", kind="link")
            return media
        if mode == "render":
            card = render_card(headline, facts_from_text(post_text), handle, category.replace("_", " "),
                               MEDIA_DIR / f"{_slug(headline)}.png")
            clip = render_clip(card) if want_clip else None
            media.update(mode="render", path=str(clip or card), rights="original", kind="video" if clip else "image")
    except Exception as e:  # noqa: BLE001  media must never block a post
        log.warning("media preparation failed: %s", e)
        media.setdefault("path", "")
    return media
