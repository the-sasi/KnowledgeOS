"""Data model: status enums and row dataclasses.

These mirror `infrastructure/database/migrations/0001_initial_schema.sql`.
The SQL file is the source of truth; this module is the typed view of it that
the rest of the codebase programs against.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class DocumentStatus(str, Enum):
    DOWNLOADED = "DOWNLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobType(str, Enum):
    DOCUMENT_PROCESSING = "DOCUMENT_PROCESSING"
    CHUNKING = "CHUNKING"


@dataclass
class Company:
    id: uuid.UUID
    cik: str
    name: str
    ticker: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Filing:
    id: uuid.UUID
    company_id: uuid.UUID
    accession_number: str
    form_type: str
    filing_date: date | None = None
    report_date: date | None = None
    primary_document: str | None = None
    source_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Document:
    id: uuid.UUID
    filing_id: uuid.UUID
    document_type: str
    file_name: str
    storage_path: str
    status: DocumentStatus
    byte_size: int | None = None
    checksum_sha256: str | None = None
    source_url: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ProcessingJob:
    id: uuid.UUID
    job_type: JobType
    status: JobStatus
    document_id: uuid.UUID | None = None
    filing_id: uuid.UUID | None = None
    attempts: int = 0
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


def normalize_cik(cik: str | int) -> str:
    """Zero-pad a CIK to the 10-character form the schema requires.

    SEC returns CIKs both padded and unpadded depending on the endpoint;
    normalizing here is what keeps "320193" and "0000320193" from becoming two
    company rows.
    """
    digits = str(cik).strip().lstrip("CIK").lstrip("cik").strip()
    if not digits.isdigit():
        raise ValueError(f"CIK is not numeric: {cik!r}")
    return digits.zfill(10)
