"""Resolve Google News wrapper links to the publisher's real article address.

Google News RSS gives `news.google.com/rss/articles/<id>` links. X cannot build a preview card from them,
and the newsroom cannot tell that a wrapper and the publisher's own feed item are the same article.
Two decoders: the old base64 form carries the URL inside the id; the current form needs one page fetch
for a signature and one call to Google's internal endpoint. Anything that fails leaves the link untouched.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

log = logging.getLogger(__name__)
_ID = re.compile(r"news\.google\.com/(?:rss/)?(?:articles|read)/([^/?#]+)")
_SG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_COOKIES = {"CONSENT": "YES+1", "SOCS": "CAI"}


def is_gnews(url: str) -> bool:
    return bool(url) and "news.google.com" in url


def decode_old_format(gid: str) -> str | None:
    """Pre-2024 ids embed the URL as a length-prefixed string."""
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except (ValueError, TypeError):
        return None
    m = re.search(rb"https?://[\x21-\x7e]+", raw)
    if not m:
        return None
    url = m.group(0).decode("ascii", "ignore")
    return url if "." in url else None


def resolve_gnews(url: str, client: httpx.Client | None = None, timeout: float = 15.0) -> str:
    """Return the publisher URL, or the input unchanged when it cannot be resolved."""
    m = _ID.search(url or "")
    if not m:
        return url
    gid = m.group(1)
    old = decode_old_format(gid)
    if old:
        return old
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True, headers=_HEADERS, cookies=_COOKIES)
    try:
        page = client.get(f"https://news.google.com/rss/articles/{gid}", params={"hl": "en-US", "gl": "US", "ceid": "US:en"})
        sg, ts = _SG.search(page.text), _TS.search(page.text)
        if not (sg and ts):
            return url
        req = (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
            f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sg.group(1)}"]'
        )
        resp = client.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data={"f.req": json.dumps([[["Fbv4je", req, None, "generic"]]])},
            headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        chunks = resp.text.split("\n\n")
        if resp.status_code != 200 or len(chunks) < 2:
            raise ValueError(f"batchexecute HTTP {resp.status_code}: {resp.text[:60]!r}")
        parsed = json.loads(chunks[1])
        inner = json.loads(parsed[0][2])
        real = inner[1]
        return real if isinstance(real, str) and real.startswith("http") else url
    except (httpx.HTTPError, ValueError, IndexError, KeyError, TypeError) as e:
        log.info("gnews resolve failed: %s", e)
        return url
    finally:
        if own:
            client.close()


def resolve_many(urls: list[str], workers: int = 2, timeout: float = 15.0, give_up_after: int = 4) -> dict[str, str]:
    """Resolve several wrappers a few at a time. Unresolved ones map to themselves.

    Google refuses the internal endpoint to an address that calls it too often (the whole cloud host shares one).
    When the first `give_up_after` lookups all fail, the rest of the batch is left alone so the block can lift."""
    urls = [u for u in dict.fromkeys(urls) if is_gnews(u)]
    if not urls:
        return {}
    out: dict[str, str] = {}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HEADERS, cookies=_COOKIES) as client:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for start in range(0, len(urls), max(1, workers)):
                chunk = urls[start:start + max(1, workers)]
                for u, real in zip(chunk, pool.map(lambda x: resolve_gnews(x, client, timeout), chunk)):
                    out[u] = real
                done = list(out.items())
                if len(done) >= give_up_after and all(is_gnews(v) for _, v in done):
                    log.warning("gnews: Google is refusing link resolution right now; leaving %d wrappers for later",
                                len(urls) - len(done))
                    break
    return out
