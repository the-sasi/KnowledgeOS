"""Ingestion orchestration.

Ties the SEC scraper to the database: for each filing, find-or-create the
company, filing, and document rows, then queue a DOCUMENT_PROCESSING job.

Nothing here opens or parses the filing HTML. The raw file stays exactly as
downloaded; only its path, size, and checksum are recorded.

Two entry points:

* `ingest_from_sec()`  - talk to SEC EDGAR, download what is missing, persist.
* `ingest_from_disk()` - persist what is already in data/raw/sec/, no network.

Both converge on `persist_filing()`, so the database result is identical.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from knowledgeos.config import REPO_ROOT, settings
from knowledgeos.db.connection import get_connection
from knowledgeos.db.models import JobType
from knowledgeos.db.repositories import (
    CompanyRepository,
    DocumentRepository,
    FilingRepository,
    ProcessingJobRepository,
)
from knowledgeos.logging_setup import get_logger
from services.ingestion.sec import scraper

log = get_logger("ingestion.pipeline")


@dataclass
class IngestionStats:
    companies_created: int = 0
    companies_found: int = 0
    filings_created: int = 0
    filings_found: int = 0
    documents_created: int = 0
    documents_found: int = 0
    documents_failed: int = 0
    jobs_created: int = 0
    jobs_existing: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"companies +{self.companies_created}/={self.companies_found}  "
            f"filings +{self.filings_created}/={self.filings_found}  "
            f"documents +{self.documents_created}/={self.documents_found}  "
            f"failed={self.documents_failed}  "
            f"jobs +{self.jobs_created}/={self.jobs_existing}"
        )


def _relative_path(path: Path) -> str:
    """Repo-root-relative POSIX path, so records survive a repo move."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def persist_filing(
    *,
    company_info: dict,
    filing_meta: dict,
    file_name: str,
    destination: Path,
    filing_url: str | None,
    index_url: str | None,
    download_error: str | None,
    stats: IngestionStats,
) -> None:
    """Write one filing's company/filing/document/job rows.

    Runs in its own transaction so a single bad filing cannot roll back the
    rest of the run.
    """
    accession = filing_meta["accession_number"]

    with get_connection() as conn:
        companies = CompanyRepository(conn)
        filings = FilingRepository(conn)
        documents = DocumentRepository(conn)
        jobs = ProcessingJobRepository(conn)

        # --- company -------------------------------------------------------
        company, created = companies.upsert(
            cik=company_info["cik"],
            name=company_info["name"],
            ticker=company_info.get("ticker"),
            metadata={"source": "SEC EDGAR"},
        )
        if created:
            stats.companies_created += 1
            log.info("Company created: %s (CIK %s)", company["name"], company["cik"])
        else:
            stats.companies_found += 1
            log.info("Company found:   %s (CIK %s)", company["name"], company["cik"])

        # --- filing --------------------------------------------------------
        filing, created = filings.upsert(
            company_id=company["id"],
            accession_number=accession,
            form_type=filing_meta["form"],
            filing_date=filing_meta.get("filing_date"),
            report_date=filing_meta.get("report_date"),
            primary_document=filing_meta.get("primary_document"),
            primary_doc_description=filing_meta.get("primary_doc_description"),
            file_number=filing_meta.get("file_number"),
            items=filing_meta.get("items"),
            size_bytes=filing_meta.get("size"),
            source=filing_meta.get("source", "SEC EDGAR"),
            source_url=filing_url,
            index_url=index_url,
            metadata={},
        )
        if created:
            stats.filings_created += 1
            log.info("Filing created:  %s %s", filing["form_type"], accession)
        else:
            stats.filings_found += 1
            log.info("Filing found:    %s %s", filing["form_type"], accession)

        storage_path = _relative_path(destination)

        # --- failure path --------------------------------------------------
        if download_error is not None or not destination.is_file():
            error = download_error or f"Raw file missing on disk: {storage_path}"

            document = documents.mark_failed(
                filing_id=filing["id"],
                file_name=file_name,
                storage_path=storage_path,
                source_url=filing_url,
                error_message=error,
            )
            stats.documents_failed += 1
            stats.errors.append(f"{accession}: {error}")
            log.error(
                "Document status -> FAILED: %s (%s)", file_name, error
            )
            # No processing job: there is nothing valid to process.
            return

        # --- success path --------------------------------------------------
        document, created = documents.upsert_downloaded(
            filing_id=filing["id"],
            file_name=file_name,
            storage_path=storage_path,
            document_type="PRIMARY",
            content_type="text/html",
            byte_size=destination.stat().st_size,
            checksum_sha256=_sha256(destination),
            source_url=filing_url,
            metadata={"primary_document": filing_meta.get("primary_document")},
        )
        if created:
            stats.documents_created += 1
            log.info(
                "Document created: %s -> status=%s", file_name, document["status"]
            )
        else:
            stats.documents_found += 1
            log.info(
                "Document found:   %s -> status=%s", file_name, document["status"]
            )

        # --- processing job ------------------------------------------------
        job, created = jobs.enqueue(
            document_id=document["id"],
            filing_id=filing["id"],
            job_type=JobType.DOCUMENT_PROCESSING,
            payload={"storage_path": storage_path},
        )
        if created:
            stats.jobs_created += 1
            log.info(
                "Job created:     DOCUMENT_PROCESSING status=QUEUED for %s", file_name
            )
        else:
            stats.jobs_existing += 1
            log.info(
                "Job exists:      DOCUMENT_PROCESSING status=%s for %s",
                job["status"] if job else "UNKNOWN",
                file_name,
            )


