from victor.collect.normalize import canonical_url, clean_title, extract_entities, normalize_publisher


def test_canonical_url_strips_tracking_and_host_noise():
    a = canonical_url("https://www.TechCrunch.com/2026/10/08/openai-model/?utm_source=x&utm_medium=social#top")
    b = canonical_url("http://techcrunch.com/2026/10/08/openai-model")
    assert a == b == "https://techcrunch.com/2026/10/08/openai-model"


def test_canonical_url_keeps_meaningful_query():
    assert canonical_url("https://youtube.com/watch?v=abc&utm_campaign=z") == "https://youtube.com/watch?v=abc"


def test_normalize_publisher_merges_variants():
    assert normalize_publisher("OpenAI News") == normalize_publisher("OpenAI") == normalize_publisher("openai.com")
    assert normalize_publisher("The Verge AI") == normalize_publisher("The Verge")
    assert normalize_publisher("NASA News") != normalize_publisher("Space.com")


def test_clean_title_drops_publisher_suffix():
    assert clean_title("OpenAI ships new model - TechCrunch", "TechCrunch") == "OpenAI ships new model"


def test_extract_entities_finds_known_names():
    ents = extract_entities("OpenAI and NVIDIA announce a Blackwell partnership for ChatGPT.")
    assert {"OpenAI", "NVIDIA", "ChatGPT"} <= set(ents)
