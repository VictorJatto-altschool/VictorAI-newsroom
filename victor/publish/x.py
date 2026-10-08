"""X publisher via the official API (tweepy, OAuth 1.0a user context).

Phase 1 wires the adapter. Phase 3 turns it on after the free-tier terms are confirmed.
Rules: never resubmit after a timeout without reconciling; source link goes in the first reply.
"""
from __future__ import annotations

import logging
import re

from .base import PublishRequest, PublishResult

log = logging.getLogger(__name__)
_STATUS_ID = re.compile(r"/status/(\d+)")


class XPublisher:
    name = "x"

    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str, username: str = ""):
        import tweepy  # imported lazily so the mock path has no dependency

        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret,
            wait_on_rate_limit=False,
        )
        self._username = username

    def publish(self, req: PublishRequest) -> PublishResult:
        import tweepy

        kwargs = {"text": req.text}
        if req.quote_url:
            m = _STATUS_ID.search(req.quote_url)
            if m:
                kwargs["quote_tweet_id"] = m.group(1)
        try:
            res = self._client.create_tweet(**kwargs)
        except tweepy.errors.TooManyRequests as e:
            return PublishResult(status="failed", error=f"rate limited: {e}")
        except tweepy.errors.Forbidden as e:
            return PublishResult(status="failed", error=f"forbidden (check API tier / duplicate): {e}")
        except tweepy.errors.TweepyException as e:
            # Network timeouts after submit are ambiguous: mark uncertain, never auto-retry.
            return PublishResult(status="uncertain", error=f"{type(e).__name__}: {e}")
        post_id = str(res.data.get("id")) if res and res.data else None
        if not post_id:
            return PublishResult(status="uncertain", error="no id in response")
        url = f"https://x.com/{self._username or 'i'}/status/{post_id}"
        reply_id = None
        if req.reply_text:
            try:
                r2 = self._client.create_tweet(text=req.reply_text, in_reply_to_tweet_id=post_id)
                reply_id = str(r2.data.get("id")) if r2 and r2.data else None
            except tweepy.errors.TweepyException as e:
                log.warning("reply failed for %s: %s", post_id, e)
        return PublishResult(status="published", remote_id=post_id, remote_url=url, reply_remote_id=reply_id)
