"""EdgarClient against a fake HTTP session and an injected clock: retries, the hard stop, 404 and 4xx handling,
the throttle and the fair-access headers (docs/decisions.md D6). Nothing here opens a socket."""

from __future__ import annotations

import json

import pytest
import requests

from sec_filings import edgar
from sec_filings.edgar import EdgarClient, EdgarError, EdgarNotFound, Stats, document_url


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


class FakeClock:
    """A monotonic clock the test controls; sleeping advances it, like the real thing would."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSession:
    """Answers each .get() with the next scripted item: a FakeResponse or an exception to raise."""

    def __init__(self, script: list, clock: FakeClock) -> None:
        self.script = list(script)
        self.clock = clock
        self.calls: list[dict] = []

    def get(self, url: str, headers: dict, timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout, "at": self.clock()})
        if not self.script:
            raise AssertionError(f"unexpected request #{len(self.calls)} to {url}")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make_client(script: list, **kwargs) -> tuple[EdgarClient, FakeSession, FakeClock]:
    clock = FakeClock()
    session = FakeSession(script, clock)
    options = {"user_agent": "sec-filings tests tests@example.com", "session": session, "sleep": clock.sleep, "clock": clock}
    options.update(kwargs)
    client = EdgarClient(**options)
    return client, session, clock


URL = "https://data.sec.gov/submissions/CIK0000320193.json"


def test_503_then_200_is_one_retry():
    client, session, clock = make_client([FakeResponse(503), FakeResponse(200, b'{"cik": "0000320193"}')])
    data, raw = client.get_json(URL)
    assert data == {"cik": "0000320193"}
    assert raw == b'{"cik": "0000320193"}'
    assert len(session.calls) == 2
    assert client.stats.retries == 1
    assert client.stats.http_errors == 1
    assert client.stats.requests == 2
    assert client.stats.bytes == len(raw)
    assert client.stats.urls == [URL, URL]
    assert clock.sleeps == [1.0]          # 2**0 seconds of backoff; the throttle had nothing to wait for after it


def test_five_consecutive_503s_give_up():
    # max_retries=4: the first attempt plus four retries; the queued 200 must never be requested
    client, session, clock = make_client([FakeResponse(503)] * 5 + [FakeResponse(200, b"{}")], max_retries=4)
    with pytest.raises(EdgarError, match="giving up after 4 retries \\(HTTP 503\\)"):
        client.get_bytes(URL)
    assert len(session.calls) == 5
    assert client.stats.requests == 5
    assert client.stats.retries == 4
    assert client.stats.http_errors == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0]   # exponential backoff between attempts, none after the last


def test_default_is_five_retries():
    client, session, _ = make_client([FakeResponse(502)] * 6)
    with pytest.raises(EdgarError, match="giving up after 5 retries"):
        client.get_bytes(URL)
    assert len(session.calls) == 6 and client.stats.retries == 5


def test_429_doubles_the_backoff():
    client, _, clock = make_client([FakeResponse(429), FakeResponse(429), FakeResponse(200, b"{}")])
    client.get_bytes(URL)
    assert clock.sleeps == [2.0, 4.0]
    assert client.stats.retries == 2 and client.stats.http_errors == 2


def test_404_raises_not_found_without_retrying():
    client, session, clock = make_client([FakeResponse(404), FakeResponse(200, b"{}")])
    with pytest.raises(EdgarNotFound, match="HTTP 404"):
        client.get_bytes(URL)
    assert issubclass(EdgarNotFound, EdgarError)
    assert len(session.calls) == 1
    assert client.stats.requests == 1
    assert client.stats.retries == 0
    assert client.stats.http_errors == 0   # an unknown page is not an EDGAR outage
    assert clock.sleeps == []


def test_400_raises_without_retrying():
    client, session, clock = make_client([FakeResponse(400), FakeResponse(200, b"{}")])
    with pytest.raises(EdgarError, match="HTTP 400") as info:
        client.get_bytes(URL)
    assert not isinstance(info.value, EdgarNotFound)
    assert len(session.calls) == 1
    assert client.stats.requests == 1
    assert client.stats.retries == 0
    assert client.stats.http_errors == 1
    assert clock.sleeps == []


def test_connection_errors_and_timeouts_are_retried():
    client, session, clock = make_client([requests.ConnectionError("reset"), requests.Timeout("slow"), FakeResponse(200, b"ok")])
    assert client.get_bytes(URL) == b"ok"
    assert len(session.calls) == 3
    assert client.stats.retries == 2 and client.stats.http_errors == 2
    assert clock.sleeps == [1.0, 2.0]


def test_throttle_spaces_back_to_back_requests():
    rate = 5.0
    client, session, clock = make_client([FakeResponse(200, b"a"), FakeResponse(200, b"b"), FakeResponse(200, b"c")], rate_limit_per_sec=rate)
    client.get_bytes(URL)
    client.get_bytes(URL)
    client.get_bytes(URL)
    interval = 1.0 / rate
    assert len(clock.sleeps) == 2
    assert all(s == pytest.approx(interval) for s in clock.sleeps)
    times = [c["at"] for c in session.calls]
    assert times[1] - times[0] >= interval - 1e-9
    assert times[2] - times[1] >= interval - 1e-9


def test_throttle_does_not_sleep_when_enough_time_has_passed():
    client, _, clock = make_client([FakeResponse(200, b"a"), FakeResponse(200, b"b")], rate_limit_per_sec=5.0)
    client.get_bytes(URL)
    clock.now += 1.0
    client.get_bytes(URL)
    assert clock.sleeps == []


def test_headers_declare_user_agent_and_gzip():
    client, session, _ = make_client([FakeResponse(200, b"{}")], timeout=12.5)
    client.get_bytes(URL)
    headers = session.calls[0]["headers"]
    assert headers["User-Agent"] == "sec-filings tests tests@example.com"
    assert "gzip" in headers["Accept-Encoding"]
    assert headers["Accept"] == "application/json"
    assert session.calls[0]["timeout"] == 12.5


def test_user_agent_needs_a_contact_address():
    with pytest.raises(ValueError, match="e-mail"):
        EdgarClient("sec-filings tests")
    with pytest.raises(ValueError):
        EdgarClient("")


def test_non_json_body_is_an_error():
    client, _, _ = make_client([FakeResponse(200, b"<html>maintenance</html>")])
    with pytest.raises(EdgarError, match="not JSON"):
        client.get_json(URL)


def test_urls_of_the_named_endpoints():
    client, session, _ = make_client([FakeResponse(200, b"{}")] * 4)
    client.company_tickers()
    client.submissions(320193)
    client.submissions_page("CIK0000320193-submissions-001.json")
    client.companyfacts(34088)
    assert [c["url"] for c in session.calls] == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000034088.json",
    ]
    assert client.stats.urls == [c["url"] for c in session.calls]


def test_document_url():
    assert document_url(320193, "0000320193-26-000026", "aapl-20260926.htm") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm"
    )
    assert document_url(320193, "0000320193-94-000016", None) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019394000016/0000320193-94-000016-index.htm"
    )
    assert document_url(320193, "0000320193-94-000016", "") == document_url(320193, "0000320193-94-000016", None)


def test_stats_start_empty():
    assert Stats() == Stats(requests=0, retries=0, http_errors=0, bytes=0, urls=[])
    assert json.loads(b"{}") == {}


def test_retry_statuses_are_rate_limits_timeouts_and_every_5xx():
    assert edgar.RETRY_STATUSES == {408, 429} | set(range(500, 600))
    assert {500, 502, 503, 504, 599} <= edgar.RETRY_STATUSES
    assert not {400, 401, 403, 404, 410, 418, 451} & edgar.RETRY_STATUSES


@pytest.mark.parametrize("status", [408, 500, 501, 520, 599])
def test_retried_statuses_are_retried_with_plain_backoff(status):
    client, session, clock = make_client([FakeResponse(status), FakeResponse(200, b"ok")])
    assert client.get_bytes(URL) == b"ok"
    assert len(session.calls) == 2
    assert client.stats.retries == 1 and client.stats.http_errors == 1
    assert clock.sleeps == [1.0]   # only 429 doubles the backoff


@pytest.mark.parametrize("status", [401, 403, 410, 418, 451])
def test_other_4xx_fail_at_once(status):
    client, session, clock = make_client([FakeResponse(status), FakeResponse(200, b"ok")])
    with pytest.raises(EdgarError, match=f"HTTP {status}"):
        client.get_bytes(URL)
    assert len(session.calls) == 1 and client.stats.retries == 0 and clock.sleeps == []
