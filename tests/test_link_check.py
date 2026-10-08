"""The source-link check must never block a cycle: slow host = warning on the card, dead link = failed check."""
from __future__ import annotations

import httpx

from victor.ai import draft as draft_mod
from victor.ai.draft import SourceView, run_checks
from victor.channels.base import DraftCard, render_card
from victor.channels.telegram import UPDATES_TIMEOUT, TelegramChannel

SRC = [SourceView(1, "OpenAI", 1, "OpenAI releases GPT-6", "GPT-6 has a 2M token context window.", "https://openai.com/gpt-6")]
TEXT = "OpenAI releases GPT-6 with a 2M token context window.\nWhy it matters: longer memory for agents."


def _head(exc=None, status=200):
    def fake(url, **kw):
        assert kw["timeout"] <= 5.0, "link check must stay under the 5 s budget"
        if exc:
            raise exc
        return httpx.Response(status, request=httpx.Request("HEAD", url))
    return fake


def test_link_timeout_is_a_warning_not_a_failure(monkeypatch):
    monkeypatch.setattr(draft_mod.httpx, "head", _head(httpx.ReadTimeout("slow")))
    r = run_checks(TEXT, SRC, {"max_chars": 600}, check_links=True)
    assert r["source_link_resolves"]["ok"] is True
    assert "timed out" in r["source_link_resolves"]["warning"]
    assert r["passed"]


def test_unreachable_host_is_a_warning(monkeypatch):
    monkeypatch.setattr(draft_mod.httpx, "head", _head(httpx.ConnectError("flaky wifi")))
    r = run_checks(TEXT, SRC, {"max_chars": 600}, check_links=True)
    assert r["source_link_resolves"]["ok"] is True and "warning" in r["source_link_resolves"]


def test_dead_link_still_fails(monkeypatch):
    monkeypatch.setattr(draft_mod.httpx, "head", _head(status=404))
    r = run_checks(TEXT, SRC, {"max_chars": 600}, check_links=True)
    assert r["source_link_resolves"] == {"ok": False, "status": 404} and not r["passed"]


def test_card_shows_the_warning():
    checks = {"length": {"ok": True}, "source_link_resolves": {"ok": True, "warning": "link check timed out after 5s"}, "passed": True}
    card = DraftCard(draft_id=1, story_title="t", text="x", why="y", reason="z", score=70, classification="hot",
                     sources=[("OpenAI", "https://openai.com/gpt-6")], media={}, checks=checks, route="review", dev_mode=True, tags=[])
    out = render_card(card)
    assert "Checks: all passed | warning: link check timed out after 5s" in out


def test_get_updates_is_capped_and_a_timeout_returns_nothing(monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["timeout"] = kw["timeout"]
        seen["long_poll"] = kw["json"]["timeout"]
        assert "bot" in url  # the token is in the URL; the test must not log it either
        raise httpx.ReadTimeout("telegram slow")

    monkeypatch.setattr("victor.channels.telegram.httpx.post", fake_post)
    tg = TelegramChannel("123:token", "42", timeout=20.0)
    assert tg.get_updates(None, timeout=30) == []
    assert seen["long_poll"] <= UPDATES_TIMEOUT and seen["timeout"] <= 2 * UPDATES_TIMEOUT