def ingest_from_sec(companies: dict[str, str] | None = None) -> IngestionStats:
    """Scrape SEC EDGAR and persist the results."""
    stats = IngestionStats()
    targets = companies or scraper.COMPANIES

    log.info("KnowledgeOS - SEC ingestion starting")
    log.info("Output dir: %s", settings.raw_sec_dir)
    log.info("Forms: %s | filings/company: %s", scraper.FORMS, scraper.FILINGS_PER_COMPANY)

    for company_name, cik in targets.items():
        try:
            for company_info, result in scraper.scrape_company(company_name, cik):
                persist_filing(
                    company_info=company_info,
                    filing_meta=result.filing,
                    file_name=result.file_name,
                    destination=result.destination,
                    filing_url=result.filing_url,
                    index_url=result.index_url,
                    download_error=result.error,
                    stats=stats,
                )
        except Exception as exc:
            message = f"{company_name}: {type(exc).__name__}: {exc}"
            stats.errors.append(message)
            log.error("Company ingestion failed: %s", message)

    log.info("SEC ingestion complete | %s", stats.summary())
    return stats


def ingest_from_disk() -> IngestionStats:
    """Persist filings already present in data/raw/sec/, without any network.

    Reads each *.json metadata file the scraper wrote and locates the matching
    raw document beside it.
    """
    stats = IngestionStats()
    root = settings.raw_sec_dir

    if not root.is_dir():
        log.error("No raw SEC directory at %s", root)
        return stats

    metadata_files = sorted(root.rglob("*.json"))
    log.info("KnowledgeOS - ingesting %d metadata file(s) from %s", len(metadata_files), root)

    for metadata_path in metadata_files:
        try:
            filing_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            message = f"{metadata_path.name}: unreadable metadata: {exc}"
            stats.errors.append(message)
            log.error(message)
            continue

        required = {"cik", "company", "form", "accession_number", "primary_document"}
        missing = required - filing_meta.keys()
        if missing:
            message = f"{metadata_path.name}: metadata missing {sorted(missing)}"
            stats.errors.append(message)
            log.error(message)
            continue

        company_info = {
            "cik": filing_meta["cik"],
            "name": filing_meta["company"],
            "ticker": filing_meta.get("ticker"),
        }

        file_name = (
            f"{filing_meta['filing_date']}_"
            f"{filing_meta['accession_number']}_"
            f"{scraper.safe_filename(filing_meta['primary_document'])}"
        )
        destination = metadata_path.parent / file_name

        log.info(
            "SEC filing discovered: %s %s | filed=%s accession=%s",
            filing_meta["company"],
            filing_meta["form"],
            filing_meta.get("filing_date"),
            filing_meta["accession_number"],
        )

        persist_filing(
            company_info=company_info,
            filing_meta=filing_meta,
            file_name=file_name,
            destination=destination,
            filing_url=filing_meta.get("filing_url"),
            index_url=filing_meta.get("index_url"),
            download_error=None,
            stats=stats,
        )

    log.info("Disk ingestion complete | %s", stats.summary())
    return stats
