# data

Local data volumes. **Contents are never committed** — only this structure is.

| Path         | Holds                                                             |
| ------------ | ----------------------------------------------------------------- |
| `raw/`       | Untouched source documents exactly as fetched                      |
| `raw/sec/`   | Raw SEC EDGAR filings                                              |
| `processed/` | Canonical structured documents (`*.canonical.json`) derived from `raw/` |
| `indexes/`   | Search and vector index artifacts                                  |
| `generated/` | Application output: reports, evaluation results, agent artifacts   |
| `postgres/`  | PostgreSQL data directory, bind-mounted into the container         |
| `indexes/qdrant/` | Qdrant storage, bind-mounted into the container               |

Rules:

- `raw/` is append-only and treated as immutable; anything else can be rebuilt.
- Nothing here is a source of truth for code.
- These paths are mounted into containers; see `.env.example`.
- `postgres/` and `indexes/qdrant/` are written by the database containers
  themselves. Do not edit them by hand. Deleting a folder destroys that
  store's data.
