"""Turn a raw feed entry into clean, comparable fields. No network, no AI."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from time import mktime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "s", "igshid", "cmpid", "ncid",
}
_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
_TITLE_SUFFIX = re.compile(r"\s+[-|–—]\s+[^-|–—]{2,60}$")
STOP = {
    "the", "this", "that", "with", "from", "after", "just", "new", "breaking", "here", "what", "when", "how", "why",
    "introducing", "announcing", "meet", "inside", "report", "review", "update", "updates", "news", "blog", "video",
    "watch", "read", "your", "their", "these", "those", "first", "best", "today", "live",
}


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragment and tracking params, strip trailing slash."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":443") or host.endswith(":80"):
        host = host.rsplit(":", 1)[0]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = _TAGS.sub(" ", html.unescape(s))
    return _WS.sub(" ", s).strip()


def clean_title(title: str | None, publisher: str = "") -> str:
    t = clean_text(title)
    if publisher and t.lower().endswith(publisher.lower()):
        t = _TITLE_SUFFIX.sub("", t)
    return t.strip(" -|–—")


def parse_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(mktime(st), tz=timezone.utc)
            except (OverflowError, ValueError):
                continue
    return None


def host_of(url: str) -> str:
    try:
        h = urlsplit(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except ValueError:
        return ""


_PUB_NOISE = {"news", "blog", "blogs", "updates", "ai", "the", "official", "newsroom", "feed", "com", "inc", "gov", "org"}


def normalize_publisher(name: str) -> str:
    """'OpenAI News', 'OpenAI', 'openai.com', 'NASA (.gov)' and 'AI News | TechCrunch' collapse to one publisher each."""
    name = re.sub(r"\([^)]*\)", " ", name or "")
    if "|" in name:
        name = name.split("|")[-1]
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    core = [w for w in words if w not in _PUB_NOISE]
    return " ".join(core) or " ".join(words)


def gnews_unwrap_publisher(entry) -> str:
    src = entry.get("source")
    if isinstance(src, dict):
        return clean_text(src.get("title", ""))
    return ""


ENTITY_HINTS = [
    "OpenAI", "Anthropic", "Claude", "ChatGPT", "GPT-5", "GPT-4", "Gemini", "DeepMind", "Google", "Microsoft",
    "Copilot", "Meta", "Llama", "xAI", "Grok", "NVIDIA", "Nvidia", "Hugging Face", "Mistral", "DeepSeek",
    "Perplexity", "Apple", "Amazon", "AWS", "Tesla", "SpaceX", "NASA", "Starship", "Falcon", "Cursor", "Midjourney",
    "Runway", "ElevenLabs", "Stability AI", "Adobe", "Qwen", "Alibaba", "Samsung", "Intel", "AMD", "TSMC", "ARM",
    "Oracle", "Salesforce", "GitHub", "Reddit", "YouTube", "TikTok", "Netflix", "Sora", "Veo", "Imagen",
    "Stable Diffusion", "Crew Dragon", "ISS", "Artemis", "Blue Origin", "Boeing", "Waymo", "Uber", "Figure",
    "Optimus", "Boston Dynamics",
]
_ENTITY_RE = re.compile(r"\b(" + "|".join(re.escape(e) for e in sorted(ENTITY_HINTS, key=len, reverse=True)) + r")\b")
_TOKEN = r"[A-Z][a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*"  # GPT-6, Claude-4, RTX-5090
_CAPS_RE = re.compile(rf"\b({_TOKEN}(?:\s+{_TOKEN}){{0,2}})\b")


def extract_entities(text: str) -> list[str]:
    """Cheap entity extraction: known names plus capitalised runs. Enough for clustering."""
    found: dict[str, None] = {}
    for m in _ENTITY_RE.finditer(text):
        found[m.group(1)] = None
    for m in _CAPS_RE.finditer(text):
        w = m.group(1)
        if len(w) > 3 and w.lower() not in STOP:
            found[w] = None
        # Model and product codes inside a run stand on their own: "Introducing GPT-6" -> "GPT-6"
        for tok in w.split():
            if (any(ch.isdigit() for ch in tok) or "-" in tok) and len(tok) > 2 and tok.lower() not in STOP:
                found[tok] = None
    return list(found)[:14]
