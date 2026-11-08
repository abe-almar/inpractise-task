"""Configuration. Everything comes from environment variables; only DATABASE_URL is mandatory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = "sec-filings take-home abe.alcarazm@gmail.com"


class SettingsError(RuntimeError):
    """A setting is missing or invalid; the message says which and why."""


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, optional `export` and quotes, values taken literally, never overrides
    variables already set."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    database_url: str
    user_agent: str
    rate_limit_per_sec: float
    companies_file: Path
    overrides_file: Path
    http_timeout: float = 30.0
    max_retries: int = 5
    run_time_budget_sec: int = 600

    @classmethod
    def from_env(cls, env: dict | None = None, base_dir: str | os.PathLike | None = None) -> "Settings":
        env = dict(os.environ) if env is None else env
        base = Path(base_dir or os.getcwd())
        url = env.get("DATABASE_URL", "").strip()
        if not url:
            raise SettingsError(
                "DATABASE_URL is not set. Example: export DATABASE_URL=postgresql://sec:sec@localhost:25432/sec_filings"
            )
        user_agent = env.get("SEC_USER_AGENT", DEFAULT_USER_AGENT).strip()
        if "@" not in user_agent:
            raise SettingsError(
                "SEC_USER_AGENT must name you and a contact e-mail address; EDGAR's fair-access rules require it"
            )
        try:
            rate = float(env.get("SEC_RATE_LIMIT_PER_SEC", "5"))
        except ValueError as exc:
            raise SettingsError("SEC_RATE_LIMIT_PER_SEC must be a number") from exc
        if not 0 < rate <= 10:
            raise SettingsError("SEC_RATE_LIMIT_PER_SEC must be between 0 and 10 (EDGAR's published maximum is 10)")
        companies = base / env.get("COMPANIES_FILE", "companies.csv")
        overrides = base / env.get("OVERRIDES_FILE", "overrides.toml")
        if not companies.is_file():
            raise SettingsError(f"companies file not found: {companies}")
        return cls(
            database_url=url,
            user_agent=user_agent,
            rate_limit_per_sec=rate,
            companies_file=companies,
            overrides_file=overrides,
        )
