"""Processing orchestration.

For each document that needs processing:

    detect format -> resolve parser -> parse -> write canonical JSON
                  -> replace section rows -> update document + job status

State transitions, both driven from here:

    document:  DOWNLOADED/FAILED -> PROCESSING -> PROCESSED
                                                \\-> FAILED
    job:       QUEUED            -> PROCESSING -> COMPLETED
                                                \\-> FAILED

Knows nothing about chunking, embeddings, Qdrant, or LLMs. Its only output is
a canonical document plus database state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from knowledgeos.config import REPO_ROOT
from knowledgeos.db.connection import get_connection
from knowledgeos.db.models import JobStatus, JobType
from knowledgeos.db.repositories import (
    DocumentRepository,
    DocumentSectionRepository,
    ProcessingJobRepository,
)
from knowledgeos.logging_setup import get_logger
from services.processing import parsers  # noqa: F401  (registers parsers)
from services.processing.base import ProcessingError, registry
from services.processing.canonical import CanonicalDocument, Node, NodeType, SourceInfo
from services.processing.detection import DocumentFormat, detect_format, media_type_for
from services.processing.storage import (
    processed_path_for,
    relative_to_repo,
    write_canonical,
)

log = get_logger("processing.pipeline")


@dataclass
class ProcessingStats:
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    sections_written: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"processed={self.processed} skipped={self.skipped} "
            f"failed={self.failed} sections={self.sections_written}"
        )


def _resolve_raw_path(storage_path: str) -> Path:
    path = Path(storage_path)
    return path if path.is_absolute() else (REPO_ROOT / path)


def process_document(
    document_row: dict,
    *,
    stats: ProcessingStats,
    force: bool = False,
) -> bool:
    """Process one document. Returns True on success.

    Each document is handled in its own transaction, so one failure cannot
    roll back the documents already processed in this run.
    """
    document_id: uuid.UUID = document_row["id"]
    file_name = document_row["file_name"]
    raw_path = _resolve_raw_path(document_row["storage_path"])

    with get_connection() as conn:
        documents = DocumentRepository(conn)
        sections_repo = DocumentSectionRepository(conn)
        jobs = ProcessingJobRepository(conn)

        job = jobs.open_job_for_document(
            document_id=document_id, job_type=JobType.DOCUMENT_PROCESSING
        )

        # --- guards -------------------------------------------------------
        if not raw_path.is_file():
            error = f"Raw file missing on disk: {document_row['storage_path']}"
            log.error("%s | %s", file_name, error)
            documents.mark_processing_failed(document_id=document_id, error_message=error)
            if job:
                jobs.set_status(
                    job_id=job["id"], status=JobStatus.FAILED, error_message=error
                )
            stats.failed += 1
            stats.errors.append(f"{file_name}: {error}")
            return False

        # --- claim --------------------------------------------------------
        documents.mark_processing(document_id)
        if job:
            jobs.set_status(job_id=job["id"], status=JobStatus.PROCESSING)
            log.info("Job PROCESSING: %s", file_name)
        else:
            log.warning(
                "No open DOCUMENT_PROCESSING job for %s; processing anyway", file_name
            )
        log.info("Document PROCESSING: %s", file_name)

        try:
            raw_bytes = raw_path.read_bytes()

            fmt = detect_format(raw_path, head=raw_bytes[:8192])
            log.info("Detected format: %s (%s)", fmt.value, file_name)

            if fmt is DocumentFormat.UNKNOWN:
                raise ProcessingError(f"Could not determine the format of {file_name}")

            parser = registry.get(fmt)
            log.info("Parser: %s v%s", parser.name, parser.version)

            source = SourceInfo(
                path=document_row["storage_path"],
                file_name=file_name,
                doc_format=fmt.value,
                media_type=media_type_for(fmt),
                byte_size=document_row.get("byte_size") or raw_path.stat().st_size,
                checksum_sha256=document_row.get("checksum_sha256"),
                source_url=document_row.get("source_url"),
            )

            canonical: CanonicalDocument = parser.parse(source, raw_bytes)

            # Carry the ingestion-time facts (CIK, form type, dates) onto the
            # canonical document so it stands alone without a database lookup.
            canonical.metadata.update(_source_metadata(conn, document_row))

            destination = processed_path_for(raw_path)
            write_canonical(canonical, destination)
            processed_rel = relative_to_repo(destination)

            written = _persist_structure(
                sections_repo,
                document_id=document_id,
                canonical=canonical,
                processor_name=parser.name,
                processor_version=parser.version,
            )
            stats.sections_written += written

            documents.mark_processed(
                document_id=document_id,
                processed_path=processed_rel,
                processor_name=parser.name,
                processor_version=parser.version,
                doc_format=fmt.value,
                metadata={"processing_stats": canonical.stats()},
            )
            if job:
                jobs.set_status(job_id=job["id"], status=JobStatus.COMPLETED)

            doc_stats = canonical.stats()
            log.info(
                "Document PROCESSED: %s -> %s | nodes=%d sections=%d paragraphs=%d "
                "tables=%d lists=%d code=%d quotes=%d chars=%d depth=%d",
                file_name,
                processed_rel,
                doc_stats["nodes"],
                doc_stats["section"],
                doc_stats["paragraph"],
                doc_stats["table"],
                doc_stats["list"],
                doc_stats["code"],
                doc_stats["quote"],
                doc_stats["text_length"],
                doc_stats["max_depth"],
            )
            if job:
                log.info("Job COMPLETED: %s", file_name)
            stats.processed += 1
            return True

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.error("Document FAILED: %s | %s", file_name, error)

            # Undo any partial section rows from this attempt.
            sections_repo.delete_for_document(document_id)
            documents.mark_processing_failed(document_id=document_id, error_message=error)
            if job:
                jobs.set_status(
                    job_id=job["id"], status=JobStatus.FAILED, error_message=error
                )
                log.info("Job FAILED: %s", file_name)

            stats.failed += 1
            stats.errors.append(f"{file_name}: {error}")
            return False


def _source_metadata(conn, document_row: dict) -> dict:
    """Ingestion-time facts worth carrying into the canonical document."""
    row = conn.execute(
        """
        SELECT c.cik, c.name AS company_name, c.ticker,
               f.accession_number, f.form_type, f.filing_date, f.report_date,
               f.source_url, f.source
          FROM filings f
          JOIN companies c ON c.id = f.company_id
         WHERE f.id = %s
        """,
        (document_row["filing_id"],),
    ).fetchone()

    if not row:
        return {}

    return {
        "cik": row["cik"],
        "company_name": row["company_name"],
        "ticker": row["ticker"],
        "accession_number": row["accession_number"],
        "form_type": row["form_type"],
        "filing_date": row["filing_date"].isoformat() if row["filing_date"] else None,
        "report_date": row["report_date"].isoformat() if row["report_date"] else None,
        "source": row["source"],
        "source_url": row["source_url"],
    }


def _persist_structure(
    repo: DocumentSectionRepository,
    *,
    document_id: uuid.UUID,
    canonical: CanonicalDocument,
    processor_name: str,
    processor_version: str,
) -> int:
    """Replace this document's structural outline rows.

    Only *structural* nodes are persisted - currently SECTION. Content nodes
    stay in the canonical JSON, so PostgreSQL keeps a queryable outline without
    becoming a document store.

    A document with no structural nodes (flat content, no headings) writes no
    rows, which is correct rather than a failure: sections are one possible
    shape, not a requirement.
    """
    repo.delete_for_document(document_id)

    written = 0
    ordinal = 0

    def visit(node: Node, parent_db_id: uuid.UUID | None) -> None:
        """Persist structural nodes, re-parenting children of skipped nodes.

        `parent_db_id` is the nearest persisted ancestor, so content nodes in
        between never orphan a nested section.
        """
        nonlocal written, ordinal
        next_parent = parent_db_id

        if node.type is NodeType.SECTION:
            content_children = [c for c in node.children if c.type is not NodeType.SECTION]
            row = repo.insert(
                document_id=document_id,
                parent_section_id=parent_db_id,
                section_key=node.id,
                title=node.text or None,
                ordinal=ordinal,
                level=node.level or 0,
                node_type=node.type.value,
                processor_name=processor_name,
                processor_version=processor_version,
                metadata={
                    "path": node.attributes.get("path", []),
                    "content_node_count": len(content_children),
                    "table_count": sum(
                        1 for c in content_children if c.type is NodeType.TABLE
                    ),
                    "text_length": sum(
                        d.text_length() for c in content_children for d in c.walk()
                    ),
                },
            )
            next_parent = row["id"]
            ordinal += 1
            written += 1

        for child in node.children:
            visit(child, next_parent)

    for root in canonical.content:
        visit(root, None)

    return written


def process_pending(
    *,
    limit: int | None = None,
    force: bool = False,
    document_id: uuid.UUID | None = None,
) -> ProcessingStats:
    """Process every document that needs it.

    `force` reprocesses regardless of recorded processor version.
    """
    stats = ProcessingStats()

    html_parser = registry.get_or_none(DocumentFormat.HTML)
    current_name = html_parser.name if html_parser else None
    current_version = html_parser.version if html_parser else None

    log.info(
        "Processing stage starting | parsers=%s",
        ", ".join(f"{p.name} v{p.version}" for p in registry.parsers()) or "none",
    )

    with get_connection(autocommit=True) as conn:
        documents = DocumentRepository(conn)
        if document_id is not None:
            row = documents.get(document_id)
            candidates = [row] if row else []
        else:
            candidates = documents.list_for_processing(
                processor_name=current_name,
                processor_version=current_version,
                reprocess=force,
                limit=limit,
            )

    if not candidates:
        log.info("Nothing to process; all documents are current")
        return stats

    log.info("%d document(s) to process", len(candidates))

    for row in candidates:
        if (
            not force
            and row["status"] == "PROCESSED"
            and row["processor_name"] == current_name
            and row["processor_version"] == current_version
        ):
            log.info(
                "Skipping (already processed by %s v%s): %s",
                current_name,
                current_version,
                row["file_name"],
            )
            stats.skipped += 1
            continue

        process_document(row, stats=stats, force=force)

    log.info("Processing stage complete | %s", stats.summary())
    return stats
