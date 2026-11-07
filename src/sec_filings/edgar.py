"""HTTP client for EDGAR that respects the fair-access rules: declared User-Agent, gzip, a request rate well below
the published maximum of 10 per second, retries with backoff on 429/5xx, and a hard stop after a few retries."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{accession}-index.htm"

RETRY_STATUSES = {429, 500, 502, 503, 504}


class EdgarError(RuntimeError):
    """EDGAR could not be read after the retries, or answered with an unexpected status."""


class EdgarNotFound(EdgarError):
    """EDGAR answered 404 (unknown CIK or page)."""


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    http_errors: int = 0
    bytes: int = 0
    urls: list[str] = field(default_factory=list)


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        rate_limit_per_sec: float = 5.0,
        timeout: float = 30.0,
        max_retries: int = 5,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("user_agent must include a contact e-mail address")
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", "Accept": "application/json"}
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self._min_interval = 1.0 / rate_limit_per_sec
        self._last_request_at = 0.0
        self.stats = Stats()

    def _throttle(self) -> None:
        wait = self._last_request_at + self._min_interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last_request_at = self.clock()

    def get_bytes(self, url: str) -> bytes:
        last_error = "no attempt made"
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self.stats.requests += 1
            self.stats.urls.append(url)
            status: int | None = None
            try:
                response = self.session.get(url, headers=self.headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if status == 200:
                    self.stats.bytes += len(response.content)
                    return response.content
                if status == 404:
                    raise EdgarNotFound(f"{url}: HTTP 404")
                last_error = f"HTTP {status}"
                if status not in RETRY_STATUSES:
                    self.stats.http_errors += 1
                    raise EdgarError(f"{url}: {last_error}")
            self.stats.http_errors += 1
            if attempt == self.max_retries:
                raise EdgarError(f"{url}: giving up after {attempt} retries ({last_error})")
            backoff = min(2 ** attempt, 30) * (2 if status == 429 else 1)
            log.warning("EDGAR %s on %s; retry %d/%d in %ss", last_error, url, attempt + 1, self.max_retries, backoff)
            self.stats.retries += 1
            self.sleep(backoff)
        raise EdgarError(f"{url}: {last_error}")  # unreachable, keeps type checkers calm

    def get_json(self, url: str) -> tuple[Any, bytes]:
        raw = self.get_bytes(url)
        try:
            return json.loads(raw), raw
        except json.JSONDecodeError as exc:
            raise EdgarError(f"{url}: body is not JSON ({exc})") from exc

    def company_tickers(self) -> tuple[dict, bytes]:
        return self.get_json(TICKERS_URL)

    def submissions(self, cik: int) -> tuple[dict, bytes]:
        return self.get_json(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"))

    def submissions_page(self, name: str) -> tuple[dict, bytes]:
        return self.get_json(SUBMISSIONS_URL.format(name=name))

    def companyfacts(self, cik: int) -> tuple[dict, bytes]:
        return self.get_json(COMPANYFACTS_URL.format(cik=cik))


def document_url(cik: int, accession_number: str, document: str | None) -> str:
    nodash = accession_number.replace("-", "")
    if document:
        return ARCHIVE_DOC_URL.format(cik=cik, accession_nodash=nodash, document=document)
    return FILING_INDEX_URL.format(cik=cik, accession_nodash=nodash, accession=accession_number)
