from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.models import Draft, Post
from victor.pipeline import run_once
from victor.publish.intent import intent_url
from victor.publish.mock import MockPublisher

from .test_commands import FakeTelegram, _settings
from .test_pipeline import make_fetcher


class FakeTelegramIntent(FakeTelegram):
    def __init__(self):
        super().__init__()
        self.buttons: list[tuple[str, list]] = []
        self.files: list[str] = []

    def send_with_buttons(self, text, buttons):
        self.buttons.append((text, buttons))
        self._next_id += 1
        return str(self._next_id)

    def send_file(self, path, caption=""):
        self.files.append(path)
        return "f1"


def test_intent_url_carries_text_and_quote():
    u = intent_url("NEW: thing happened\n\nWhy it matters: x", "https://x.com/NASA/status/123")
    q = parse_qs(urlsplit(u).query)
    assert q["text"][0].startswith("NEW: thing happened") and q["url"] == ["https://x.com/NASA/status/123"]


def test_approve_hands_off_to_phone_and_posted_confirms(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramIntent()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        did, ref = d.id, d.channel_ref
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        d = s.get(Draft, did)
        assert d.status == "handed_off"
        post = s.scalar(select(Post).where(Post.draft_id == did))
        assert post.status == "manual"
    text, buttons = tg.buttons[-1]
    assert "READY TO POST" in text and buttons[0][0]["text"] == "Open in X"
    assert buttons[0][0]["url"].startswith("https://x.com/intent/post?")
    assert "url=https%3A%2F%2Fx.com%2FOpenAI" in buttons[0][0]["url"]  # the official post is quoted
    assert "Source: https://openai.com" in text
    handoff_ref = tg._next_id
    tg.push_button(f"posted:{did}", handoff_ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        post = s.scalar(select(Post).where(Post.draft_id == did))
        assert post.status == "published" and s.get(Draft, did).status == "published"
    tg.push_cmd("https://x.com/SI4blog_/status/1975999999999999999", reply_to=str(handoff_ref))
    with session() as s:
        process_updates(s, settings, tg, now)
        assert s.scalar(select(Post).where(Post.draft_id == did)).remote_url.endswith("1975999999999999999")


def test_handoffs_count_toward_daily_limit(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_posts_per_day"] = 1
    tg = FakeTelegramIntent()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        did, ref = d.id, d.channel_ref
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
    # a second story approved the same day is deferred by the limit, not handed off
    with session() as s:
        from victor.models import Story
        st = Story(title="Other story", category="tech", entities=[], source_count=2, score=70, classification="hot", status="drafted")
        s.add(st); s.flush()
        d2 = Draft(story=st, text="Second post text here that is fine.", provider="mock", checks={"passed": True}, checks_passed=True, status="approved")
        s.add(d2); s.flush(); d2id = d2.id
    from victor.pipeline import publish_approved
    with session() as s:
        stats = {}
        publish_approved(s, settings, stats, now, channel=tg)
        assert stats["posts_deferred"] == 1 and s.get(Draft, d2id).status == "approved"
