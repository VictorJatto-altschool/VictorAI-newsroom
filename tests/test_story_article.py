import json

import httpx
from sqlalchemy import select

from victor.collect.manual import article_excerpt, fetch_page, ingest_url
from victor.db import session
from victor.models import Item

TWEET = "https://x.com/TechCrunch/status/1976000000000000000"
ARTICLE = "https://techcrunch.com/2026/10/11/what-to-know-about-the-warner-bros-deal/"
OEMBED_HTML = ('<blockquote class="twitter-tweet"><p lang="en">What to know about the landmark Warner Bros. deal '
               '<a href="https://t.co/abc123">https://t.co/abc123</a></p>&mdash; TechCrunch (@TechCrunch) '
               '<a href="https://twitter.com/TechCrunch/status/1976000000000000000">October 11, 2026</a></blockquote>')
ARTICLE_HTML = """<html><head><title>What to know about the landmark Warner Bros. deal | TechCrunch</title>
<meta property="og:title" content="What to know about the landmark Warner Bros. deal">
<meta property="og:description" content="Paramount Skydance agreed to buy Warner Bros. Discovery for $110 billion.">
</head><body>
<p>Subscribe to our newsletter for more.</p>
<p>Paramount Skydance agreed on Saturday to buy Warner Bros. Discovery in a deal valued at $110 billion including debt, the largest media merger in a decade.</p>
<p>The combined company would own CNN, HBO, Warner Bros. Pictures and Paramount Pictures, and regulators in the U.S. and EU must still approve it.</p>
</body></html>"""


def _transport(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "publish.twitter.com":
        return httpx.Response(200, json={"html": OEMBED_HTML, "author_name": "TechCrunch"})
    if host == "t.co":
        return httpx.Response(301, headers={"location": ARTICLE})
    if host == "techcrunch.com":
        return httpx.Response(200, text=ARTICLE_HTML)
    return httpx.Response(404)


def test_x_post_that_links_to_an_article_is_read_as_the_article():
    client = httpx.Client(transport=httpx.MockTransport(_transport), follow_redirects=True)
    page = fetch_page(TWEET, client)
    assert page["title"] == "What to know about the landmark Warner Bros. deal"
    assert page["publisher"] == "techcrunch.com" and page["article_url"] == ARTICLE
    assert page["x_post"] == TWEET and page["author"] == "TechCrunch"
    assert "Paramount Skydance agreed on Saturday" in page["description"]  # the article's own paragraphs
    assert "Subscribe to our newsletter" not in page["description"]
    assert "The X post by TechCrunch that linked to it said" in page["description"]


REAL_SHAPE_HTML = ('<blockquote class="twitter-tweet"><p lang="en">What to know about the landmark Warner Bros. Discovery sale '
                   'techcrunch.​com/2026/10/11/what-to-know-about-the-warner-bros-deal/ '
                   '<a href="https://t.co/pic999">pic.twitter.com/pic999</a></p>&mdash; TechCrunch (@TechCrunch) '
                   '<a href="https://twitter.com/TechCrunch/status/1976000000000000000">October 11, 2026</a></blockquote>')


def test_article_address_shown_only_as_text_is_still_followed():
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "publish.twitter.com":
            return httpx.Response(200, json={"html": REAL_SHAPE_HTML, "author_name": "TechCrunch"})
        if request.url.host == "t.co":  # the picture link leads back to the post, as it does on X
            return httpx.Response(301, headers={"location": TWEET})
        if request.url.host == "x.com":
            return httpx.Response(200, text="<html>post</html>")
        if request.url.host == "techcrunch.com":
            return httpx.Response(200, text=ARTICLE_HTML)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=True)
    page = fetch_page(TWEET, client)
    assert page["article_url"] == ARTICLE and page["publisher"] == "techcrunch.com"
    assert page["title"] == "What to know about the landmark Warner Bros. deal"


def test_excerpt_skips_boilerplate_and_keeps_paragraphs():
    text = article_excerpt(ARTICLE_HTML)
    assert text.startswith("Paramount Skydance agreed") and "Subscribe" not in text and "CNN, HBO" in text


def test_ingest_files_the_story_under_the_article_and_keeps_the_quote(fresh_db, settings, now):
    client = httpx.Client(transport=httpx.MockTransport(_transport), follow_redirects=True)
    page = fetch_page(TWEET, client)
    with session() as s:
        story = ingest_url(s, settings, TWEET, now=now, page=page)
        item = s.scalar(select(Item))
        assert item.original_url == ARTICLE and item.publisher == "techcrunch.com"
        assert item.media[0] == {"type": "x_post", "url": TWEET, "author": "TechCrunch", "rights": "quote_ok", "provider": "x"}
        assert story.title.startswith("What to know about the landmark Warner Bros.")
        assert "Paramount Skydance" in item.summary
