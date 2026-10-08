"""Educational posts drafted only from operator notes. Never invented from nothing."""
from __future__ import annotations

from ..ai.base import AIProvider, DraftOutput, parse_draft_json

EDU_SYSTEM = (
    "You write educational posts for the X account Victor AI & Tech. Follow the voice guide. "
    "Use ONLY what the OPERATOR NOTE says the operator actually did and observed. Do not add features, numbers, "
    "prices or steps that are not in the note. Structure: PROBLEM -> USEFUL INSIGHT -> PRACTICAL EXAMPLE -> ACTIONABLE TAKEAWAY. "
    "No URLs, no hashtags, under 280 characters. Respond with a JSON object: "
    '{"post": string, "why_it_matters": one sentence, "reason": "educational post from operator note"}.'
)


def generate_educational(provider: AIProvider, voice: str, note_text: str) -> DraftOutput:
    user = f"OPERATOR NOTE:\n{note_text.strip()}\n--- END NOTE"
    raw = provider.complete(EDU_SYSTEM + "\n\nVOICE GUIDE:\n" + voice, user)
    return parse_draft_json(raw, provider.name, provider.model)
