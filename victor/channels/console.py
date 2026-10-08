"""Console channel: prints draft cards. Used when Telegram is not configured."""
from __future__ import annotations

import logging

from .base import DraftCard, render_card

log = logging.getLogger(__name__)


class ConsoleChannel:
    name = "console"

    def send_draft(self, card: DraftCard) -> str | None:
        print("\n" + "=" * 72)
        print(render_card(card))
        print("=" * 72 + "\n")
        return None

    def notify(self, text: str) -> None:
        print(f"[notify] {text}")
