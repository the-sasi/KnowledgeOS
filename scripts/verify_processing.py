#!/usr/bin/env python3
"""Show what the processing stage produced, and inspect one canonical document.

    python scripts/verify_processing.py                  # summary
    python scripts/verify_processing.py --document <uuid>
    python scripts/verify_processing.py --file <path to raw .htm>
    python scripts/verify_processing.py --file <...> --outline 40 --show-table 0

Read-only. Also re-hashes the raw files to confirm processing did not modify
anything under data/raw.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledgeos.config import REPO_ROOT  # noqa: E402
from knowledgeos.db.connection import get_connection  # noqa: E402
from services.processing.canonical import NodeType  # noqa: E402
from services.processing.storage import read_canonical  # noqa: E402


def _rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def summary() -> None:
    with get_connection(autocommit=True) as conn:
        docs = conn.execute(
            """
            SELECT status::text AS status, doc_format, processor_name,
                   processor_version, count(*) AS n
              FROM documents
             GROUP BY status, doc_format, processor_name, processor_version
             ORDER BY status
            """
        ).fetchall()
        jobs = conn.execute(
            """
            SELECT job_type::text AS job_type, status::text AS status, count(*) AS n
              FROM processing_jobs GROUP BY 1, 2 ORDER BY 1, 2
            """
        ).fetchall()
        sections = conn.execute(
            """
            SELECT count(*) AS rows,
                   count(DISTINCT document_id) AS documents,
                   count(*) FILTER (WHERE parent_section_id IS NOT NULL) AS nested,
                   max(level) AS max_level
              FROM document_sections
            """
        ).fetchone()
        failures = conn.execute(
            """
            SELECT file_name, error_message FROM documents
             WHERE status = 'FAILED' ORDER BY updated_at DESC LIMIT 10
            """
        ).fetchall()

    _rule("Documents")
    print(f"  {'STATUS':<12} {'FORMAT':<8} {'PROCESSOR':<20} {'COUNT':>6}")
    for row in docs:
        processor = (
            f"{row['processor_name']} v{row['processor_version']}"
            if row["processor_name"]
            else "-"
        )
        print(
            f"  {row['status']:<12} {(row['doc_format'] or '-'):<8} "
            f"{processor:<20} {row['n']:>6}"
        )

    _rule("Processing jobs")
    for row in jobs:
        print(f"  {row['job_type']} / {row['status']:<10} {row['n']:>6}")

    _rule("Structural outline")
    print(f"  outline rows        {sections['rows']:>8,}")
    print(f"  documents covered   {sections['documents']:>8,}")
    print(f"  nested sections     {sections['nested']:>8,}")
    print(f"  max nesting level   {sections['max_level'] or 0:>8}")

    if failures:
        _rule("Failures")
        for row in failures:
            print(f"  {row['file_name']}\n      {row['error_message']}")


def verify_raw_immutable() -> bool:
    """Re-hash every raw file and compare against the ingestion-time checksum."""
    with get_connection(autocommit=True) as conn:
        rows = conn.execute(
            """
            SELECT file_name, storage_path, checksum_sha256
              FROM documents WHERE checksum_sha256 IS NOT NULL
            """
        ).fetchall()

    checked = 0
    mismatched: list[str] = []
    missing: list[str] = []

    for row in rows:
        path = REPO_ROOT / row["storage_path"]
        if not path.is_file():
            missing.append(row["file_name"])
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checked += 1
        if digest != row["checksum_sha256"]:
            mismatched.append(row["file_name"])

    _rule("Raw file immutability")
    print(f"  files re-hashed     {checked:>8,}")
    print(f"  checksum mismatches {len(mismatched):>8,}")
    print(f"  missing files       {len(missing):>8,}")
    for name in mismatched[:10]:
        print(f"    MODIFIED: {name}")
    for name in missing[:10]:
        print(f"    MISSING:  {name}")

    ok = not mismatched and not missing
    print(f"  -> data/raw is {'UNCHANGED' if ok else 'NOT INTACT'}")
    return ok


def inspect(
    *, document_id: str | None, file_arg: str | None, outline: int, show_table: int | None
) -> int:
    if document_id:
        where, param = "id = %s", document_id
    else:
        where, param = "storage_path LIKE %s", f"%{Path(file_arg).name}"

    with get_connection(autocommit=True) as conn:
        row = conn.execute(
            f"SELECT * FROM documents WHERE {where} LIMIT 1", (param,)
        ).fetchone()

    if not row:
        print("No matching document.", file=sys.stderr)
        return 1
    if not row["processed_path"]:
        print(f"{row['file_name']} is {row['status']}, not processed.", file=sys.stderr)
        return 1

    doc = read_canonical(row["processed_path"])

    print("Document")
    print(f"  file            {row['file_name']}")
    print(f"  status          {row['status']}")
    print(f"  raw             {row['storage_path']}")
    print(f"  canonical       {row['processed_path']}")
    print(f"  processor       {row['processor_name']} v{row['processor_version']}")
    print(f"  schema_version  {doc.schema_version}")

    _rule("Source metadata (carried onto the canonical document)")
    for key in (
        "company_name", "cik", "ticker", "form_type",
        "filing_date", "report_date", "html_title",
    ):
        if doc.metadata.get(key):
            print(f"  {key:<16} {doc.metadata[key]}")

    _rule("Stats (node counts)")
    for key, value in doc.stats().items():
        if value:
            print(f"  {key:<16} {value:>10,}")

    _rule("Document shape")
    print(f"  has sections    {doc.has_sections()}")
    print(f"  root nodes      {len(doc.content)}")
    print(f"  root node types {sorted({n.type.value for n in doc.content})}")

    _rule(f"Node outline (first {outline})")
    shown = 0
    for node, parent in doc.iter_with_parents():
        if shown >= outline:
            break
        # Indent by section level where there is one; content nodes sit one
        # step in from their enclosing section.
        depth = (node.level or 1) - 1 if node.type is NodeType.SECTION else 1
        indent = "  " * max(0, depth)
        label = node.text[:64] if node.text else ""
        extra = ""
        if node.type is NodeType.TABLE and node.table is not None:
            extra = f" ({node.table.n_rows}x{node.table.n_cols})"
        elif node.type is NodeType.LIST:
            extra = f" ({len(node.list_items())} items)"
        elif node.type is NodeType.SECTION:
            extra = f" (L{node.level}, {len(node.children)} children)"
        print(f"  {indent}<{node.type.value}>{extra} {label}")
        shown += 1

    tables = doc.tables()
    _rule(f"Tables ({len(tables)} total)")
    if show_table is not None and 0 <= show_table < len(tables):
        table = tables[show_table]
        print(f"  table #{show_table}: {table.n_rows} rows x {table.n_cols} cols")
        if table.caption:
            print(f"  caption: {table.caption}")
        if table.header:
            print("  H | " + " | ".join(c[:22] for c in table.header))
        for r in table.rows[:12]:
            print("    | " + " | ".join(c[:22] for c in r))
    else:
        for i, table in enumerate(tables[:5]):
            first = " | ".join(c[:18] for c in (table.header or table.rows[0])[:5])
            print(f"  #{i}: {table.n_rows}x{table.n_cols}  {first}")
        print("  (use --show-table N to print one)")

    _rule("Sample text blocks")
    paragraphs = [
        n for n in doc.find(NodeType.PARAGRAPH) if len(n.text) > 200
    ]
    for block in paragraphs[:2]:
        print(f"  {block.text[:300]}...")
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify KnowledgeOS document processing")
    parser.add_argument("--document", help="inspect one document by UUID")
    parser.add_argument("--file", help="inspect one document by raw file name/path")
    parser.add_argument("--outline", type=int, default=25, help="section outline depth")
    parser.add_argument("--show-table", type=int, help="print table N in full")
    args = parser.parse_args()

    try:
        if args.document or args.file:
            return inspect(
                document_id=args.document,
                file_arg=args.file,
                outline=args.outline,
                show_table=args.show_table,
            )
        print("KnowledgeOS - processing verification")
        summary()
        ok = verify_raw_immutable()
        print()
        return 0 if ok else 1
    except Exception as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
