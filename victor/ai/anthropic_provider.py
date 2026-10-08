"""Anthropic Claude via the official SDK."""
from __future__ import annotations

import logging

from .base import ProviderError

log = logging.getLogger(__name__)
DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 90.0):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        import anthropic

        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max(max_tokens, 2048),  # short JSON output; thinking is accounted separately
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low"},  # drafting a post is routine work
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise ProviderError(f"anthropic: invalid API key ({e.message})") from e
        except anthropic.RateLimitError as e:
            raise ProviderError(f"anthropic: rate limited ({e.message})") from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"anthropic: bad request ({e.message})") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"anthropic: http {e.status_code} ({e.message})") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"anthropic: network error ({e})") from e
        if response.stop_reason == "refusal":
            cat = response.stop_details.category if response.stop_details else None
            raise ProviderError(f"anthropic: request declined by safety classifier ({cat})")
        if response.stop_reason == "max_tokens":
            log.warning("anthropic: output hit max_tokens; the draft may be truncated")
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            raise ProviderError("anthropic: empty response")
        return text
