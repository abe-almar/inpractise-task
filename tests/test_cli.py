"""What the command line says when the database cannot be reached, and the defaults that `make run` depends on."""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest

from sec_filings import cli

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


def test_the_local_database_has_one_default_port_and_listens_on_localhost_only():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    default = re.search(r"^export DB_PORT \?= (\d+)$", makefile, re.M).group(1)
    assert "postgresql://sec:sec@localhost:$(DB_PORT)/sec_filings" in makefile
    assert f'"127.0.0.1:${{DB_PORT:-{default}}}:5432"' in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"DATABASE_URL=postgresql://sec:sec@localhost:{default}/sec_filings" in (ROOT / ".env.example").read_text(encoding="utf-8")
