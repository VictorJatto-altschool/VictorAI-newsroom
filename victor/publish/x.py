"""X publisher via the official API (tweepy, OAuth 1.0a user context).

Rules: never resubmit after a timeout without reconciling; source link goes in the first reply;
media is uploaded only from a local path we prepared under the rights policy.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .base import PublishRequest, PublishResult

log = logging.getLogger(__name__)
_STATUS_ID = re.compile(r"/status/(\d+)")


class XPublisher:
    name = "x"

    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str, username: str = ""):
        import tweepy  # imported lazily so the mock path has no dependency

        self._client = tweepy.Client(
            consumer_key=api_key, consumer_secret=api_secret,
            access_token=access_token, access_token_secret=access_secret, wait_on_rate_limit=False,
        )
        auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
        self._v1 = tweepy.API(auth)  # media upload lives on v1.1
        self._username = username

    def _upload(self, path: str) -> str | None:
        import tweepy

        p = Path(path)
        if not p.exists():
            return None
        try:
            if p.suffix.lower() == ".mp4":
                m = self._v1.media_upload(filename=str(p), media_category="tweet_video", chunked=True)
            else:
                m = self._v1.media_upload(filename=str(p))
            return str(m.media_id)
        except tweepy.errors.TweepyException as e:
            log.warning("media upload failed (%s): %s", p.name, e)
            return None

    def publish(self, req: PublishRequest) -> PublishResult:
        import tweepy

        kwargs: dict = {"text": req.text}
        if req.quote_url:
            m = _STATUS_ID.search(req.quote_url)
            if m:
                kwargs["quote_tweet_id"] = m.group(1)
        if req.media_path:
            mid = self._upload(req.media_path)
            if mid:
                kwargs["media_ids"] = [mid]
        try:
            res = self._client.create_tweet(**kwargs)
        except tweepy.errors.TooManyRequests as e:
            return PublishResult(status="failed", error=f"rate limited: {e}")
        except tweepy.errors.Forbidden as e:
            return PublishResult(status="failed", error=f"forbidden (API tier, duplicate, or media rights): {e}")
        except tweepy.errors.Unauthorized as e:
            return PublishResult(status="failed", error=f"unauthorized: {e}")
        except tweepy.errors.TweepyException as e:
            # Network trouble after submit is ambiguous: mark uncertain, never auto-retry.
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

    def find_recent(self, text: str) -> tuple[str, str] | None:
        """Reconcile an uncertain result: look for our own recent post with this text. Needs read access."""
        import tweepy

        try:
            me = self._client.get_me()
            tl = self._client.get_users_tweets(me.data.id, max_results=20)
        except tweepy.errors.TweepyException as e:
            log.info("reconcile unavailable on this API tier: %s", e)
            return None
        head = text.strip()[:60]
        for t in tl.data or []:
            if t.text.strip().startswith(head):
                return str(t.id), f"https://x.com/{self._username or me.data.username}/status/{t.id}"
        return None
