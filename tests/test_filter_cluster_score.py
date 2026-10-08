from datetime import timedelta

from victor.collect.fetch import RawItem
from victor.intelligence.cluster import Candidate, best_match, same_story
from victor.intelligence.filter import filter_reason
from victor.intelligence.score import StoryFacts, classify, score_story


def _raw(title, published, summary="", url="https://example.com/a"):
    return RawItem(None, url, url, title, summary, "Example", "", published, [])


def test_filter_drops_old_short_sponsored_and_non_english(settings, now):
    cfg = settings.filter
    assert filter_reason(_raw("A perfectly fine headline about AI", now - timedelta(hours=1)), cfg, now) is None
    assert filter_reason(_raw("A perfectly fine headline about AI", now - timedelta(days=3)), cfg, now) == "too_old"
    assert filter_reason(_raw("short one", now), cfg, now) == "title_too_short"
    assert filter_reason(_raw("Sponsored: the best AI laptops of 2026", now), cfg, now).startswith("drop_keyword")
    assert filter_reason(_raw("人工智能模型发布新版本并且非常强大，开发者反应热烈，市场关注度上升", now), cfg, now) == "language"


def test_cluster_matches_paraphrased_titles(now):
    cand = Candidate(1, "OpenAI releases new model GPT-6", ["OpenAI", "GPT-6"], now)
    ok, _ = same_story("OpenAI unveils its latest model, GPT-6", ["OpenAI", "GPT-6"], now, cand)
    assert ok
    ok, _ = same_story("NASA delays Artemis III to 2028", ["NASA", "Artemis"], now, cand)
    assert not ok


def test_cluster_respects_time_window(now):
    cand = Candidate(1, "OpenAI releases new model GPT-6", ["OpenAI", "GPT-6"], now - timedelta(days=5))
    assert best_match("OpenAI releases new model GPT-6", ["OpenAI", "GPT-6"], now, [cand]) is None


def test_single_community_source_never_trends(settings, now):
    f = StoryFacts(source_count=1, tier1_count=0, first_seen_at=now - timedelta(hours=1), last_updated_at=now,
                   category="ai_models", novelty=1.0, max_tier=4)
    score, parts, label = score_story(f, settings.scoring, now)
    assert label in ("developing", "ignore")
    assert score < settings.scoring["thresholds"]["trending"]


def test_official_plus_corroboration_is_hot_or_breaking(settings, now):
    f = StoryFacts(source_count=5, tier1_count=1, first_seen_at=now - timedelta(minutes=40), last_updated_at=now,
                   category="ai_models", novelty=1.0, max_tier=1)
    score, parts, label = score_story(f, settings.scoring, now)
    assert label in ("hot", "breaking"), (score, parts)


def test_single_fresh_official_announcement_can_trend(settings, now):
    f = StoryFacts(source_count=1, tier1_count=1, first_seen_at=now - timedelta(minutes=20), last_updated_at=now,
                   category="ai_models", novelty=1.0, max_tier=1)
    _, _, label = score_story(f, settings.scoring, now)
    assert label == "trending"


def test_classify_thresholds(settings):
    t = settings.scoring["thresholds"]
    assert classify(95, t) == "breaking" and classify(10, t) == "ignore"
