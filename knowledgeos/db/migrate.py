"""Migration runner.

Applies the numbered .sql files in infrastructure/database/migrations/ in
order, once each, tracked in a schema_migrations table. Deliberately not
Alembic: plain SQL files keep the schema readable and reviewable, and the
project has no ORM to autogenerate from.

    python -m knowledgeos.db.migrate            # apply pending migrations
    python -m knowledgeos.db.migrate --status   # show what is applied
    python -m knowledgeos.db.migrate --reset    # drop and re-apply everything
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from knowledgeos.config import REPO_ROOT
from knowledgeos.db.connection import get_connection
from knowledgeos.logging_setup import get_logger

log = get_logger("knowledgeos.db.migrate")

MIGRATIONS_DIR = REPO_ROOT / "infrastructure" / "database" / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT        PRIMARY KEY,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def discover() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"No migrations directory at {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def apply_pending() -> int:
    """Apply every migration not yet recorded. Returns how many ran."""
    migrations = discover()
    if not migrations:
        log.warning("No migration files found in %s", MIGRATIONS_DIR)
        return 0

    applied_count = 0

    with get_connection() as conn:
        conn.execute(_TRACKING_TABLE)
        rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        applied = {r["version"]: r["checksum"] for r in rows}

        for path in migrations:
            version = path.stem
            checksum = _checksum(path)

            if version in applied:
                if applied[version] != checksum:
                    # An already-applied file changed. Silently re-running it
                    # would corrupt the schema, so refuse.
                    raise RuntimeError(
                        f"Migration {version} was already applied but its "
                        f"contents changed (recorded {applied[version]}, now "
                        f"{checksum}). Add a new migration instead of editing "
                        f"an applied one, or run with --reset in development."
                    )
                log.debug("Migration already applied: %s", version)
                continue

            log.info("Applying migration: %s", version)
            # Each migration runs inside the outer transaction, so a failure
            # anywhere leaves the schema untouched.
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, checksum),
            )
            applied_count += 1

    if applied_count:
        log.info("Applied %d migration(s)", applied_count)
    else:
        log.info("Database is up to date; nothing to apply")
    return applied_count


def status() -> None:
    with get_connection(autocommit=True) as conn:
        conn.execute(_TRACKING_TABLE)
        rows = conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        applied = {r["version"]: r["applied_at"] for r in rows}

    print(f"{'MIGRATION':<40} {'STATUS':<10} APPLIED AT")
    print("-" * 80)
    for path in discover():
        version = path.stem
        if version in applied:
            print(f"{version:<40} {'applied':<10} {applied[version]:%Y-%m-%d %H:%M:%S}")
        else:
            print(f"{version:<40} {'pending':<10} -")


def reset() -> None:
    """Drop everything in the public schema and re-apply from scratch.

    Development only. This destroys all ingested records; the raw SEC files on
    disk are untouched.
    """
    log.warning("Resetting schema: dropping and recreating the public schema")
    with get_connection() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    apply_pending()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply KnowledgeOS database migrations")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="show migration status")
    group.add_argument(
        "--reset",
        action="store_true",
        help="DROP the public schema and re-apply all migrations (destroys data)",
    )
    args = parser.parse_args(argv)

    try:
        if args.status:
            status()
        elif args.reset:
            reset()
        else:
            apply_pending()
    except Exception as exc:
        log.error("Migration failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
