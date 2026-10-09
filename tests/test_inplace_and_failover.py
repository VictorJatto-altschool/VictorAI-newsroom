from sqlalchemy import select

from victor.ai.base import ProviderError
from victor.ai.draft import FailoverProvider, pick_provider
from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.config import Env
from victor.db import session
from victor.models import Draft
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_intent import FakeTelegramIntent
from .test_pipeline import make_fetcher


class FakeTelegramEditable(FakeTelegramIntent):
    def __init__(self):
        super().__init__()
        self.edits: list[tuple[str, str, list]] = []

    def edit_with_buttons(self, message_id, text, buttons):
        self.edits.append((message_id, text, buttons))
        return str(message_id)


def test_approve_transforms_the_card_in_place(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramEditable()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        did, ref = d.id, d.channel_ref
    tg.push_button(f"approve:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
    assert tg.buttons == [], "no second message"
    msg_id, text, buttons = tg.edits[-1]
    assert msg_id == ref and "READY TO POST" in text and buttons[0][0]["text"] == "Open in X"


class Flaky:
    name, model = "groq", "a"

    def complete(self, system, user, max_tokens=600):
        raise ProviderError("groq http 429: rate limit")


class Fine:
    name, model = "cerebras", "b"

    def complete(self, system, user, max_tokens=600):
        return '{"post": "ok", "why_it_matters": "x", "reason": "y"}'


def test_failover_moves_to_next_provider_on_rate_limit():
    p = FailoverProvider([Flaky(), Fine()])
    assert "ok" in p.complete("s", "u") and p.name == "cerebras"


def test_pick_provider_builds_failover_when_several_keys_exist():
    env = Env(groq_api_key="g", cerebras_api_key="c")
    p = pick_provider(env, ["anthropic", "gemini", "groq", "cerebras", "mock"])
    assert isinstance(p, FailoverProvider) and p.name == "groq"
    assert pick_provider(Env(), ["groq", "mock"]).name == "mock"
