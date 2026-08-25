# data

Local data volumes. **Contents are never committed** — only this structure is.

| Path         | Holds                                                             |
| ------------ | ----------------------------------------------------------------- |
| `raw/`       | Untouched source documents exactly as fetched                      |
| `raw/sec/`   | Raw SEC EDGAR filings                                              |
| `processed/` | Cleaned, normalized, chunk-ready text derived from `raw/`          |
| `indexes/`   | Search and vector index artifacts                                  |
| `generated/` | Application output: reports, evaluation results, agent artifacts   |

Rules:

- `raw/` is append-only and treated as immutable; anything else can be rebuilt.
- Nothing here is a source of truth for code.
- These paths are mounted into containers; see `.env.example`.
