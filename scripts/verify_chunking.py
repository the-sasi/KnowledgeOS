#!/usr/bin/env python3
"""Inspect what the chunking stage produced.

    python scripts/verify_chunking.py                      # summary
    python scripts/verify_chunking.py --runs               # chunk sets side by side
    python scripts/verify_chunking.py --by-document        # chunks per document
    python scripts/verify_chunking.py --by-path            # chunks per section path
    python scripts/verify_chunking.py --file <name>        # one document's chunks
    python scripts/verify_chunking.py --file <name> --chunk 12   # one chunk in full

Read-only. Chunk quality has to be eyeballed before embeddings, which is what
`--chunk` is for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledgeos.db.connection import get_connection  # noqa: E402


def _rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _run_filter(args) -> tuple[str, list]:
    clauses, params = [], []
    if args.strategy:
        clauses.append("strategy = %s")
        params.append(args.strategy)
    if args.config_hash:
        clauses.append("config_hash = %s")
        params.append(args.config_hash)
    return (" AND ".join(clauses) or "TRUE"), params


def summary(conn, args) -> None:
    where, params = _run_filter(args)

    totals = conn.execute(
        f"""
        SELECT count(*) AS chunks,
               count(DISTINCT document_id) AS documents,
               round(avg(token_count), 1) AS avg_tokens,
               min(token_count) AS min_tokens,
               max(token_count) AS max_tokens,
               sum(token_count) AS total_tokens,
               round(avg(char_count), 1) AS avg_chars,
               count(*) FILTER (WHERE has_table) AS with_tables,
               count(*) FILTER (WHERE metadata ? 'oversized') AS oversized,
               count(*) FILTER (WHERE metadata ? 'table_split') AS table_parts,
               count(*) FILTER (WHERE metadata ? 'hard_split') AS hard_splits,
               count(*) FILTER (WHERE metadata ? 'overlap_tokens') AS with_overlap
          FROM document_chunks WHERE {where}
        """,
        params,
    ).fetchone()

    docs_total = conn.execute(
        "SELECT count(*) AS n FROM documents WHERE status = 'PROCESSED'"
    ).fetchone()["n"]

    _rule("Coverage")
    print(f"  processed documents     {docs_total:>9,}")
    print(f"  documents chunked       {totals['documents'] or 0:>9,}")
    print(f"  chunks                  {totals['chunks'] or 0:>9,}")

    if not totals["chunks"]:
        print("\n  (no chunks yet - run: python -m services.chunking)")
        return

    _rule("Token counts")
    print(f"  average                 {totals['avg_tokens']:>9}")
    print(f"  min / max               {totals['min_tokens']:>9,} / {totals['max_tokens']:,}")
    print(f"  total                   {totals['total_tokens']:>9,}")
    print(f"  average characters      {totals['avg_chars']:>9}")

    _rule("Composition")
    print(f"  chunks containing a table {totals['with_tables']:>7,}")
    print(f"  table parts (split table) {totals['table_parts']:>7,}")
    print(f"  over budget (unsplittable){totals['oversized']:>7,}")
    print(f"  hard sentence splits      {totals['hard_splits']:>7,}")
    print(f"  chunks with overlap       {totals['with_overlap']:>7,}")

    rows = conn.execute(
        f"""
        SELECT width_bucket(token_count, 0, GREATEST(max_over.m, 1), 10) AS bucket,
               count(*) AS n, min(token_count) AS lo, max(token_count) AS hi
          FROM document_chunks,
               LATERAL (SELECT max(token_count) AS m FROM document_chunks
                         WHERE {where}) AS max_over
         WHERE {where}
         GROUP BY bucket ORDER BY bucket
        """,
        params + params,
    ).fetchall()

    _rule("Size distribution")
    peak = max((r["n"] for r in rows), default=1)
    for r in rows:
        bar = "#" * max(1, round(40 * r["n"] / peak))
        print(f"  {r['lo']:>5,}-{r['hi']:<5,} {r['n']:>6,}  {bar}")

    failures = conn.execute(
        """
        SELECT d.file_name, j.status::text AS status, j.error_message, j.attempts
          FROM processing_jobs j JOIN documents d ON d.id = j.document_id
         WHERE j.job_type = 'CHUNKING' AND j.status = 'FAILED'
         ORDER BY j.updated_at DESC LIMIT 10
        """
    ).fetchall()

    _rule("Chunking jobs")
    for r in conn.execute(
        """
        SELECT status::text AS status, count(*) AS n FROM processing_jobs
         WHERE job_type = 'CHUNKING' GROUP BY status ORDER BY status
        """
    ).fetchall():
        print(f"  {r['status']:<12} {r['n']:>6,}")

    if failures:
        _rule("Failed chunking jobs")
        for r in failures:
            print(f"  {r['file_name']} (attempts={r['attempts']})")
            print(f"      {r['error_message']}")


def show_runs(conn) -> None:
    rows = conn.execute(
        """
        SELECT strategy, strategy_version, config_hash,
               min(config::text) AS config,
               count(*) AS chunks, count(DISTINCT document_id) AS documents,
               round(avg(token_count), 1) AS avg_tokens,
               max(token_count) AS max_tokens
          FROM document_chunks
         GROUP BY strategy, strategy_version, config_hash
         ORDER BY strategy, strategy_version, config_hash
        """
    ).fetchall()

    _rule("Chunk sets (one row per strategy/version/config)")
    if not rows:
        print("  (none)")
        return
    for r in rows:
        cfg = json.loads(r["config"])
        print(f"  {r['strategy']} v{r['strategy_version']}  cfg={r['config_hash']}")
        print(
            f"      docs={r['documents']:<5} chunks={r['chunks']:<7} "
            f"avg={r['avg_tokens']:<7} max={r['max_tokens']}"
        )
        print(
            f"      max_tokens={cfg.get('max_tokens')} min={cfg.get('min_tokens')} "
            f"overlap={cfg.get('overlap_tokens')} tokenizer={cfg.get('tokenizer')}"
        )


def by_document(conn, args, limit: int = 25) -> None:
    where, params = _run_filter(args)
    rows = conn.execute(
        f"""
        SELECT d.file_name, count(*) AS chunks,
               round(avg(c.token_count), 1) AS avg_tokens,
               max(c.token_count) AS max_tokens,
               count(*) FILTER (WHERE c.has_table) AS with_tables
          FROM document_chunks c JOIN documents d ON d.id = c.document_id
         WHERE {where}
         GROUP BY d.file_name ORDER BY d.file_name LIMIT {limit}
        """,
        params,
    ).fetchall()

    _rule("Chunks by document")
    print(f"  {'CHUNKS':>7}{'AVG':>8}{'MAX':>7}{'TABLES':>8}  FILE")
    for r in rows:
        print(
            f"  {r['chunks']:>7,}{r['avg_tokens']:>8}{r['max_tokens']:>7}"
            f"{r['with_tables']:>8}  {r['file_name'][:52]}"
        )


def by_path(conn, args, limit: int = 25) -> None:
    where, params = _run_filter(args)
    rows = conn.execute(
        f"""
        SELECT array_to_string(node_path, ' > ') AS path,
               count(*) AS chunks, round(avg(token_count), 1) AS avg_tokens,
               count(*) FILTER (WHERE has_table) AS with_tables
          FROM document_chunks
         WHERE {where} AND cardinality(node_path) > 0
         GROUP BY path ORDER BY chunks DESC LIMIT {limit}
        """,
        params,
    ).fetchall()

    _rule("Chunks by section path (most chunked first)")
    print(f"  {'CHUNKS':>7}{'AVG':>8}{'TABLES':>8}  PATH")
    for r in rows:
        print(
            f"  {r['chunks']:>7,}{r['avg_tokens']:>8}{r['with_tables']:>8}  "
            f"{r['path'][:64]}"
        )


def inspect_document(conn, args) -> int:
    where, params = _run_filter(args)
    rows = conn.execute(
        f"""
        SELECT c.chunk_index, c.token_count, c.char_count, c.has_table,
               c.node_path, c.node_ids, c.section_node_id, c.content, c.metadata,
               c.content_nodes, d.file_name
          FROM document_chunks c JOIN documents d ON d.id = c.document_id
         WHERE {where} AND d.file_name LIKE %s
         ORDER BY c.chunk_index
        """,
        params + [f"%{args.file}%"],
    ).fetchall()

    if not rows:
        print(f"No chunks found for {args.file!r}", file=sys.stderr)
        return 1

    if args.chunk is None:
        _rule(f"{rows[0]['file_name']} - {len(rows)} chunks")
        print(f"  {'IDX':>5}{'TOK':>6}{'CHR':>7}  T  PATH")
        for r in rows:
            flag = "T" if r["has_table"] else " "
            path = " > ".join(r["node_path"])[:58]
            print(
                f"  {r['chunk_index']:>5}{r['token_count']:>6}{r['char_count']:>7}"
                f"  {flag}  {path}"
            )
        print("\n  use --chunk N to print one in full")
        return 0

    match = [r for r in rows if r["chunk_index"] == args.chunk]
    if not match:
        print(f"No chunk {args.chunk} (0-{len(rows) - 1})", file=sys.stderr)
        return 1
    r = match[0]

    _rule(f"{r['file_name']} - chunk {r['chunk_index']}")
    print(f"  tokens        {r['token_count']}")
    print(f"  characters    {r['char_count']}")
    print(f"  has table     {r['has_table']}")

    _rule("Lineage")
    print(f"  path            {' > '.join(r['node_path']) or '(document root)'}")
    print(f"  section node    {r['section_node_id'] or '-'}")
    print(f"  canonical nodes {', '.join(r['node_ids'][:8])}")
    print(f"  node types      {r['metadata'].get('node_types')}")

    _rule("Metadata")
    for k, v in sorted(r["metadata"].items()):
        if k != "node_types":
            print(f"  {k:<20} {v}")

    tables = [n for n in r["content_nodes"] if n.get("type") == "table"]
    if tables:
        _rule(f"Structured tables retained ({len(tables)})")
        for t in tables[:2]:
            tbl = t["table"]
            print(f"  {tbl['n_rows']}x{tbl['n_cols']}  caption={tbl.get('caption')}")
            if tbl["header"]:
                print("    H | " + " | ".join(c[:18] for c in tbl["header"]))
            for row in tbl["rows"][:4]:
                print("      | " + " | ".join(c[:18] for c in row))

    _rule("Text")
    print(r["content"][:2500])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify KnowledgeOS chunking")
    parser.add_argument("--strategy", help="restrict to one strategy")
    parser.add_argument("--config-hash", help="restrict to one configuration")
    parser.add_argument("--runs", action="store_true", help="list chunk sets")
    parser.add_argument("--by-document", action="store_true")
    parser.add_argument("--by-path", action="store_true")
    parser.add_argument("--file", help="inspect one document's chunks")
    parser.add_argument("--chunk", type=int, help="print one chunk in full")
    args = parser.parse_args()

    try:
        with get_connection(autocommit=True) as conn:
            if args.file:
                return inspect_document(conn, args)
            print("KnowledgeOS - chunking verification")
            if args.runs:
                show_runs(conn)
                return 0
            summary(conn, args)
            if args.by_document:
                by_document(conn, args)
            if args.by_path:
                by_path(conn, args)
            print()
        return 0
    except Exception as exc:
        print(f"Verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
