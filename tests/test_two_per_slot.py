from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.db import session
from victor.models import Draft, Story
from victor.pipeline import HANDOFF_ABANDON_HOURS, run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_freshness import FakeTelegramAll
from .test_volume import busy_fetcher


def _fmt(**limits):
    settings = _settings()
    settings.raw["publishing"].update(prepare_media=False, auto_handoff=True)
    settings.raw["limits"].update(max_drafts_per_run=2, card_every_minutes=30, apply_to_manual=False, thread_every_slots=0)
    settings.raw["limits"].update(limits)
    settings.raw["drafting"].update(fallback_min_score=45, second_card_different_category=True)
    return settings


def test_two_cards_per_slot_from_different_categories(fresh_db, now):
    settings = _fmt()
    tg = FakeTelegramAll()
    stats = run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(),
                     check_links=False)
    with session() as s:  # the fixture is all AI stories: make the second-strongest a space story so the mix rule has a choice
        stories = s.scalars(select(Story).order_by(Story.score.desc())).all()
        for st in stories:
            d = s.scalar(select(Draft).where(Draft.story_id == st.id))
            if d:
                d.status, st.status = "skipped", "discovered"  # undo this cycle's cards; we re-run with a category mix
        stories[2].category = "space"
        from victor.pipeline import set_state
        set_state(s, "last_card_at", (now - timedelta(hours=1)).isoformat())
    tg2 = FakeTelegramAll()
    stats = run_once(settings, now + timedelta(minutes=1), fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg2,
                     publisher=MockPublisher(), check_links=False)
    assert stats["drafts_made"] == 2
    ready = [t for t, _ in tg2.buttons if t.startswith("READY TO POST")]
    assert len(ready) == 2
    with session() as s:
        cats = {d.story.category for d in s.scalars(select(Draft).where(Draft.status == "handed_off")).all()}
        assert cats == {"ai", "space"} or len(cats) == 2


def test_thread_rides_along_every_nth_slot(fresh_db, now):
    settings = _fmt(thread_every_slots=1)
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    assert any(t.startswith("THREAD on:") for t, _ in tg.buttons)


def test_handoffs_stay_a_day():
    assert HANDOFF_ABANDON_HOURS == 24
