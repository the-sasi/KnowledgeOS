"""CLI entry point for document processing.

    python -m services.processing                  # process what needs it
    python -m services.processing --force          # reprocess everything
    python -m services.processing --limit 5        # first N documents
    python -m services.processing --document <uuid>
"""

from __future__ import annotations

import argparse
import sys
import uuid

from knowledgeos.logging_setup import get_logger
from services.processing.pipeline import process_pending

log = get_logger("processing.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run KnowledgeOS document processing")
    parser.add_argument("--limit", type=int, help="process at most N documents")
    parser.add_argument(
        "--force",
        action="store_true",
        help="reprocess even when the recorded processor version already matches",
    )
    parser.add_argument("--document", help="process a single document by UUID")
    args = parser.parse_args(argv)

    document_id = None
    if args.document:
        try:
            document_id = uuid.UUID(args.document)
        except ValueError:
            parser.error(f"--document expects a UUID, got {args.document!r}")

    try:
        stats = process_pending(
            limit=args.limit, force=args.force, document_id=document_id
        )
    except Exception as exc:
        log.error("Processing aborted: %s: %s", type(exc).__name__, exc)
        return 1

    if stats.errors:
        log.warning("Completed with %d error(s):", len(stats.errors))
        for err in stats.errors[:20]:
            log.warning("  - %s", err)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
