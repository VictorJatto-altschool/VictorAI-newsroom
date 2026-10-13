from victor.collect.normalize import MAX_CANONICAL_CHARS, canonical_url


def test_monster_addresses_get_a_short_stable_canonical_form():
    base = "https://news.example.com/articles/" + "x" * 4000
    a, b = canonical_url(base), canonical_url(base)
    assert a == b and len(a) <= MAX_CANONICAL_CHARS
    assert a != canonical_url(base + "y")  # a different address stays distinct
    assert canonical_url("https://example.com/short") == "https://example.com/short"
