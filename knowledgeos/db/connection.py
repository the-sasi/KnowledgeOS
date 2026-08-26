"""PostgreSQL connections.

Thin wrapper over psycopg. No pooling yet: the current workloads are one-shot
CLI runs, and a pool is worth adding when the API service exists.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from knowledgeos.config import settings


@contextmanager
def get_connection(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Open a connection with dict rows, closing it on exit.

    With autocommit=False the caller gets one transaction for the whole block,
    committed on clean exit and rolled back on exception.
    """
    conn = psycopg.connect(
        settings.database_url,
        row_factory=dict_row,
        autocommit=autocommit,
    )
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def check_connection() -> str:
    """Return the server version, or raise if unreachable."""
    with get_connection(autocommit=True) as conn:
        row = conn.execute("SELECT version() AS version").fetchone()
        return row["version"]
