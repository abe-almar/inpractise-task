"""What the command line says when the database cannot be reached, and the defaults that `make run` depends on."""

from __future__ import annotations

import re
import socket
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest

import psycopg

from sec_filings import cli, db, loader
from sec_filings.flow import RunResult

ROOT = Path(__file__).resolve().parent.parent


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("command", [["run"], ["backfill"], ["check"], ["summary"], ["verify-labels"], ["report", "AAPL", "2009"]], ids=lambda c: c[0])
def test_an_unreachable_database_is_one_line_not_a_traceback(command, monkeypatch, capsys):
    """On a second machine a taken port ended in a stack trace that never named the problem."""
    port = _closed_port()
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://sec:s3cret@127.0.0.1:{port}/sec_filings")
    assert cli.main(command) == 2
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"error: cannot connect to the database at 127.0.0.1:{port} ")
    assert "DB_PORT=<free port> make run" in lines[0]
    assert "s3cret" not in lines[0]


def _driver_says(monkeypatch, text: str) -> None:
    def refuse(*args, **kwargs):
        raise psycopg.OperationalError(text)
    monkeypatch.setattr(db.psycopg, "connect", refuse)


def test_the_message_masks_a_password_the_driver_repeats(monkeypatch):
    _driver_says(monkeypatch, "bad login with password s3cret\nsecond line s3cret")
    with pytest.raises(db.DatabaseUnavailable) as caught:
        db.connect("postgresql://sec:s3cret@db.example:5433/filings")
    assert "at db.example:5433 (bad login with password ***)" in str(caught.value)
    assert "s3cret" not in str(caught.value) and "\n" not in str(caught.value)
    assert caught.value.__suppress_context__        # the driver's text does not come back through the traceback


@pytest.mark.parametrize("url, where", [
    ("postgresql:///filings", "default host:default port"),            # libpq's own defaults, which may be PGHOST and PGPORT
    ("hostaddr=192.0.2.1 dbname=filings", "192.0.2.1:default port"),
    ("postgresql://sec@bad%0Ahost:1/filings", "bad host:1"),           # the message stays on one line
])
def test_the_message_does_not_invent_an_address(monkeypatch, url, where):
    _driver_says(monkeypatch, "refused")
    with pytest.raises(db.DatabaseUnavailable) as caught:
        db.connect(url)
    assert f"at {where} (refused)" in str(caught.value) and "\n" not in str(caught.value)


def test_a_malformed_url_is_reported_without_quoting_it():
    with pytest.raises(db.DatabaseUnavailable, match="^DATABASE_URL is not a valid connection string$") as caught:
        db.connect("not a url, password s3cret")
    assert caught.value.__suppress_context__


def test_the_local_database_has_one_default_port_and_listens_on_localhost_only():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    default = re.search(r"^override DB_PORT := \$\(or \$\(DB_PORT\),(\d+)\)$", makefile, re.M).group(1)
    assert "postgresql://sec:sec@localhost:$(DB_PORT)/sec_filings" in makefile
    assert f'"127.0.0.1:${{DB_PORT:-{default}}}:5432"' in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"DATABASE_URL=postgresql://sec:sec@localhost:{default}/sec_filings" in (ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.mark.parametrize("url, expected", [
    ("postgresql://sec:s3cret@localhost:25432/sec_filings", "localhost:25432/sec_filings"),
    ("postgresql:///filings", "default host:default port/filings"),
    ("hostaddr=192.0.2.1 dbname=filings", "192.0.2.1:default port/filings"),
    ("postgresql://sec:s3cret@db:5432", "db:5432/default database"),
    ("postgresql://sec:s3cret@bad%0Ahost:1/filings", "bad host:1/filings"),
    ("not a url, password s3cret", "(invalid DATABASE_URL)"),
])
def test_describe_names_host_port_and_database_without_credentials(url, expected):
    assert db.describe(url) == expected
    assert "s3cret" not in db.describe(url)


class _Closable:
    def close(self) -> None:
        pass


def test_every_command_names_the_database_it_uses(monkeypatch, capsys):
    """Review passes 7 and 8 found a green run against the wrong database, and nothing in its output said which."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("DATABASE_URL", "postgresql://sec:s3cret@localhost:25432/sec_filings")
    line = "database: localhost:25432/sec_filings"

    monkeypatch.setattr(cli, "daily_run", lambda settings, mode, full=False: RunResult(run_id=1, mode=mode, status="ok"))
    assert cli.main(["run"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == line and out[-1] == "RESULT: OK"

    monkeypatch.setattr(cli.db, "connect", lambda url: _Closable())
    monkeypatch.setattr(cli.report, "latest_run", lambda conn: None)
    assert cli.main(["check"]) == 1
    assert capsys.readouterr().out.splitlines() == [line, "no run on record"]

    monkeypatch.setattr(cli.report, "summary", lambda conn: [])
    assert cli.main(["summary"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.strip() == line      # the JSON commands keep stdout for the rows


def test_check_says_whether_the_inputs_changed_since_the_run(monkeypatch, capsys):
    """The stored checks were evaluated against the companies.csv and overrides.toml of that moment."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("DATABASE_URL", "postgresql://sec:sec@localhost:25432/sec_filings")
    monkeypatch.setattr(cli.db, "connect", lambda url: _Closable())
    monkeypatch.setattr(cli.report, "checks_of_run", lambda conn, run_id: [])
    current = loader.fingerprint_inputs(ROOT / "companies.csv", ROOT / "overrides.toml")
    run = {"id": 7, "mode": "daily", "started_at": datetime.now(timezone.utc), "status": "ok", "ciks_fetched": 21,
           "ciks_expected": 21, "filings_inserted": 0, "http_errors": 0, "error": None, "notes": {"inputs": current}}
    monkeypatch.setattr(cli.report, "latest_run", lambda conn: run)

    assert cli.main(["check"]) == 0
    assert "[ok] inputs_unchanged: companies.csv and overrides.toml are the files this run read" in capsys.readouterr().out.splitlines()

    run["notes"] = {"inputs": {**current, "overrides_toml": {"path": "overrides.toml", "sha256": "0" * 64}}}
    assert cli.main(["check"]) == 0                                     # a warning, never an exit code
    warnings = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[WARN] inputs_unchanged")]
    assert len(warnings) == 1
    assert "overrides.toml changed since this run (sha256 000000000000 -> " in warnings[0] and "companies.csv changed" not in warnings[0]

    run["notes"] = {}
    assert cli.main(["check"]) == 0
    assert "[info] inputs_unchanged: this run recorded no input fingerprints" in capsys.readouterr().out.splitlines()


def test_every_dependency_is_pinned_in_constraints():
    """A fresh clone installs the versions in constraints.txt, not whatever PyPI serves that morning."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {re.match(r"[A-Za-z0-9_.-]+", d).group(0).lower().replace("_", "-")
                for d in project["dependencies"] + project["optional-dependencies"]["dev"]}
    pinned = {}
    for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.count("==") == 1, f"not an exact pin: {line}"
            name, version = line.split("==")
            pinned[name.lower().replace("_", "-")] = version
    assert declared <= set(pinned), declared - set(pinned)
    assert '-c constraints.txt -e ".[dev]"' in (ROOT / "Makefile").read_text(encoding="utf-8")
