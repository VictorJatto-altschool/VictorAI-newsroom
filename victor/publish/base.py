"""Publisher interface. A publish result is only 'published' when the platform confirmed it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PublishRequest:
    idempotency_key: str
    text: str
    reply_text: str = ""
    quote_url: str = ""
    media_path: str = ""


@dataclass
class PublishResult:
    status: str  # published | uncertain | failed | mock
    remote_id: str | None = None
    remote_url: str | None = None
    reply_remote_id: str | None = None
    error: str | None = None


class Publisher(Protocol):
    name: str

    def publish(self, req: PublishRequest) -> PublishResult: ...
