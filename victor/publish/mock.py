"""Mock publisher: records what would have been posted. Never claims success."""
from __future__ import annotations

import logging

from .base import PublishRequest, PublishResult

log = logging.getLogger(__name__)


class MockPublisher:
    name = "mock"

    def publish(self, req: PublishRequest) -> PublishResult:
        log.info("[DEV MODE] would publish (%s): %s", req.idempotency_key, req.text[:80].replace("\n", " "))
        return PublishResult(status="mock", error="X credentials not configured; nothing was posted")
