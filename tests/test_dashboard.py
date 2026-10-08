from victor.ai.mock import MockProvider
from victor.channels.console import ConsoleChannel
from victor.dashboard import write_dashboard
from victor.db import session
from victor.pipeline import run_once
from victor.publish.mock import MockPublisher

from .test_pipeline import make_fetcher


def test_dashboard_renders_without_draft_text(fresh_db, settings, now, tmp_path):
    settings.raw["publishing"]["prepare_media"] = False
    run_once(settings, now, fetcher=make_fetcher(now), provider=MockProvider(), channel=ConsoleChannel(),
             publisher=MockPublisher(), check_links=False, dashboard=False)
    with session() as s:
        p = write_dashboard(s, settings, now, out_dir=tmp_path)
    html = p.read_text(encoding="utf-8")
    assert "GPT-6" in html and "DEV MODE" in html and "Sources" in html
    assert "[DEV MODE] NEW" not in html  # draft text stays private by default
    assert (tmp_path / ".nojekyll").exists()
