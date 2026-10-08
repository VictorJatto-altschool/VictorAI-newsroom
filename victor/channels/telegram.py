"""Telegram channel. Phase 1: send draft cards with inline buttons. Phase 2 adds command handling."""
from __future__ import annotations

import logging

import httpx

from .base import DraftCard, render_card

log = logging.getLogger(__name__)


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 20.0):
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._chat = chat_id
        self._timeout = timeout

    def _call(self, method: str, **payload):
        try:
            r = httpx.post(f"{self._base}/{method}", json=payload, timeout=self._timeout)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("telegram %s failed: %s", method, e)
            return None
        if not data.get("ok"):
            log.warning("telegram %s rejected: %s", method, str(data)[:200])
            return None
        return data.get("result")

    def send_draft(self, card: DraftCard) -> str | None:
        d = card.draft_id
        buttons = [
            [
                {"text": "Approve", "callback_data": f"approve:{d}"},
                {"text": "Approve for night", "callback_data": f"night:{d}"},
            ],
            [
                {"text": "Rewrite", "callback_data": f"rewrite:{d}"},
                {"text": "Reject", "callback_data": f"reject:{d}"},
            ],
        ]
        res = self._call(
            "sendMessage",
            chat_id=self._chat,
            text=render_card(card)[:4000],
            disable_web_page_preview=False,
            reply_markup={"inline_keyboard": buttons},
        )
        return str(res["message_id"]) if res else None

    def notify(self, text: str) -> None:
        self._call("sendMessage", chat_id=self._chat, text=text[:4000], disable_web_page_preview=True)
