import pytest

from victor.ai.base import ProviderError, parse_draft_json
from victor.ai.draft import SourceView, run_checks
from victor.ai.mock import MockProvider
from victor.media.extract import choose_media, find_media

SRC = [SourceView(1, "OpenAI", 1, "OpenAI releases GPT-6", "GPT-6 has a 2M token context window and costs $5 per million tokens.",
                  "https://openai.com/gpt-6")]


def test_parse_draft_json_tolerates_prose():
    out = parse_draft_json('Sure! {"post": "NEW: thing", "why_it_matters": "x", "reason": "y", "suggested_take": "I think this is the real shift."} done', "p", "m")
    assert out.text == "NEW: thing" and out.provider == "p" and out.suggested_take == "I think this is the real shift."


def test_parse_draft_json_rejects_garbage():
    with pytest.raises(ProviderError):
        parse_draft_json("no json here", "p", "m")


def test_checks_pass_on_clean_post(settings):
    r = run_checks("NEW: OpenAI releases GPT-6.\n\n2M token context window.\n\nWhy it matters: whole codebases fit in one prompt.",
                   SRC, settings.drafting, check_links=False)
    assert r["passed"], r


def test_checks_catch_urls_hashtags_numbers_and_banned_words(settings):
    r = run_checks("Groundbreaking! GPT-6 scores 99% #AI https://x.com/foo", SRC, settings.drafting, check_links=False)
    assert not r["passed"]
    assert not r["no_urls_in_post"]["ok"] and not r["no_hashtags"]["ok"]
    assert "99" in r["numbers_in_sources"]["missing"] and r["banned_words"]["hits"] == ["groundbreaking"]


def test_mock_provider_is_labelled_dev_mode():
    raw = MockProvider().complete("sys", "TITLE: A thing happened\nPUBLISHER: NASA\nSOURCE_COUNT: 3\n")
    assert "[DEV MODE]" in parse_draft_json(raw, "mock", "t").text


def test_find_media_and_choose_official_quote():
    html = ('<p>See <a href="https://x.com/OpenAI/status/1234567890123">the post</a> and '
            '<a href="https://twitter.com/randomguy/status/999999999999">this</a> '
            '<img src="https://images-assets.nasa.gov/image/x/x~orig.jpg"> '
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ</p>')
    media = find_media(html, "", "openai.com")
    types = {m["type"] for m in media}
    assert types == {"x_post", "image", "youtube"}
    pick = choose_media(media, {"openai"})
    assert pick["mode"] == "quote" and "OpenAI" in pick["url"]
    pick = choose_media([m for m in media if m["type"] != "x_post"], {"openai"})
    assert pick["mode"] == "upload" and pick["rights"] == "public_domain"
    assert choose_media([m for m in media if m["type"] == "youtube"], {"openai"})["mode"] == "render"
    # a random person's post is never auto-quoted
    assert choose_media([m for m in media if m["type"] == "x_post" and m["author"] == "randomguy"], {"openai"})["mode"] == "render"
