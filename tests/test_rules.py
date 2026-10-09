from datetime import datetime, timedelta, timezone

from victor.intelligence.rules import PostingHistory, decide, in_window, limits_ok

LAGOS_0200_UTC = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)  # 02:00 in Africa/Lagos (UTC+1)
LAGOS_1400_UTC = datetime(2026, 10, 8, 13, 0, tzinfo=timezone.utc)


def _decide(settings, **kw):
    base = dict(
        now_utc=LAGOS_0200_UTC, settings=settings.raw, story_category="ai_models",
        story_text="OpenAI releases GPT-6 with a 2M context window", story_entities=["OpenAI", "GPT-6"], story_id=1,
        tier1_count=1, other_count=3, checks_passed=True, hist=PostingHistory(), paused=False,
        mode="restricted_autonomous",
    )
    base.update(kw)
    return decide(**base)


def test_window_crosses_midnight():
    assert in_window(datetime(2026, 1, 1, 2, 0), "23:00", "07:00")
    assert in_window(datetime(2026, 1, 1, 23, 30), "23:00", "07:00")
    assert not in_window(datetime(2026, 1, 1, 12, 0), "23:00", "07:00")


def test_paused_blocks_everything(settings):
    d = _decide(settings, paused=True)
    assert not d.allowed and d.route == "blocked"


def test_failed_checks_go_to_review_not_block(settings):
    d = _decide(settings, checks_passed=False)
    assert d.allowed and d.route == "review" and "checks_failed" in d.reasons


def test_entity_cooldown_is_a_tag_not_a_block(settings):
    h = PostingHistory(entities_recent={"openai"})
    d = _decide(settings, hist=h, mode="approval")
    assert d.allowed and d.route == "review" and "entity_cooldown" in d.reasons


def test_approval_mode_always_reviews(settings):
    assert _decide(settings, mode="approval").route == "review"


def test_overnight_rule_met(settings):
    d = _decide(settings)
    assert d.route == "autonomous", d.reasons


def test_daytime_goes_to_review(settings):
    assert _decide(settings, now_utc=LAGOS_1400_UTC).route == "review"


def test_sensitive_topic_never_autonomous(settings):
    d = _decide(settings, story_text="OpenAI sued over alleged data breach")
    assert d.route == "review" and any(r.startswith("sensitive") for r in d.reasons)


def test_needs_tier1_and_corroboration(settings):
    assert "needs_tier1" in _decide(settings, tier1_count=0).reasons
    assert "needs_corroboration" in _decide(settings, other_count=1).reasons


def test_night_cap(settings):
    h = PostingHistory(autonomous_in_window=2)
    assert "night_cap_reached" in _decide(settings, hist=h).reasons


def test_category_not_allowed(settings):
    assert _decide(settings, story_category="cybersecurity").route == "review"


def test_story_cooldown_blocks(settings):
    h = PostingHistory(story_ids_recent={1})
    assert _decide(settings, hist=h).route == "blocked"


def test_limits(settings):
    now = LAGOS_0200_UTC
    h = PostingHistory(posted_at=[now - timedelta(minutes=10)])
    assert "min_gap" in limits_ok(now, settings.limits, h)
    h = PostingHistory(posted_at=[now - timedelta(hours=i) for i in range(1, 14)])
    assert "daily_limit" in limits_ok(now, settings.limits, h)
