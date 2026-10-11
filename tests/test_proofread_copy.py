from victor.ai.base import ProviderError
from victor.ai.mock import MockProvider
from victor.ai.proofread import proofread, tidy_punctuation
from victor.publish.base import PublishRequest
from victor.publish.intent import ManualPublisher


def test_tidy_fixes_spacing_and_capitals_without_touching_words():
    raw = "OpenAI shipped it today ,and nobody noticed.the docs say 3.5x faster .\n\nwhat changes:everything downstream"
    out = tidy_punctuation(raw)
    assert out == "OpenAI shipped it today, and nobody noticed. The docs say 3.5x faster.\n\nWhat changes: everything downstream"


class RewritingEditor:
    name, model = "editor", "t"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, max_tokens=600):
        return self.reply


def test_proofread_keeps_a_sane_fix_and_rejects_a_rewrite():
    base = "The crew is home after 237 days.\n\nNobody has bone data from a flight that long."
    assert proofread(MockProvider(), base) == base
    fixed = '{"post": "The crew is home after 237 days.\\n\\nNobody has bone data from a flight that long."}'
    assert proofread(RewritingEditor(fixed), base) == base
    strayed = '{"post": "The crew is home after 240 days and the mission was a triumph for everyone involved."}'
    assert proofread(RewritingEditor(strayed), base) == base  # numbers and line count must survive: rejected
    broken = RewritingEditor("not json at all")
    assert proofread(broken, base) == base


class HtmlTelegram:
    supports_html = True

    def __init__(self):
        self.calls = []

    def send_with_buttons(self, text, buttons, html=False):
        self.calls.append((text, buttons, html))
        return "1"


def test_ready_post_copies_whole_text_with_one_tap():
    short = "A short post with <angle> brackets & an ampersand."
    tg = HtmlTelegram()
    ManualPublisher(tg, draft_id=5).publish(PublishRequest(idempotency_key="k", text=short, reply_text="Source: https://x.test/a"))
    text, buttons, html = tg.calls[-1]
    assert html and "<pre>A short post with &lt;angle&gt; brackets &amp; an ampersand.</pre>" in text
    labels = [b["text"] for row in buttons for b in row]
    assert labels.count("Copy post") == 1 and not any(l.startswith("Copy 1/") for l in labels)
    assert [b for row in buttons for b in row if b["text"] == "Copy post"][0]["copy_text"]["text"] == short

    long = "\n\n".join(f"Paragraph {i}: " + "word " * 40 for i in range(4))
    ManualPublisher(tg, draft_id=6).publish(PublishRequest(idempotency_key="k2", text=long))
    text, buttons, html = tg.calls[-1]
    labels = [b["text"] for row in buttons for b in row]
    assert "Copy post" not in labels and "Tap the post above to copy all of it." in text and "<pre>" in text
    assert "Open in X" in labels and "Posted" in labels
