"""Manual publishing through X's web intent: no API, no cost.

On approve, the operator gets a Telegram message with an "Open in X" button. Tapping it opens the X app's
composer with the text filled in. If the draft quotes an official post, that post is attached as a quote.
Rendered or downloaded media is sent to Telegram as a file so the operator can attach it in the composer.
The operator taps "Posted" afterwards so the newsroom records it and the anti-spam limits apply.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import urlencode

from .base import PublishRequest, PublishResult

log = logging.getLogger(__name__)
INTENT_BASE = "https://x.com/intent/post"


COPY_LIMIT = 256  # Telegram copy_text buttons hold at most this many characters


def copy_parts(text: str, limit: int = COPY_LIMIT) -> list[str]:
    """Split a post into pieces a Telegram copy button can hold, breaking at blank lines, then lines, then spaces."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else [""]
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = -1
        for sep in ("\n\n", "\n", " "):
            cut = rest.rfind(sep, 0, limit + 1)
            if cut > 0:
                break
        if cut <= 0:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


def intent_url(text: str, quote_url: str = "") -> str:
    params = {"text": text}
    if quote_url:
        params["url"] = quote_url  # an X post URL in `url` renders as a quote in the composer
    return f"{INTENT_BASE}?{urlencode(params)}"


class ManualPublisher:
    """Hands the post to the operator's phone instead of calling X."""

    name = "manual"

    def __init__(self, channel, draft_id: int | None = None):
        self._tg = channel
        self.draft_id = draft_id
        self.message_ref: str | None = None  # the draft card to transform in place
        self.footer: str = ""  # e.g. "Next slot opens at 14:30", set by the pipeline
        self.take_hint: str = ""  # a suggested line of the owner's own take, offered as a Copy button

    def publish(self, req: PublishRequest) -> PublishResult:
        url = intent_url(req.text, req.quote_url)
        d = self.draft_id
        html_ok = bool(getattr(self._tg, "supports_html", False))
        esc = (lambda s: html.escape(s, quote=False)) if html_ok else (lambda s: s)
        fits = len(req.text) <= COPY_LIMIT
        # In HTML mode the post sits in a <pre> block: one tap on it copies the whole post, whatever its length.
        post_block = f"<pre>{esc(req.text)}</pre>" if html_ok else req.text
        lines = [f"READY TO POST (draft #{d})" if d else "READY TO POST", "", post_block]
        if html_ok and not fits:
            lines += ["", "Tap the post above to copy all of it."]
        if req.quote_url:
            lines += ["", esc(f"Quotes: {req.quote_url} (video plays under your text)")]
        if req.media_path:
            lines += ["", "Media is in the next message: save it and attach it in the composer."]
        if req.reply_text:
            lines += ["", "After posting, paste this as the first reply:", esc(req.reply_text)]
        if self.take_hint:
            lines += ["", "Your take (copy, change a few words, paste it as the last line in the composer):", esc(self.take_hint)]
        if self.footer:
            lines += ["", esc(self.footer)]
        buttons = [[{"text": "Open in X", "url": url}]]
        if fits:  # Telegram copy buttons hold 256 characters; longer posts copy from the block above
            buttons.append([{"text": "Copy post", "copy_text": {"text": req.text}}])
        if self.take_hint:
            buttons.append([{"text": "Copy my take", "copy_text": {"text": self.take_hint[:256]}}])
        if d:
            buttons.append([{"text": "Posted", "callback_data": f"posted:{d}"}, {"text": "Skip", "callback_data": f"skip:{d}"}])
        kwargs = {"html": True} if html_ok else {}
        ref = None
        if self.message_ref and hasattr(self._tg, "edit_with_buttons"):
            ref = self._tg.edit_with_buttons(self.message_ref, "\n".join(lines), buttons, **kwargs)  # the card becomes the post
        elif hasattr(self._tg, "send_with_buttons"):
            ref = self._tg.send_with_buttons("\n".join(lines), buttons, **kwargs)
        else:
            self._tg.notify("\n".join(lines) + f"\n\nOpen in X: {url}")
        if req.media_path and Path(req.media_path).exists() and hasattr(self._tg, "send_file"):
            self._tg.send_file(req.media_path, caption=f"Media for draft #{d}" if d else "Media")
        if ref is None and hasattr(self._tg, "send_with_buttons"):
            return PublishResult(status="failed", error="could not reach Telegram to hand off the post")
        return PublishResult(status="manual", remote_url=None, error=None)
