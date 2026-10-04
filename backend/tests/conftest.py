from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

import contract
import pytest
from alembic import command
from alembic.config import Config
from starlette.testclient import TestClient

from swipe_rss.db import make_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

# Contract coverage (see contract.py): every response a test client receives is recorded; after a
# full, green run the recorded set must equal the responses documented in api/openapi.yaml.
_recorder = contract.Recorder(contract.load_spec())
_deselected = 0
_contract_problems: list[str] = []


@pytest.fixture(autouse=True, scope="session")
def _record_api_responses():
    original = TestClient.request

    def request(self, method, url, *args, **kwargs):
        response = original(self, method, url, *args, **kwargs)
        _recorder.record(method, response.request.url.path, response.status_code)
        return response

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TestClient, "request", request)
        yield


def pytest_deselected(items):
    global _deselected
    _deselected += len(items)


def _is_full_run(session) -> bool:
    # A partial run (one file, -k, -m, deselected tests, mutmut's subsets) can't judge coverage.
    option = session.config.option
    args = {Path(arg.split("::")[0]).resolve() for arg in session.config.args}
    return (
        not option.keyword
        and not option.markexpr
        and _deselected == 0
        and all("::" not in arg for arg in session.config.args)
        and args <= {TESTS_DIR, BACKEND_DIR}
    )


def pytest_sessionfinish(session, exitstatus):
    if exitstatus != pytest.ExitCode.OK or not _is_full_run(session):
        return
    _contract_problems.extend(_recorder.problems())
    if _contract_problems:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    if _contract_problems:
        terminalreporter.section("contract coverage (api/openapi.yaml)", red=True)
        for problem in _contract_problems:
            terminalreporter.write_line(problem)


def migrate(engine) -> None:
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.attributes["engine"] = engine
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def engine(db_path):
    engine = make_engine(db_path)
    migrate(engine)
    yield engine
    engine.dispose()


def rss(*entries: dict) -> bytes:
    """Minimal RSS 2.0 document. Entry keys: title, link, guid, published (datetime), description,
    author, categories (list of str)."""
    items = []
    for e in entries:
        parts = [f"<title>{e['title']}</title>"]
        if "link" in e:
            parts.append(f"<link>{e['link']}</link>")
        if "guid" in e:
            parts.append(f'<guid isPermaLink="false">{e["guid"]}</guid>')
        if "published" in e:
            published: datetime = e["published"]
            parts.append(f"<pubDate>{format_datetime(published)}</pubDate>")
        if "description" in e:
            parts.append(f"<description><![CDATA[{e['description']}]]></description>")
        if "author" in e:
            parts.append(f"<author>{e['author']}</author>")
        parts.extend(f"<category>{c}</category>" for c in e.get("categories", []))
        items.append(f"<item>{''.join(parts)}</item>")
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><link>https://example.com/</link>'
        f"<description>d</description>{''.join(items)}</channel></rss>"
    ).encode()
