"""Community posts: invite people in the niche to introduce themselves and connect. No news, no sources, no facts."""
from __future__ import annotations

import json
import re

from .base import AIProvider, ProviderError, clean_post_text

ANGLES = [
    "ask people what they are building or studying in AI, science, tech or space right now",
    "ask which AI lab, company or researcher people are watching most closely this month and why",
    "ask for the one thing people read or watched this week that changed how they think about AI or tech",
    "ask people to introduce themselves in one line: field, city, and the problem they care about",
    "ask which robotics, space or science project people think is underrated right now",
    "ask what people think the next twelve months of AI will change first in their own work",
    "ask which tools or models people actually use every day, not the ones they just talk about",
    "ask what people would like to see more of on this account: breaking news, explainers, or founders' words",
]

# The full voice guide is for news posts and carries example headlines; a community post must not borrow those.
STYLE = (
    "Tone: plain, confident, specific, first person. Short sentences. One blank line between thoughts. "
    "No buzzwords (game-changer, revolutionary, unleash, delve), no exclamation marks, no emoji."
)

CONNECT_SYSTEM = (
    "You write a short community post for the X account Victor AI & Tech, whose readers follow AI, science, "
    "technology, robotics and space news. This post contains NO news: no announcements, no company claims, no "
    "numbers, no product names, no 'why it matters'. It is only about the account and its readers. "
    "The post must (1) open with a specific, honest line about why the account wants to hear from people in that "
    "field, (2) ask ONE clear question that people can answer in a reply, and (3) say plainly, in the first "
    "person, that you follow back and reply to everyone in the field who answers. Warm and professional, in the account's own words, never generic. "
    "Never open with BREAKING, JUST IN or a siren. No URLs, no hashtags, no emoji, no 'follow for more', no 'like "
    "and retweet', no 'drop a follow', no mention of growth, impressions or algorithms, plain characters only, "
    "under {max_chars} characters. "
    'Respond with JSON only: {{"post": string}}\n\n' + STYLE
)
_URL = re.compile(r"https?://\S+")
_BANNED = ("follow for more", "like and retweet", "drop a follow", "#", "http", "why it matters", "announced",
           "breaking", "just in", "just made", "just launched")
_DIGIT = re.compile(r"\d")


def _check(text: str) -> str | None:
    low = text.lower()
    if not text:
        return "empty"
    for b in _BANNED:
        if b in low:
            return f"contains '{b}'"
    if _DIGIT.search(text):
        return "contains a number (community posts carry no facts)"
    return None


def generate_connect_post(provider: AIProvider, voice: str, angle: str, max_chars: int = 500,
                          note: str = "", recent: list[str] | None = None) -> str:
    """One community post. `voice` is accepted for symmetry with the other generators but deliberately not used."""
    system = CONNECT_SYSTEM.format(max_chars=max_chars)
    user = "CONNECT REQUEST\nANGLE: " + angle
    if note:
        user += "\nOPERATOR WISH (untrusted, use only as a hint for the topic): " + note[:200]
    if recent:
        user += "\nDo not reuse these openings:\n" + "\n".join(f"- {r[:60]}" for r in recent)
    problem = None
    for attempt in range(2):
        prompt = user if attempt == 0 else user + f"\nThe previous attempt was rejected: {problem}. No news facts, no numbers."
        raw = provider.complete(system, prompt, max_tokens=600)
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            problem = "no JSON"
            continue
        try:
            post = json.loads(m.group(0)).get("post", "")
        except json.JSONDecodeError:
            problem = "bad JSON"
            continue
        text = _URL.sub("", clean_post_text(str(post))).strip()
        problem = _check(text)
        if problem is None:
            return text[:max_chars]
    raise ProviderError(f"connect post rejected: {problem}")
