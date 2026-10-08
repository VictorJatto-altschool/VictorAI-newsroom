"""Telegram buttons and commands against a fake bot API. No network."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.config import Env, load_settings, CONFIG_DIR
from victor.db import session
from victor.models import Draft, GrowthSnapshot, Note, Source
from victor.pipeline import get_state, run_once
from victor.publish.mock import MockPublisher

from .test_pipeline import make_fetcher

CHAT = 4242


class FakeTelegram:
    def __init__(self):
        self.sent: list[str] = []
        self.marks: list[tuple[str | None, str]] = []
        self.answers: list[str] = []
        self.queue: list[dict] = []
        self._next_id = 100
        self._update_id = 1

    # outbound
    def send_draft(self, card):
        self._next_id += 1
        self.sent.append(f"DRAFT#{card.draft_id}:{card.text}")
        return str(self._next_id)

    def notify(self, text):
        self.sent.append(text)

    def mark(self, message_id, label):
        self.marks.append((message_id, label))

    def answer_callback(self, cid, text=""):
        self.answers.append(text)

    # inbound
    def get_updates(self, offset, timeout=0):
        out = [u for u in self.queue if offset is None or u["update_id"] >= offset]
        return out

    def push_cmd(self, text, chat=CHAT, reply_to=None):
        m = {"message_id": 7, "chat": {"id": chat}, "text": text}
        if reply_to:
            m["reply_to_message"] = {"message_id": int(reply_to)}
        self.queue.append({"update_id": self._bump(), "message": m})

    def push_button(self, data, message_id, chat=CHAT):
        self.queue.append({"update_id": self._bump(), "callback_query": {
            "id": "cb1", "data": data, "from": {"id": chat},
            "message": {"message_id": int(message_id), "chat": {"id": chat}}}})

    def _bump(self):
        self._update_id += 1
        return self._update_id


def _settings():
    st = load_settings(CONFIG_DIR, env=Env(app_env="development", telegram_bot_token="t", telegram_chat_id=str(CHAT)))
    st.raw["drafting"]["check_links"] = False
    return st


def _seed(now):
    settings = _settings()
    tg = FakeTelegram()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg,
             publisher=MockPublisher(), check_links=False)
    return settings, tg


def test_approve_button_then_publish(fresh_db, now):
    settings, tg = _seed(now)
    with session() as s:
        d = s.scalar(select(Draft))
        ref = d.channel_ref
    tg.push_button(f"approve:{d.id}", ref)
    with session() as s:
        assert process_updates(s, settings, tg, now) == 1
        assert s.get(Draft, d.id).status == "approved"
    assert tg.marks[-1][1] == "Approved"
    assert get_state_value("telegram_offset") == str(tg._update_id + 1)


def get_state_value(key):
    with session() as s:
        return get_state(s, key, "")


def test_unauthorized_chat_is_ignored(fresh_db, now):
    settings, tg = _seed(now)
    with session() as s:
        d = s.scalar(select(Draft))
    tg.push_button(f"approve:{d.id}", d.channel_ref, chat=999)
    tg.push_cmd("/pause", chat=999)
    with session() as s:
        process_updates(s, settings, tg, now)
        assert s.get(Draft, d.id).status == "pending"
        assert get_state(s, "paused", "false") == "false"
    assert tg.answers[-1] == "Not authorized"


def test_reject_rewrite_and_edit(fresh_db, now):
    settings, tg = _seed(now)
    with session() as s:
        d = s.scalar(select(Draft))
        did, ref = d.id, d.channel_ref
    tg.push_button(f"rewrite:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now, provider=MockProvider())
        drafts = s.scalars(select(Draft).order_by(Draft.id)).all()
        assert [x.status for x in drafts] == ["superseded", "pending"]
        assert drafts[1].version == 2
        new_ref = drafts[1].channel_ref
        new_id = drafts[1].id
    tg.push_cmd("NEW: GPT-6 ships with a 2M token context window.\n\nWhy it matters: whole repos fit in one prompt.",
                reply_to=new_ref)
    with session() as s:
        process_updates(s, settings, tg, now, provider=MockProvider())
        drafts = s.scalars(select(Draft).order_by(Draft.id)).all()
        assert drafts[-1].provider == "operator" and drafts[-1].version == 3 and drafts[-1].checks_passed
        assert drafts[-1].text.startswith("NEW: GPT-6")
        last_id = drafts[-1].id
    tg.push_button(f"reject:{last_id}", drafts[-1].channel_ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        assert s.get(Draft, last_id).status == "rejected"
        assert s.get(Draft, last_id).story.status == "skipped"


def test_commands(fresh_db, now):
    settings, tg = _seed(now)
    for c in ("/pause", "/status", "/mode restricted_autonomous", "/night 3", "/note tried Cursor agent mode, it refactors tests well",
              "/growth 160 4,200", "/block techcrunch-ai 2", "/sources", "/tonight", "/posted", "/pending", "/digest", "/resume", "/nonsense"):
        tg.push_cmd(c)
    with session() as s:
        n = process_updates(s, settings, tg, now)
        assert n == 14
        assert get_state(s, "paused", "") == "false"
        assert get_state(s, "mode", "") == "restricted_autonomous"
        assert get_state(s, "night_cap", "") == "3"
        assert s.scalar(select(Note)).text.startswith("tried Cursor")
        g = s.scalar(select(GrowthSnapshot))
        assert g.followers == 160 and g.verified_impressions_90d == 4200
        src = s.scalar(select(Source).where(Source.key == "techcrunch-ai"))
        assert src.blocked_until is not None
    joined = "\n".join(tg.sent)
    assert "PAUSED" in joined and "Followers 160/500" in joined and "Unknown command" in joined and "Week to" in joined


def test_blocked_source_is_skipped_next_run(fresh_db, now):
    settings, tg = _seed(now)
    tg.push_cmd("/block techcrunch-ai 2")
    stats = run_once(settings, now + timedelta(hours=1), fetcher=make_fetcher(now + timedelta(hours=1)), provider=MockProvider(),
                     channel=tg, publisher=MockPublisher(), check_links=False)
    assert stats["commands_handled"] == 1
    with session() as s:
        src = s.scalar(select(Source).where(Source.key == "techcrunch-ai"))
        assert src.last_attempt_at.replace(tzinfo=None) == now.replace(tzinfo=None)  # not fetched again
