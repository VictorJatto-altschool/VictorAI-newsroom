"""Mock provider: deterministic template from the story's own sources. Always labelled DEV MODE."""
from __future__ import annotations

import json
import re


class MockProvider:
    name = "mock"
    model = "template-v1"

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        if user.startswith("THREAD REQUEST"):
            title = _between(user, "TITLE:", "\n") or "Untitled"
            return json.dumps({"posts": [f"[DEV MODE] {title[:80]}", "Fact one from the sources.", "Fact two from the sources.",
                                         "Why it matters: pending real provider. What would you do with it?"]})
        if user.startswith("CONNECT REQUEST"):
            angle = _between(user, "ANGLE:", "\n") or "introduce yourself"
            return json.dumps({"post": f"[DEV MODE] This account reads AI, science and space news all day. Question of the day: {angle[:120]}. "
                                       "Reply and I follow and talk back to everyone in the field."})
        if user.startswith("REPLY REQUEST"):
            n = len(re.findall(r"^STORY \d+$", user, re.M))
            return json.dumps({"replies": [f"[DEV MODE] Reply {i + 1}: a fact from the sources. What would change your mind?" for i in range(n)]})
        if user.startswith("OPERATOR NOTE:"):
            note = _between(user, "OPERATOR NOTE:", "--- END NOTE")
            post = f"[DEV MODE] From the desk: {note[:180]}\n\nTakeaway: pending real AI provider."
            return json.dumps({"post": post[:280], "why_it_matters": "Placeholder.",
                               "reason": "educational post from operator note"})
        title = _between(user, "TITLE:", "\n") or "Untitled story"
        publisher = _between(user, "PUBLISHER:", "\n") or "source"
        count = _between(user, "SOURCE_COUNT:", "\n") or "1"
        post = f"[DEV MODE] NEW: {title[:70]}\n\nReported by {publisher} and {count} other source(s).\n\nWhy it matters: pending real AI provider."
        return json.dumps(
            {
                "post": post[:280],
                "why_it_matters": "Placeholder from mock provider.",
                "reason": f"Mock draft. Story had {count} source(s); first reported by {publisher}.",
                "suggested_take": "[DEV MODE] I think this matters more for small teams than for the big labs.",
            }
        )


def _between(text: str, start: str, end: str) -> str:
    m = re.search(re.escape(start) + r"\s*(.*?)" + re.escape(end), text, re.S)
    return m.group(1).strip() if m else ""
