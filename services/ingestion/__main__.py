"""CLI entry point for SEC ingestion.

    python -m services.ingestion              # scrape SEC EDGAR, then persist
    python -m services.ingestion --from-disk  # persist data/raw/sec/, no network
"""

from __future__ import annotations

import argparse
import sys

from knowledgeos.logging_setup import get_logger
from services.ingestion.pipeline import ingest_from_disk, ingest_from_sec

log = get_logger("ingestion.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run KnowledgeOS SEC ingestion")
    parser.add_argument(
        "--from-disk",
        action="store_true",
        help="ingest filings already in data/raw/sec/ without contacting SEC",
    )
    parser.add_argument(
        "--company",
        action="append",
        metavar="NAME",
        help="limit the scrape to these companies (repeatable); names come from scraper.COMPANIES",
    )
    args = parser.parse_args(argv)

    try:
        if args.from_disk:
            stats = ingest_from_disk()
        else:
            companies = None
            if args.company:
                from services.ingestion.sec import scraper

                unknown = [c for c in args.company if c not in scraper.COMPANIES]
                if unknown:
                    parser.error(
                        f"unknown company {unknown}; known: {sorted(scraper.COMPANIES)}"
                    )
                companies = {c: scraper.COMPANIES[c] for c in args.company}
            stats = ingest_from_sec(companies)
    except Exception as exc:
        log.error("Ingestion aborted: %s: %s", type(exc).__name__, exc)
        return 1

    if stats.errors:
        log.warning("Completed with %d error(s):", len(stats.errors))
        for err in stats.errors[:20]:
            log.warning("  - %s", err)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
