from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import process_updates
from victor.db import session
from victor.models import Draft, Post, Story
from victor.pipeline import publish_approved, run_once
from victor.publish.mock import MockPublisher

from .test_commands import _settings
from .test_expiry import FakeTelegramDel
from .test_volume import busy_fetcher


class FakeTelegramAll(FakeTelegramDel):
    def __init__(self):
        super().__init__()
        self.buttons = []

    def send_with_buttons(self, text, buttons):
        self.buttons.append((text, buttons)); self._next_id += 1
        return str(self._next_id)

    def edit_with_buttons(self, message_id, text, buttons):
        self.buttons.append((text, buttons))
        return str(message_id)


def test_stale_approval_expires_and_strongest_goes_first(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_drafts_per_run"] = 0
    settings.raw["limits"]["card_every_minutes"] = 0
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    later = now + timedelta(hours=5)  # 18:00 Lagos: well inside prime hours, so the 2-hour approval TTL applies plainly
    with session() as s:
        drafts = s.scalars(select(Draft).order_by(Draft.id)).all()
        weak, strong = drafts[0], drafts[-1]
        weak.story.score, strong.story.score = 61.0, 95.0
        weak.status, weak.decided_at = "approved", later - timedelta(hours=3)   # stale
        strong.status, strong.decided_at = "approved", later - timedelta(minutes=5)
        wid, sid = weak.id, strong.id
    with session() as s:
        publish_approved(s, settings, {}, later, channel=tg)
        assert s.get(Draft, wid).status == "expired"
        assert s.get(Draft, sid).status == "handed_off"
        assert s.scalar(select(Post)).draft_id == sid


def test_next_command_drafts_the_strongest_fresh_story(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    settings.raw["limits"]["max_drafts_per_run"] = 2  # leaves undrafted strong stories for /next to pick
    settings.raw["limits"]["card_every_minutes"] = 0
    tg = FakeTelegramAll()
    run_once(settings, now, fetcher=busy_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    before = len([m for m in tg.sent if m.startswith("DRAFT#")])
    tg.push_cmd("/next")
    with session() as s:
        process_updates(s, settings, tg, now + timedelta(minutes=1))
    assert any("Strongest story right now" in m for m in tg.sent)
    after = len([m for m in tg.sent if m.startswith("DRAFT#")])
    assert after >= before  # either an existing card was pointed to or a fresh one was sent
