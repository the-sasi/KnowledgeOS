"""Data access layer.

Every write here is idempotent: re-running ingestion over the same SEC files
must never create a second company, filing, document, or open job.

The `created` flag returned alongside each row uses PostgreSQL's `xmax`
system column. On a freshly inserted row xmax is 0; on a row that an
`ON CONFLICT DO UPDATE` touched instead, it holds the updating transaction id.
That is what lets the caller log "created" versus "found" accurately without a
separate SELECT.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg

from knowledgeos.db.models import DocumentStatus, JobStatus, JobType, normalize_cik


class CompanyRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        *,
        cik: str,
        name: str,
        ticker: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Find or create a company, keyed on the normalized CIK."""
        row = self._conn.execute(
            """
            INSERT INTO companies (cik, name, ticker, metadata)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (cik) DO UPDATE
                SET name     = EXCLUDED.name,
                    ticker   = COALESCE(EXCLUDED.ticker, companies.ticker),
                    metadata = companies.metadata || EXCLUDED.metadata
            RETURNING *, (xmax = 0) AS created
            """,
            (normalize_cik(cik), name, ticker, json.dumps(metadata or {})),
        ).fetchone()
        return row, row.pop("created")


class FilingRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(
        self,
        *,
        company_id: uuid.UUID,
        accession_number: str,
        form_type: str,
        filing_date: str | None = None,
        report_date: str | None = None,
        primary_document: str | None = None,
        primary_doc_description: str | None = None,
        file_number: str | None = None,
        items: str | None = None,
        size_bytes: int | None = None,
        source: str = "SEC EDGAR",
        source_url: str | None = None,
        index_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Find or create a filing, keyed on (company, accession number)."""
        row = self._conn.execute(
            """
            INSERT INTO filings (
                company_id, accession_number, form_type, filing_date,
                report_date, primary_document, primary_doc_description,
                file_number, items, size_bytes, source, source_url,
                index_url, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, accession_number) DO UPDATE
                SET form_type               = EXCLUDED.form_type,
                    filing_date             = COALESCE(EXCLUDED.filing_date, filings.filing_date),
                    report_date             = COALESCE(EXCLUDED.report_date, filings.report_date),
                    primary_document        = COALESCE(EXCLUDED.primary_document, filings.primary_document),
                    primary_doc_description = COALESCE(EXCLUDED.primary_doc_description, filings.primary_doc_description),
                    file_number             = COALESCE(EXCLUDED.file_number, filings.file_number),
                    items                   = COALESCE(EXCLUDED.items, filings.items),
                    size_bytes              = COALESCE(EXCLUDED.size_bytes, filings.size_bytes),
                    source_url              = COALESCE(EXCLUDED.source_url, filings.source_url),
                    index_url               = COALESCE(EXCLUDED.index_url, filings.index_url),
                    metadata                = filings.metadata || EXCLUDED.metadata
            RETURNING *, (xmax = 0) AS created
            """,
            (
                company_id,
                accession_number,
                form_type,
                filing_date or None,
                report_date or None,
                primary_document,
                primary_doc_description,
                file_number,
                items,
                size_bytes,
                source,
                source_url,
                index_url,
                json.dumps(metadata or {}),
            ),
        ).fetchone()
        return row, row.pop("created")


class DocumentRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert_downloaded(
        self,
        *,
        filing_id: uuid.UUID,
        file_name: str,
        storage_path: str,
        document_type: str = "PRIMARY",
        content_type: str | None = None,
        byte_size: int | None = None,
        checksum_sha256: str | None = None,
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Record a successfully downloaded document.

        On conflict the file facts are refreshed but `status` is deliberately
        left alone, so re-running ingestion never drags a PROCESSED document
        back to DOWNLOADED. The one exception is a document previously marked
        FAILED: a good download clears the error and returns it to DOWNLOADED.
        """
        row = self._conn.execute(
            """
            INSERT INTO documents (
                filing_id, document_type, file_name, storage_path,
                content_type, byte_size, checksum_sha256, source_url,
                status, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'DOWNLOADED', %s)
            ON CONFLICT (filing_id, file_name) DO UPDATE
                SET storage_path    = EXCLUDED.storage_path,
                    content_type    = COALESCE(EXCLUDED.content_type, documents.content_type),
                    byte_size       = COALESCE(EXCLUDED.byte_size, documents.byte_size),
                    checksum_sha256 = COALESCE(EXCLUDED.checksum_sha256, documents.checksum_sha256),
                    source_url      = COALESCE(EXCLUDED.source_url, documents.source_url),
                    metadata        = documents.metadata || EXCLUDED.metadata,
                    status          = CASE
                                          WHEN documents.status = 'FAILED' THEN 'DOWNLOADED'::document_status
                                          ELSE documents.status
                                      END,
                    error_message   = CASE
                                          WHEN documents.status = 'FAILED' THEN NULL
                                          ELSE documents.error_message
                                      END
            RETURNING *, (xmax = 0) AS created
            """,
            (
                filing_id,
                document_type,
                file_name,
                storage_path,
                content_type,
                byte_size,
                checksum_sha256,
                source_url,
                json.dumps(metadata or {}),
            ),
        ).fetchone()
        return row, row.pop("created")

    def mark_failed(
        self,
        *,
        filing_id: uuid.UUID,
        file_name: str,
        storage_path: str,
        error_message: str,
        document_type: str = "PRIMARY",
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Record a download failure against the document row.

        A failed download must never leave a document that looks complete, so
        byte_size and checksum stay NULL and the status is FAILED with the
        error attached.
        """
        row = self._conn.execute(
            """
            INSERT INTO documents (
                filing_id, document_type, file_name, storage_path,
                source_url, status, error_message
            )
            VALUES (%s, %s, %s, %s, %s, 'FAILED', %s)
            ON CONFLICT (filing_id, file_name) DO UPDATE
                SET status        = 'FAILED'::document_status,
                    error_message = EXCLUDED.error_message,
                    source_url    = COALESCE(EXCLUDED.source_url, documents.source_url)
            RETURNING *
            """,
            (
                filing_id,
                document_type,
                file_name,
                storage_path,
                source_url,
                error_message[:2000],
            ),
        ).fetchone()
        return row

    def set_status(
        self,
        *,
        document_id: uuid.UUID,
        status: DocumentStatus,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Move a document to a new status.

        The schema's CHECK constraint requires error_message to be present for
        FAILED and absent otherwise, so it is normalized here rather than left
        to the caller.
        """
        if status is DocumentStatus.FAILED and not error_message:
            raise ValueError("FAILED documents require an error_message")

        row = self._conn.execute(
            """
            UPDATE documents
               SET status = %s::document_status,
                   error_message = %s
             WHERE id = %s
            RETURNING *
            """,
            (
                status.value,
                error_message[:2000] if status is DocumentStatus.FAILED else None,
                document_id,
            ),
        ).fetchone()
        return row


    def get(self, document_id: uuid.UUID) -> dict[str, Any] | None:
        return self._conn.execute(
            "SELECT * FROM documents WHERE id = %s", (document_id,)
        ).fetchone()

    def list_for_processing(
        self,
        *,
        processor_name: str | None = None,
        processor_version: str | None = None,
        reprocess: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Documents the processing stage should look at.

        Without `reprocess`, this returns documents that have never been
        processed plus those whose recorded processor name/version differs from
        the current one. That version comparison is what makes a processor
        upgrade re-run only the documents that are actually stale.
        """
        clauses = ["status <> 'PROCESSING'"]
        params: list[Any] = []

        if not reprocess:
            clauses.append(
                """(
                    status IN ('DOWNLOADED', 'FAILED')
                    OR processed_path IS NULL
                    OR processor_name IS DISTINCT FROM %s
                    OR processor_version IS DISTINCT FROM %s
                )"""
            )
            params.extend([processor_name, processor_version])

        sql = f"""
            SELECT * FROM documents
             WHERE {' AND '.join(clauses)}
             ORDER BY created_at
        """
        if limit:
            sql += " LIMIT %s"
            params.append(limit)

        return list(self._conn.execute(sql, tuple(params)).fetchall())

    def mark_processing(self, document_id: uuid.UUID) -> dict[str, Any]:
        """DOWNLOADED/FAILED -> PROCESSING, clearing any previous error."""
        return self._conn.execute(
            """
            UPDATE documents
               SET status = 'PROCESSING'::document_status,
                   error_message = NULL
             WHERE id = %s
            RETURNING *
            """,
            (document_id,),
        ).fetchone()

    def mark_processed(
        self,
        *,
        document_id: uuid.UUID,
        processed_path: str,
        processor_name: str,
        processor_version: str,
        doc_format: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._conn.execute(
            """
            UPDATE documents
               SET status            = 'PROCESSED'::document_status,
                   error_message     = NULL,
                   processed_path    = %s,
                   processor_name    = %s,
                   processor_version = %s,
                   doc_format        = COALESCE(%s, doc_format),
                   processed_at      = now(),
                   metadata          = documents.metadata || %s
             WHERE id = %s
            RETURNING *
            """,
            (
                processed_path,
                processor_name,
                processor_version,
                doc_format,
                json.dumps(metadata or {}),
                document_id,
            ),
        ).fetchone()

    def mark_processing_failed(
        self, *, document_id: uuid.UUID, error_message: str
    ) -> dict[str, Any]:
        """PROCESSING -> FAILED.

        Any processed_path from an earlier successful run is cleared, so a
        FAILED document never points at output that no longer reflects it.
        """
        return self._conn.execute(
            """
            UPDATE documents
               SET status         = 'FAILED'::document_status,
                   error_message  = %s,
                   processed_path = NULL,
                   processed_at   = NULL
             WHERE id = %s
            RETURNING *
            """,
            (error_message[:2000], document_id),
        ).fetchone()


class ProcessingJobRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def enqueue(
        self,
        *,
        document_id: uuid.UUID,
        job_type: JobType = JobType.DOCUMENT_PROCESSING,
        filing_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Queue a job unless an open one already exists for this document.

        The partial unique index `processing_jobs_open_document_job_key` covers
        only QUEUED and PROCESSING rows, so a COMPLETED or FAILED job from an
        earlier run does not block re-queueing.

        Returns (row, created). When an open job already exists the insert is
        skipped and the existing row is returned with created=False.
        """
        row = self._conn.execute(
            """
            INSERT INTO processing_jobs (job_type, status, document_id, filing_id, payload)
            VALUES (%s::job_type, 'QUEUED', %s, %s, %s)
            ON CONFLICT (document_id, job_type)
                WHERE status IN ('QUEUED', 'PROCESSING')
                DO NOTHING
            RETURNING *
            """,
            (job_type.value, document_id, filing_id, json.dumps(payload or {})),
        ).fetchone()

        if row is not None:
            return row, True

        existing = self._conn.execute(
            """
            SELECT * FROM processing_jobs
             WHERE document_id = %s
               AND job_type = %s::job_type
               AND status IN ('QUEUED', 'PROCESSING')
             LIMIT 1
            """,
            (document_id, job_type.value),
        ).fetchone()
        return existing, False

    def open_job_for_document(
        self,
        *,
        document_id: uuid.UUID,
        job_type: JobType = JobType.DOCUMENT_PROCESSING,
    ) -> dict[str, Any] | None:
        """The QUEUED/PROCESSING job for a document, if any.

        Locked with FOR UPDATE SKIP LOCKED so two workers cannot claim the same
        job. There is one worker today, but the queue is the natural place for
        a second one later and the cost of getting this right now is a clause.
        """
        return self._conn.execute(
            """
            SELECT * FROM processing_jobs
             WHERE document_id = %s
               AND job_type = %s::job_type
               AND status IN ('QUEUED', 'PROCESSING')
             ORDER BY scheduled_at
             LIMIT 1
             FOR UPDATE SKIP LOCKED
            """,
            (document_id, job_type.value),
        ).fetchone()

    def set_status(
        self,
        *,
        job_id: uuid.UUID,
        status: JobStatus,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        row = self._conn.execute(
            """
            UPDATE processing_jobs
               SET status        = %s::job_status,
                   error_message = %s,
                   started_at    = CASE WHEN %s = 'PROCESSING' THEN now() ELSE started_at END,
                   finished_at   = CASE WHEN %s IN ('COMPLETED', 'FAILED') THEN now() ELSE finished_at END,
                   attempts      = CASE WHEN %s = 'PROCESSING' THEN attempts + 1 ELSE attempts END
             WHERE id = %s
            RETURNING *
            """,
            (
                status.value,
                error_message[:2000] if error_message else None,
                status.value,
                status.value,
                status.value,
                job_id,
            ),
        ).fetchone()
        return row


class DocumentSectionRepository:
    """Structural outline produced by the processing stage.

    Holds one row per structural canonical node. Rows are replaced wholesale
    per document rather than merged: a reprocess produces a new structure, and
    diffing two structures to update in place would be far more complex than
    rewriting them.

    A document with no structural nodes - flat content with no headings -
    legitimately has no rows here. Its structure lives in the canonical JSON.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def delete_for_document(self, document_id: uuid.UUID) -> int:
        result = self._conn.execute(
            "DELETE FROM document_sections WHERE document_id = %s", (document_id,)
        )
        return result.rowcount or 0

    def insert(
        self,
        *,
        document_id: uuid.UUID,
        parent_section_id: uuid.UUID | None,
        section_key: str,
        title: str | None,
        ordinal: int,
        level: int,
        processor_name: str,
        processor_version: str,
        node_type: str = "section",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._conn.execute(
            """
            INSERT INTO document_sections (
                document_id, parent_section_id, section_key, title,
                ordinal, level, node_type, processor_name, processor_version,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                document_id,
                parent_section_id,
                section_key,
                title,
                ordinal,
                level,
                node_type,
                processor_name,
                processor_version,
                json.dumps(metadata or {}),
            ),
        ).fetchone()


class ChunkRepository:
    """Chunks produced by the chunking stage.

    A chunk set is keyed on (document, strategy, strategy_version,
    config_hash). Several sets coexist per document so strategies and
    configurations can be compared rather than overwriting one another.

    `replace_set` deletes and reinserts inside the caller's transaction, so a
    failure never leaves a half-written chunk set behind.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def count_for_run(
        self,
        *,
        document_id: uuid.UUID,
        strategy: str,
        strategy_version: str,
        config_hash: str,
    ) -> int:
        row = self._conn.execute(
            """
            SELECT count(*) AS n FROM document_chunks
             WHERE document_id = %s AND strategy = %s
               AND strategy_version = %s AND config_hash = %s
            """,
            (document_id, strategy, strategy_version, config_hash),
        ).fetchone()
        return row["n"]

    def delete_set(
        self,
        *,
        document_id: uuid.UUID,
        strategy: str,
        strategy_version: str,
        config_hash: str,
    ) -> int:
        result = self._conn.execute(
            """
            DELETE FROM document_chunks
             WHERE document_id = %s AND strategy = %s
               AND strategy_version = %s AND config_hash = %s
            """,
            (document_id, strategy, strategy_version, config_hash),
        )
        return result.rowcount or 0

    def replace_set(
        self,
        *,
        document_id: uuid.UUID,
        strategy: str,
        strategy_version: str,
        config_hash: str,
        config: dict[str, Any],
        tokenizer: str,
        chunks: list[dict[str, Any]],
        section_id_by_key: dict[str, uuid.UUID] | None = None,
    ) -> int:
        """Replace one chunk set wholesale. Returns the number written."""
        self.delete_set(
            document_id=document_id,
            strategy=strategy,
            strategy_version=strategy_version,
            config_hash=config_hash,
        )

        section_ids = section_id_by_key or {}
        config_json = json.dumps(config)
        written = 0

        for chunk in chunks:
            self._conn.execute(
                """
                INSERT INTO document_chunks (
                    document_id, section_id, chunk_index, content, content_hash,
                    token_count, char_count, strategy, strategy_version,
                    config_hash, config, tokenizer, node_ids, node_path,
                    section_node_id, content_nodes, has_table, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s)
                """,
                (
                    document_id,
                    section_ids.get(chunk.get("section_node_id") or ""),
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk["content_hash"],
                    chunk["token_count"],
                    chunk["char_count"],
                    strategy,
                    strategy_version,
                    config_hash,
                    config_json,
                    tokenizer,
                    chunk["node_ids"],
                    chunk["path"],
                    chunk.get("section_node_id"),
                    json.dumps(chunk["nodes"]),
                    chunk["has_table"],
                    json.dumps(chunk.get("metadata", {})),
                ),
            )
            written += 1

        return written

    def section_key_map(self, document_id: uuid.UUID) -> dict[str, uuid.UUID]:
        """Canonical node id -> document_sections row id, for FK lineage."""
        rows = self._conn.execute(
            "SELECT section_key, id FROM document_sections WHERE document_id = %s",
            (document_id,),
        ).fetchall()
        return {r["section_key"]: r["id"] for r in rows if r["section_key"]}

    def runs(self, document_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        """Distinct chunk sets, for comparing experiments."""
        sql = """
            SELECT strategy, strategy_version, config_hash,
                   min(config::text) AS config,
                   count(*) AS chunks,
                   count(DISTINCT document_id) AS documents
              FROM document_chunks
        """
        params: tuple[Any, ...] = ()
        if document_id is not None:
            sql += " WHERE document_id = %s"
            params = (document_id,)
        sql += """
             GROUP BY strategy, strategy_version, config_hash
             ORDER BY strategy, strategy_version, config_hash
        """
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]


class StatsRepository:
    """Read-only counts used by the verification command."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def totals(self) -> dict[str, int]:
        row = self._conn.execute(
            """
            SELECT (SELECT count(*) FROM companies)            AS companies,
                   (SELECT count(*) FROM filings)              AS filings,
                   (SELECT count(*) FROM documents)            AS documents,
                   (SELECT count(*) FROM document_sections)    AS document_sections,
                   (SELECT count(*) FROM document_chunks)      AS document_chunks,
                   (SELECT count(*) FROM vector_index_records) AS vector_index_records,
                   (SELECT count(*) FROM processing_jobs)      AS processing_jobs
            """
        ).fetchone()
        return dict(row)

    def document_status_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status::text AS status, count(*) AS n FROM documents GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def job_status_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT job_type::text AS job_type, status::text AS status, count(*) AS n
              FROM processing_jobs
             GROUP BY job_type, status
            """
        ).fetchall()
        return {f"{r['job_type']} / {r['status']}": r["n"] for r in rows}

    def companies_overview(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT c.cik,
                   c.name,
                   c.ticker,
                   count(DISTINCT f.id) AS filings,
                   count(d.id)          AS documents
              FROM companies c
              LEFT JOIN filings f   ON f.company_id = c.id
              LEFT JOIN documents d ON d.filing_id = f.id
             GROUP BY c.id, c.cik, c.name, c.ticker
             ORDER BY c.name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_failures(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT file_name, storage_path, error_message, updated_at
              FROM documents
             WHERE status = 'FAILED'
             ORDER BY updated_at DESC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
