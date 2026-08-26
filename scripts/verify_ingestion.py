#!/usr/bin/env python3
"""Show what ingestion actually put in PostgreSQL.

    python scripts/verify_ingestion.py

Read-only. Prints row counts, document status counts, processing job status
counts, a per-company breakdown, and any recorded failures.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledgeos.db.connection import check_connection, get_connection  # noqa: E402
from knowledgeos.db.models import DocumentStatus, JobStatus  # noqa: E402
from knowledgeos.db.repositories import StatsRepository  # noqa: E402


def _rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    try:
        version = check_connection()
    except Exception as exc:
        print(f"Cannot reach PostgreSQL: {exc}", file=sys.stderr)
        print("Is the stack up?  docker compose up -d", file=sys.stderr)
        return 1

    print("KnowledgeOS - ingestion verification")
    print(version.split(",")[0])

    with get_connection(autocommit=True) as conn:
        stats = StatsRepository(conn)

        totals = stats.totals()
        doc_status = stats.document_status_counts()
        job_status = stats.job_status_counts()
        companies = stats.companies_overview()
        failures = stats.recent_failures()

    _rule("Row counts")
    for table, count in totals.items():
        print(f"  {table:<22} {count:>8,}")

    _rule("Document status")
    # Show every status the model defines, including the zero ones, so an
    # empty FAILED bucket is visibly empty rather than merely absent.
    for status in DocumentStatus:
        print(f"  {status.value:<22} {doc_status.get(status.value, 0):>8,}")

    _rule("Processing jobs")
    if job_status:
        for key, count in sorted(job_status.items()):
            print(f"  {key:<40} {count:>8,}")
    else:
        for status in JobStatus:
            print(f"  DOCUMENT_PROCESSING / {status.value:<18} {0:>8,}")

    _rule("Companies")
    if companies:
        print(f"  {'CIK':<12} {'TICKER':<8} {'FILINGS':>8} {'DOCS':>6}  NAME")
        for row in companies:
            print(
                f"  {row['cik']:<12} {(row['ticker'] or '-'):<8} "
                f"{row['filings']:>8} {row['documents']:>6}  {row['name']}"
            )
    else:
        print("  (none)")

    if failures:
        _rule("Failed documents")
        for row in failures:
            print(f"  {row['file_name']}")
            print(f"      {row['error_message']}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
