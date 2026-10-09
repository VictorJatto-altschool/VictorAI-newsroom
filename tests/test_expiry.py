from datetime import timedelta

from sqlalchemy import select

from victor.ai.mock import MockProvider
from victor.channels.commands import expire_cards, process_updates
from victor.db import session
from victor.models import Draft
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_commands import FakeTelegram, _settings
from .test_pipeline import make_fetcher


class FakeTelegramDel(FakeTelegram):
    def __init__(self):
        super().__init__()
        self.deleted: list[str] = []

    def delete_message(self, message_id):
        self.deleted.append(str(message_id))
        return True


def test_stale_pending_cards_are_deleted_and_expired(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramDel()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        assert expire_cards(s, settings, tg, now + timedelta(minutes=10)) == 0  # still fresh
        n = expire_cards(s, settings, tg, now + timedelta(minutes=31))
        assert n == 1
        d = s.scalar(select(Draft))
        assert d.status == "expired" and d.channel_ref in tg.deleted


def test_reject_deletes_the_card(fresh_db, now):
    settings = _settings()
    settings.raw["publishing"]["prepare_media"] = False
    tg = FakeTelegramDel()
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=tg, publisher=MockPublisher(), check_links=False)
    with session() as s:
        d = s.scalar(select(Draft))
        did, ref = d.id, d.channel_ref
    tg.push_button(f"reject:{did}", ref)
    with session() as s:
        process_updates(s, settings, tg, now)
        assert s.get(Draft, did).status == "rejected"
    assert ref in tg.deleted
