"""CLI entry point for chunking.

    python -m services.chunking
    python -m services.chunking --max-tokens 1024 --overlap-tokens 64
    python -m services.chunking --strategy structure_recursive --limit 5
    python -m services.chunking --force --document <uuid>
    python -m services.chunking --list-strategies
"""

from __future__ import annotations

import argparse
import sys
import uuid

from knowledgeos.logging_setup import get_logger
from services.chunking.engine import DEFAULT_STRATEGY, available_strategies
from services.chunking.models import ChunkingConfig
from services.chunking.pipeline import chunk_pending
from services.chunking.tokenizer import available_tokenizers

log = get_logger("chunking.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run KnowledgeOS chunking")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--min-tokens", type=int, default=64)
    parser.add_argument("--overlap-tokens", type=int, default=0)
    parser.add_argument("--tokenizer", default="simple")
    parser.add_argument(
        "--path-prefix",
        action="store_true",
        help="prepend the section path to each chunk's text",
    )
    parser.add_argument("--limit", type=int, help="consider at most N documents")
    parser.add_argument("--document", help="chunk a single document by UUID")
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild the chunk set even if one already exists for this run key",
    )
    parser.add_argument(
        "--list-strategies", action="store_true", help="show what is registered and exit"
    )
    args = parser.parse_args(argv)

    if args.list_strategies:
        print("strategies:", ", ".join(available_strategies()))
        print("tokenizers:", ", ".join(available_tokenizers()))
        return 0

    document_id = None
    if args.document:
        try:
            document_id = uuid.UUID(args.document)
        except ValueError:
            parser.error(f"--document expects a UUID, got {args.document!r}")

    try:
        config = ChunkingConfig(
            max_tokens=args.max_tokens,
            min_tokens=args.min_tokens,
            overlap_tokens=args.overlap_tokens,
            tokenizer=args.tokenizer,
            include_path_prefix=args.path_prefix,
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        stats = chunk_pending(
            strategy=args.strategy,
            config=config,
            limit=args.limit,
            force=args.force,
            document_id=document_id,
        )
    except Exception as exc:
        log.error("Chunking aborted: %s: %s", type(exc).__name__, exc)
        return 1

    if stats.errors:
        log.warning("Completed with %d error(s):", len(stats.errors))
        for err in stats.errors[:20]:
            log.warning("  - %s", err)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
