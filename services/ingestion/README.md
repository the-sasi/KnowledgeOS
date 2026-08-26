# services/ingestion

Acquires source documents and records them in PostgreSQL.

First domain: **SEC EDGAR filings**.

## What this stage does

1. Discover 10-K filings for each configured company via the SEC submissions API.
2. Download the primary document into `data/raw/sec/` — unchanged, never parsed.
3. Find-or-create the `companies`, `filings`, and `documents` rows.
4. Set the document status to `DOWNLOADED` and queue a `DOCUMENT_PROCESSING` job.

It does **not** open the HTML. No parsing, sectioning, chunking, or embedding
happens here — those are later stages, and the queued job is the handoff point.

## Layout

| File | Role |
| --- | --- |
| `sec/scraper.py` | SEC EDGAR client: discovery, download, metadata files |
| `pipeline.py` | Orchestration: scraper results → database rows → queued job |
| `__main__.py` | CLI entry point |

The database layer it writes through lives in `knowledgeos/db/`.

## Running

```bash
# Scrape SEC EDGAR, then persist
docker compose run --rm app python -m services.ingestion

# Only certain companies
docker compose run --rm app python -m services.ingestion --company Meta --company Apple

# Persist what is already in data/raw/sec/, no network calls
docker compose run --rm app python -m services.ingestion --from-disk
```

`--from-disk` reads the `*.json` metadata files the scraper writes and locates
the matching document beside each one. Useful for rebuilding the database from
files already downloaded.

## Idempotency

Re-running is safe and creates nothing new:

| Table | Natural key |
| --- | --- |
| `companies` | `cik`, zero-padded to 10 characters |
| `filings` | `(company_id, accession_number)` |
| `documents` | `(filing_id, file_name)` |
| `processing_jobs` | one open job per `(document_id, job_type)` |

A re-run never drags a document backwards: a `PROCESSED` document stays
`PROCESSED`. The one status change on re-run is recovery — a `FAILED` document
whose file is now present returns to `DOWNLOADED`, its error is cleared, and a
job is queued.

## Failure handling

A download failure records the document as `FAILED` with the error message and
leaves `byte_size` and `checksum_sha256` NULL, so a failed document can never
be mistaken for a complete one. **No processing job is queued** — there is
nothing valid to process. A partially written file is deleted so the next run
does not treat it as a finished download.

Each filing is persisted in its own transaction, so one bad filing does not
roll back the rest of the run.

## Known limitation

`FILINGS_PER_COMPANY` is a ceiling, not a guarantee. The scraper reads only
`filings.recent` from the SEC submissions API, which covers roughly the last
1000 filings of any type. For companies that file frequently this can hold
fewer than five 10-Ks — Meta and Alphabet currently yield 2 and 3. Older
filings live in the paginated `filings.files[]` entries, which this stage does
not follow yet.

## Configuration

`COMPANIES`, `FORMS`, and `FILINGS_PER_COMPANY` are constants at the top of
`sec/scraper.py`. `SEC_USER_AGENT` comes from `.env`.

> The SEC requires a descriptive User-Agent with a real contact address and
> rate-limits by it. Set `SEC_USER_AGENT` in `.env` before any sizeable run.
> See <https://www.sec.gov/os/webmaster-faq#developers>.
