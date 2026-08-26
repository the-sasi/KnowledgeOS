"""SEC EDGAR scraper.

Ported from the original data_extract notebook. The request handling, URL
construction, output layout, and file naming are unchanged, so files already
in data/raw/sec/ are recognised as-is.

What changed: print() became logging, and each filing now yields a
FilingDownload result instead of only side effects, so the ingestion pipeline
can tell downloaded from skipped from failed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

from knowledgeos.config import settings
from knowledgeos.logging_setup import get_logger

log = get_logger("ingestion.sec.scraper")


# ============================================================
# Configuration
# ============================================================

COMPANIES = {
    "NVIDIA": "0001045810",
    "Microsoft": "0000789019",
    "Apple": "0000320193",
    "Amazon": "0001018724",
    "Alphabet": "0001652044",
    "Meta": "0001326801",
    "Tesla": "0001318605",
    "AMD": "0000002488",
    "Intel": "0000050863",
    "Oracle": "0001341439",
}

FORMS = ["10-K"]

# Number of filings to download per company. Start small. Increase later.
FILINGS_PER_COMPANY = 5

OUTPUT_DIR = settings.raw_sec_dir

USER_AGENT = settings.sec_user_agent

SEC_DATA_BASE = "https://data.sec.gov"
SEC_ARCHIVE_BASE = "https://www.sec.gov"


# ============================================================
# Results
# ============================================================


@dataclass
class FilingDownload:
    """Outcome of handling one filing."""

    filing: dict
    filing_url: str
    index_url: str
    file_name: str
    destination: Path
    metadata_path: Path
    # "DOWNLOADED" (fetched now), "SKIPPED" (already on disk), or "FAILED"
    outcome: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in ("DOWNLOADED", "SKIPPED")


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }
)


def get_json(url: str) -> dict:
    """GET JSON from SEC with basic retry handling."""

    for attempt in range(3):
        try:
            response = session.get(url, timeout=30)

            if response.status_code == 429:
                wait = 2**attempt
                log.warning("Rate limited by SEC. Waiting %ss...", wait)
                time.sleep(wait)
                continue

            response.raise_for_status()
            return response.json()

        except requests.RequestException as exc:
            if attempt == 2:
                raise

            wait = 2**attempt
            log.warning("Request failed: %s. Retrying in %ss...", exc, wait)
            time.sleep(wait)

    raise RuntimeError(f"Unable to fetch {url}")


def download_file(url: str, destination: Path) -> None:
    """Download a filing document."""

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }

    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=60)

            if response.status_code == 429:
                wait = 2**attempt
                log.warning("Rate limited by SEC. Waiting %ss...", wait)
                time.sleep(wait)
                continue

            response.raise_for_status()

            destination.write_bytes(response.content)
            return

        except requests.RequestException:
            if attempt == 2:
                raise

            wait = 2**attempt
            time.sleep(wait)

    raise RuntimeError(f"Unable to download {url}")


# ============================================================
# SEC metadata
# ============================================================


def get_company_submissions(cik: str) -> dict:
    """Get filing history for a company."""

    cik_padded = cik.zfill(10)
    url = f"{SEC_DATA_BASE}/submissions/CIK{cik_padded}.json"

    log.info("Fetching submissions: %s", url)

    return get_json(url)


def get_recent_filings(submissions: dict) -> list[dict]:
    """Extract recent filings from the SEC compact filing structure."""

    recent = submissions["filings"]["recent"]

    filings = []

    for i in range(len(recent["form"])):
        filings.append(
            {
                "accession_number": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
                "form": recent["form"][i],
                "primary_document": recent["primaryDocument"][i],
                "primary_doc_description": recent.get(
                    "primaryDocDescription", [None] * len(recent["form"])
                )[i],
                "file_number": recent["fileNumber"][i],
                "items": recent.get("items", [None] * len(recent["form"]))[i],
                "size": recent.get("size", [None] * len(recent["form"]))[i],
            }
        )

    return filings


# ============================================================
# URL construction
# ============================================================


def build_filing_url(cik: str, accession_number: str, primary_document: str) -> str:
    """Build the URL for the primary filing document."""

    cik_numeric = str(int(cik))
    accession_no_dashes = accession_number.replace("-", "")

    return (
        f"{SEC_ARCHIVE_BASE}/Archives/edgar/data/"
        f"{cik_numeric}/{accession_no_dashes}/{primary_document}"
    )


def build_submission_index_url(cik: str, accession_number: str) -> str:
    """Build the SEC filing index page URL."""

    cik_numeric = str(int(cik))
    accession_no_dashes = accession_number.replace("-", "")

    return (
        f"{SEC_ARCHIVE_BASE}/Archives/edgar/data/"
        f"{cik_numeric}/{accession_no_dashes}/{accession_number}-index.html"
    )


# ============================================================
# File utilities
# ============================================================


def safe_filename(value: str) -> str:
    """Make a string safe for filesystem usage."""

    return re.sub(r"[^a-zA-Z0-9._-]", "_", value)


def build_metadata(filing: dict, filing_url: str, index_url: str) -> dict:
    return {
        "company": filing["company"],
        "ticker": filing.get("ticker"),
        "cik": filing["cik"],
        "form": filing["form"],
        "filing_date": filing["filing_date"],
        "report_date": filing["report_date"],
        "accession_number": filing["accession_number"],
        "primary_document": filing["primary_document"],
        "primary_doc_description": filing["primary_doc_description"],
        "file_number": filing["file_number"],
        "items": filing["items"],
        "size": filing["size"],
        "filing_url": filing_url,
        "index_url": index_url,
        "source": "SEC EDGAR",
    }


def save_metadata(
    company_dir: Path, filing: dict, filing_url: str, index_url: str
) -> Path:
    metadata_path = company_dir / f"{filing['accession_number']}.json"
    metadata_path.write_text(
        json.dumps(build_metadata(filing, filing_url, index_url), indent=2),
        encoding="utf-8",
    )
    return metadata_path


# ============================================================
# Scraping
# ============================================================


def discover_filings(company_name: str, cik: str) -> tuple[dict, list[dict]]:
    """Return (company_info, selected_filings) for a company.

    Performs no downloads. company_info carries the official SEC name and
    ticker, which is what the database should record rather than the local
    lookup key.
    """
    submissions = get_company_submissions(cik)

    official_name = submissions.get("name", company_name)
    tickers = submissions.get("tickers", [])
    ticker = tickers[0] if tickers else None

    company_info = {
        "cik": cik,
        "name": official_name,
        "ticker": ticker,
        "local_name": company_name,
    }

    selected = [f for f in get_recent_filings(submissions) if f["form"] in FORMS]
    selected = selected[:FILINGS_PER_COMPANY]

    for filing in selected:
        filing["company"] = official_name
        filing["ticker"] = ticker
        filing["cik"] = cik

    return company_info, selected


def fetch_filing(company_name: str, filing: dict) -> FilingDownload:
    """Download one filing's primary document, unless it is already on disk.

    Never raises: a failure is returned as outcome="FAILED" with the error
    text, so the caller can record it rather than losing the whole run.
    """
    cik = filing["cik"]
    accession = filing["accession_number"]
    primary_document = filing["primary_document"]

    filing_url = build_filing_url(cik, accession, primary_document)
    index_url = build_submission_index_url(cik, accession)

    company_dir = OUTPUT_DIR / safe_filename(company_name) / filing["form"]
    company_dir.mkdir(parents=True, exist_ok=True)

    file_name = (
        f"{filing['filing_date']}_{accession}_{safe_filename(primary_document)}"
    )
    destination = company_dir / file_name
    metadata_path = company_dir / f"{accession}.json"

    result = FilingDownload(
        filing=filing,
        filing_url=filing_url,
        index_url=index_url,
        file_name=file_name,
        destination=destination,
        metadata_path=metadata_path,
        outcome="SKIPPED",
    )

    log.info(
        "SEC filing discovered: %s %s | filed=%s report=%s accession=%s doc=%s",
        filing["company"],
        filing["form"],
        filing["filing_date"],
        filing["report_date"],
        accession,
        primary_document,
    )

    try:
        if destination.exists():
            log.info("Already downloaded, skipping fetch: %s", destination.name)
            result.outcome = "SKIPPED"
        else:
            log.info("Downloading: %s", filing_url)
            download_file(filing_url, destination)
            log.info(
                "Filing downloaded: %s (%s bytes)",
                destination.name,
                destination.stat().st_size,
            )
            result.outcome = "DOWNLOADED"
            # Be polite to SEC servers.
            time.sleep(0.2)

        if not metadata_path.exists():
            save_metadata(company_dir, filing, filing_url, index_url)

    except Exception as exc:
        # Do not leave a truncated file behind that a later run would treat as
        # a completed download.
        if result.outcome != "SKIPPED" and destination.exists():
            try:
                destination.unlink()
            except OSError:
                pass

        result.outcome = "FAILED"
        result.error = f"{type(exc).__name__}: {exc}"
        log.error("Download FAILED for accession %s: %s", accession, result.error)

    time.sleep(0.2)
    return result


def scrape_company(company_name: str, cik: str) -> Iterator[tuple[dict, FilingDownload]]:
    """Yield (company_info, download_result) for each selected filing."""

    log.info("=" * 60)
    log.info("Company: %s (CIK %s)", company_name, cik)

    company_info, filings = discover_filings(company_name, cik)

    if not filings:
        log.warning("No matching filings found for %s", company_name)
        return

    log.info(
        "Discovered %d %s filing(s) for %s",
        len(filings),
        "/".join(FORMS),
        company_info["name"],
    )

    for filing in filings:
        yield company_info, fetch_filing(company_name, filing)
