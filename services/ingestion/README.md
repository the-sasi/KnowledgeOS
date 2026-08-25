# services/ingestion

Acquires source documents and turns them into normalized, processed text.

First domain: **SEC EDGAR filings**.

Intended flow (not implemented):

1. Discover filings for a company/CIK.
2. Download raw artifacts into `data/raw/sec/`.
3. Parse, clean, and normalize into `data/processed/`.
4. Emit metadata for downstream retrieval.

Note: the SEC requires a descriptive `User-Agent` and enforces rate limits.
See `SEC_USER_AGENT` in `.env.example`.
