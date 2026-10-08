"""Telegram channel: draft cards with inline buttons, notifications, and update polling (no server needed)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from .base import DraftCard, render_card

log = logging.getLogger(__name__)
UPDATES_TIMEOUT = 5.0  # getUpdates is a quick poll; a slow Telegram must not hold up collection


def draft_keyboard(draft_id: int) -> dict:
    d = draft_id
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approve:{d}"},
                {"text": "Approve for night", "callback_data": f"night:{d}"},
            ],
            [
                {"text": "Rewrite", "callback_data": f"rewrite:{d}"},
                {"text": "Reject", "callback_data": f"reject:{d}"},
            ],
        ]
    }


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 20.0):
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self.chat_id = str(chat_id)
        self._timeout = timeout

    def _call(self, method: str, **payload):
        try:
            r = httpx.post(f"{self._base}/{method}", json=payload, timeout=self._timeout)
            data = r.json()
        except Exception as e:  # noqa: BLE001  a channel failure must never end a cycle
            log.warning("telegram %s failed: %s: %s", method, type(e).__name__, str(e)[:160])
            return None
        if not data.get("ok"):
            log.warning("telegram %s rejected: %s", method, str(data)[:200])
            return None
        return data.get("result")

    # ---- outbound
    def send_draft(self, card: DraftCard) -> str | None:
        res = self._call("sendMessage", chat_id=self.chat_id, text=render_card(card)[:4000],
                         disable_web_page_preview=False, reply_markup=draft_keyboard(card.draft_id))
        time.sleep(0.6)  # Telegram allows ~1 message/second per chat; a busy news hour can mean dozens of cards
        return str(res["message_id"]) if res else None

    def notify(self, text: str) -> None:
        self._call("sendMessage", chat_id=self.chat_id, text=text[:4000], disable_web_page_preview=True)

    def send_with_buttons(self, text: str, buttons: list[list[dict]]) -> str | None:
        res = self._call("sendMessage", chat_id=self.chat_id, text=text[:4000], disable_web_page_preview=True,
                         reply_markup={"inline_keyboard": buttons})
        return str(res["message_id"]) if res else None

    def send_file(self, path: str, caption: str = "") -> str | None:
        """Send a local image or video so the operator can save it and attach it in the X composer."""
        p = Path(path)
        method, field = ("sendVideo", "video") if p.suffix.lower() in (".mp4", ".mov") else ("sendPhoto", "photo")
        try:
            with p.open("rb") as fh:
                r = httpx.post(f"{self._base}/{method}", data={"chat_id": self.chat_id, "caption": caption[:1000]},
                               files={field: (p.name, fh)}, timeout=self._timeout + 60)
            data = r.json()
        except (httpx.HTTPError, ValueError, OSError) as e:
            log.warning("telegram %s failed: %s", method, e)
            return None
        if not data.get("ok"):
            log.warning("telegram %s rejected: %s", method, str(data)[:200])
            return None
        return str(data["result"]["message_id"])

    def mark(self, message_id: str | None, label: str) -> None:
        """Replace the buttons under a card with a single disabled-looking label."""
        if not message_id:
            return
        self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=int(message_id),
                   reply_markup={"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]})

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])

    # ---- inbound
    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        """One short poll. `timeout` is Telegram's long-poll wait and is capped so the cycle never stalls."""
        timeout = max(0, min(int(timeout), int(UPDATES_TIMEOUT)))
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        try:
            r = httpx.post(f"{self._base}/getUpdates", json=payload, timeout=UPDATES_TIMEOUT + timeout)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("telegram getUpdates failed: %s", e)
            return []
        return data.get("result", []) if data.get("ok") else []
