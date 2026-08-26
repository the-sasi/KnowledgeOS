"""Environment-driven configuration.

Values come from the process environment, which `.env` populates. Nothing is
hardcoded here and nothing is read from a config file yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Repo root: knowledgeos/config.py -> knowledgeos/ -> <root>
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader.

    Avoids a python-dotenv dependency. Existing environment variables always
    win, so `docker compose` service-level overrides are not clobbered.
    """
    if not path.is_file():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    raw_sec_dir: Path
    sec_user_agent: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=_database_url(),
            raw_sec_dir=REPO_ROOT / "data" / "raw" / "sec",
            sec_user_agent=os.environ.get(
                "SEC_USER_AGENT", "KnowledgeOS your-email@example.com"
            ),
            log_level=os.environ.get("LOG_LEVEL", "info").upper(),
        )


def _database_url() -> str:
    """Prefer DATABASE_URL; otherwise assemble it from the POSTGRES_* parts.

    Assembling from parts is what lets the compose service point at the
    `postgres` hostname while a host-side run points at localhost, without
    keeping two connection strings in sync.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    db = os.environ.get("POSTGRES_DB")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")

    missing = [
        name
        for name, value in (
            ("POSTGRES_USER", user),
            ("POSTGRES_PASSWORD", password),
            ("POSTGRES_DB", db),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Cannot build a database connection string. Set DATABASE_URL, or "
            f"these variables: {', '.join(missing)}. See .env.example."
        )

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


settings = Settings.from_env()
