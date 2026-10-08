"""Mock provider: deterministic template from the story's own sources. Always labelled DEV MODE."""
from __future__ import annotations

import json
import re


class MockProvider:
    name = "mock"
    model = "template-v1"

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        title = _between(user, "TITLE:", "\n") or "Untitled story"
        publisher = _between(user, "PUBLISHER:", "\n") or "source"
        count = _between(user, "SOURCE_COUNT:", "\n") or "1"
        post = f"[DEV MODE] NEW: {title[:120]}\n\nReported by {publisher} and {count} other source(s).\n\nWhy it matters: pending real AI provider."
        return json.dumps(
            {
                "post": post[:280],
                "why_it_matters": "Placeholder from mock provider.",
                "reason": f"Mock draft. Story had {count} source(s); first reported by {publisher}.",
            }
        )


def _between(text: str, start: str, end: str) -> str:
    m = re.search(re.escape(start) + r"\s*(.*?)" + re.escape(end), text)
    return m.group(1).strip() if m else ""
