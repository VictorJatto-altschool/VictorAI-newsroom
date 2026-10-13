import pytest
from sqlalchemy import select

from victor import pipeline
from victor.__main__ import _note_cycle_failure, _note_cycle_ok
from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.db import session
from victor.models import Run
from victor.pipeline import get_state, run_once
from victor.publish.mock import MockPublisher

from .test_pipeline import make_fetcher


def test_failed_cycle_leaves_a_run_row(fresh_db, settings, now, monkeypatch):
    settings.raw["publishing"]["prepare_media"] = False

    def boom(*a, **k):
        raise RuntimeError("index row size 3320 exceeds btree maximum")

    monkeypatch.setattr(pipeline, "cluster_items", boom)
    with pytest.raises(RuntimeError):
        run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(),
                 publisher=MockPublisher(), check_links=False)
    with session() as s:
        run = s.scalar(select(Run).order_by(Run.id.desc()))
        assert run is not None and run.ok is False and "btree" in run.error and run.finished_at is not None


class Phone:
    def __init__(self):
        self.sent = []

    def notify(self, text):
        self.sent.append(text)


def test_second_failure_in_a_row_messages_the_phone_once(fresh_db):
    tg = Phone()
    _note_cycle_failure(tg, "OperationalError: boom")
    assert tg.sent == []  # one failure can be a blip
    _note_cycle_failure(tg, "OperationalError: boom")
    assert len(tg.sent) == 1 and "failed 2 times in a row" in tg.sent[0]
    _note_cycle_failure(tg, "OperationalError: boom")
    assert len(tg.sent) == 1  # not again within the hour
    _note_cycle_ok(tg)
    assert len(tg.sent) == 2 and "back" in tg.sent[1]
    with session() as s:
        assert get_state(s, "cycle_fail_count", "0") == "0"
