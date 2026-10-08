"""Approval channel interface: where drafts go for a human decision."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class DraftCard:
    draft_id: int
    story_title: str
    text: str
    why: str
    reason: str
    score: float
    classification: str
    sources: list[tuple[str, str]]  # (publisher, url)
    media: dict
    checks: dict
    route: str
    dev_mode: bool = True
    tags: list[str] = field(default_factory=list)


class Channel(Protocol):
    name: str

    def send_draft(self, card: DraftCard) -> str | None:
        """Deliver a draft for review. Returns a channel reference (message id) or None."""
        ...

    def notify(self, text: str) -> None: ...


def render_card(card: DraftCard) -> str:
    srcs = "\n".join(f"  - {p}: {u}" for p, u in card.sources[:6])
    checks = ", ".join(k for k, v in card.checks.items() if k != "passed" and not v.get("ok", True)) or "all passed"
    label = "[DEV MODE] " if card.dev_mode else ""
    media = card.media.get("mode", "none")
    media_line = f"{media} {card.media.get('url', '')}".strip()
    return (
        f"{label}DRAFT #{card.draft_id} | {card.classification.upper()} {card.score:.0f} | route: {card.route}\n"
        f"Story: {card.story_title}\n"
        f"\n{card.text}\n\n"
        f"Why it matters: {card.why}\n"
        f"Reason chosen: {card.reason}\n"
        f"Media: {media_line}\n"
        f"Checks: {checks}\n"
        f"Sources:\n{srcs}"
    )
