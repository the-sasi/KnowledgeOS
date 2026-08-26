"""Chunking orchestration.

For each processed document:

    read canonical JSON -> ChunkingEngine -> chunks -> PostgreSQL

Job state, driven from here:

    QUEUED -> PROCESSING -> COMPLETED
                        \\-> FAILED

Writing one document's chunk set - deleting any previous set for this run key
and inserting every chunk - happens in a single transaction, so a failure rolls
back to the previous state rather than leaving a partial set that later looks
complete. The job lifecycle deliberately runs *outside* that transaction: it
records what was attempted, and must survive the rollback it is describing.

Knows nothing about embeddings, vectors, Qdrant, retrieval, or models.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from knowledgeos.db.connection import get_connection
from knowledgeos.db.models import JobStatus, JobType
from knowledgeos.db.repositories import ChunkRepository, ProcessingJobRepository
from knowledgeos.logging_setup import get_logger
from services.chunking.engine import DEFAULT_STRATEGY, ChunkingEngine
from services.chunking.models import ChunkingConfig
from services.processing.storage import read_canonical

log = get_logger("chunking.pipeline")


@dataclass
class ChunkingStats:
    documents_chunked: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    chunks_written: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"chunked={self.documents_chunked} skipped={self.documents_skipped} "
            f"failed={self.documents_failed} chunks={self.chunks_written}"
        )


def _candidate_documents(limit: int | None, document_id: uuid.UUID | None) -> list[dict]:
    """Processed documents are the input to chunking."""
    with get_connection(autocommit=True) as conn:
        if document_id is not None:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = %s", (document_id,)
            ).fetchone()
            return [row] if row else []

        sql = """
            SELECT * FROM documents
             WHERE status = 'PROCESSED' AND processed_path IS NOT NULL
             ORDER BY created_at
        """
        params: tuple = ()
        if limit:
            sql += " LIMIT %s"
            params = (limit,)
        return list(conn.execute(sql, params).fetchall())


def _already_chunked(
    *, document_id: uuid.UUID, strategy: str, version: str, config_hash: str
) -> int:
    with get_connection(autocommit=True) as conn:
        return ChunkRepository(conn).count_for_run(
            document_id=document_id,
            strategy=strategy,
            strategy_version=version,
            config_hash=config_hash,
        )


def _open_job(document_row: dict, *, strategy: str, config_hash: str) -> dict | None:
    """Claim (or create) the CHUNKING job and move it to PROCESSING.

    Runs on its own autocommit connection, deliberately outside the transaction
    that writes chunks: job state is an audit trail of what was attempted, and
    rolling the chunk write back must not erase the record of the attempt.
    """
    with get_connection(autocommit=True) as conn:
        jobs = ProcessingJobRepository(conn)
        job, created = jobs.enqueue(
            document_id=document_row["id"],
            filing_id=document_row.get("filing_id"),
            job_type=JobType.CHUNKING,
            payload={"strategy": strategy, "config_hash": config_hash},
        )
        if created:
            log.info(
                "Job created: CHUNKING status=QUEUED for %s", document_row["file_name"]
            )
        if job:
            jobs.set_status(job_id=job["id"], status=JobStatus.PROCESSING)
            log.info("Job PROCESSING: CHUNKING %s", document_row["file_name"])
        return job


def _close_job(job: dict | None, status: JobStatus, error: str | None = None) -> None:
    if job is None:
        return
    with get_connection(autocommit=True) as conn:
        ProcessingJobRepository(conn).set_status(
            job_id=job["id"], status=status, error_message=error
        )


def chunk_document(
    document_row: dict,
    *,
    engine: ChunkingEngine,
    config: ChunkingConfig,
    stats: ChunkingStats,
    force: bool = False,
) -> bool:
    """Chunk one document. True on success.

    The chunk write is transactional; the job lifecycle is not, so a rolled
    back write still leaves a FAILED job explaining why.
    """
    document_id: uuid.UUID = document_row["id"]
    file_name = document_row["file_name"]
    config_hash = config.fingerprint()

    # --- idempotency ------------------------------------------------------
    existing = _already_chunked(
        document_id=document_id,
        strategy=engine.name,
        version=engine.version,
        config_hash=config_hash,
    )
    if existing and not force:
        log.info(
            "Skipping (already chunked by %s v%s cfg=%s, %d chunks): %s",
            engine.name,
            engine.version,
            config_hash,
            existing,
            file_name,
        )
        stats.documents_skipped += 1
        return True

    job = _open_job(document_row, strategy=engine.name, config_hash=config_hash)

    try:
        if not document_row.get("processed_path"):
            raise RuntimeError(
                f"{file_name} has no processed_path; run the processing stage first"
            )

        canonical = read_canonical(document_row["processed_path"])
        result = engine.run(canonical, config)

        if not result.chunks:
            raise RuntimeError(
                f"{file_name} produced no chunks "
                f"(canonical document has {len(canonical.content)} root nodes)"
            )

        payload = [
            {
                "chunk_index": c.chunk_index,
                "text": c.text,
                "content_hash": c.content_hash,
                "token_count": c.token_count,
                "char_count": c.char_count,
                "node_ids": c.node_ids,
                "path": c.path,
                "section_node_id": c.section_node_id,
                "nodes": c.nodes_payload(),
                "has_table": c.has_table(),
                "metadata": c.metadata,
            }
            for c in result.chunks
        ]

        # One transaction for the whole set: delete the previous one and insert
        # every chunk, or leave the database exactly as it was.
        with get_connection() as conn:
            chunks_repo = ChunkRepository(conn)
            written = chunks_repo.replace_set(
                document_id=document_id,
                strategy=engine.name,
                strategy_version=engine.version,
                config_hash=config_hash,
                config=config.to_dict(),
                tokenizer=config.tokenizer,
                chunks=payload,
                section_id_by_key=chunks_repo.section_key_map(document_id),
            )

            if written != len(result.chunks):
                # A short write must never be reported as success; raising
                # inside the transaction rolls the partial set back.
                raise RuntimeError(
                    f"expected to write {len(result.chunks)} chunks, wrote {written}"
                )

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.error("Chunking FAILED: %s | %s", file_name, error)
        _close_job(job, JobStatus.FAILED, error)
        if job:
            log.info("Job FAILED: CHUNKING %s", file_name)
        stats.documents_failed += 1
        stats.errors.append(f"{file_name}: {error}")
        return False

    # Only now, with the transaction committed, is the job complete.
    _close_job(job, JobStatus.COMPLETED)

    s = result.stats()
    log.info(
        "Chunked %s | chunks=%d tokens avg=%s min=%s max=%s "
        "with_tables=%d over_budget=%d",
        file_name,
        s["chunks"],
        s["tokens_avg"],
        s["tokens_min"],
        s["tokens_max"],
        s["with_tables"],
        s["over_budget"],
    )
    if job:
        log.info("Job COMPLETED: CHUNKING %s", file_name)

    stats.documents_chunked += 1
    stats.chunks_written += written
    return True


def chunk_pending(
    *,
    strategy: str = DEFAULT_STRATEGY,
    config: ChunkingConfig | None = None,
    limit: int | None = None,
    force: bool = False,
    document_id: uuid.UUID | None = None,
) -> ChunkingStats:
    """Chunk every processed document that does not yet have this chunk set."""
    stats = ChunkingStats()
    config = config or ChunkingConfig()
    engine = ChunkingEngine(strategy)

    log.info(
        "Chunking stage starting | strategy=%s v%s tokenizer=%s "
        "max_tokens=%d overlap=%d cfg=%s",
        engine.name,
        engine.version,
        config.tokenizer,
        config.max_tokens,
        config.overlap_tokens,
        config.fingerprint(),
    )

    documents = _candidate_documents(limit, document_id)
    if not documents:
        log.info("No processed documents to chunk")
        return stats

    log.info("%d document(s) to consider", len(documents))

    for row in documents:
        try:
            chunk_document(
                row, engine=engine, config=config, stats=stats, force=force
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.error("Chunking aborted for %s: %s", row["file_name"], error)
            stats.documents_failed += 1
            stats.errors.append(f"{row['file_name']}: {error}")

    log.info("Chunking stage complete | %s", stats.summary())
    return stats
