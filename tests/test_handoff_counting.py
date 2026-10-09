from datetime import timedelta

from sqlalchemy import select

from victor.channels.console import ConsoleChannel
from victor.db import session
from victor.models import Draft, Post, Story
from victor.pipeline import _history, abandon_stale_handoffs


def _handoff(s, when, status="manual"):
    st = Story(title=f"S {when:%H%M}", category="tech", entities=[], source_count=2, score=70, classification="hot", status="posted")
    s.add(st); s.flush()
    d = Draft(story=st, text="x", provider="mock", checks={"passed": True}, checks_passed=True, status="handed_off", channel_ref="1", created_at=when)
    s.add(d); s.flush()
    p = Post(draft=d, story_id=st.id, idempotency_key=f"k{when.timestamp()}", text="x", status=status, created_at=when)
    s.add(p); s.flush()
    return p


def test_only_confirmed_and_fresh_handoffs_count(fresh_db, settings, now):
    with session() as s:
        _handoff(s, now - timedelta(hours=5))               # old, never posted: must not count
        _handoff(s, now - timedelta(hours=1))               # fresh hand-off: counts for spacing
        _handoff(s, now - timedelta(hours=8), "published")  # confirmed: counts
        h = _history(s, settings, now)
        assert len(h.posted_at) == 2


def test_stale_handoffs_are_abandoned(fresh_db, settings, now):
    with session() as s:
        _handoff(s, now - timedelta(hours=7))
        _handoff(s, now - timedelta(hours=1))
        assert abandon_stale_handoffs(s, ConsoleChannel(), now) == 1
        statuses = sorted(p.status for p in s.scalars(select(Post)).all())
        assert statuses == ["manual", "skipped"]
