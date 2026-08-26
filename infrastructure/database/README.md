# infrastructure/database

**PostgreSQL 16 (alpine)** — the metadata and document store.
See [ADR 0001](../../docs/decisions/0001-postgresql-and-qdrant.md).

Defined as the `postgres` service in the root `docker-compose.yml`.

| | |
| --- | --- |
| Container | `knowledgeos-postgres` |
| Host address | `localhost:${POSTGRES_PORT}` (default `5432`, loopback only) |
| Address from other containers | `postgres:5432` |
| Persistence | bind mount `${POSTGRES_DATA_DIR}` (default `./data/postgres`) → `/var/lib/postgresql/data` |
| Health check | `pg_isready` |
| Credentials | `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` in `.env` |

## Usage

```bash
docker compose up -d postgres
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

For a web UI, start Adminer (development only, `ui` profile):

```bash
docker compose --profile ui up -d
```

Then <http://localhost:8080> - system `PostgreSQL`, server `postgres`,
credentials from `.env`.

## Migrations

Numbered SQL files in `migrations/`, applied in order and recorded in a
`schema_migrations` table with a checksum. See
[ADR 0002](../../docs/decisions/0002-plain-sql-migrations-and-no-orm.md).

```bash
docker compose run --rm app python -m knowledgeos.db.migrate            # apply pending
docker compose run --rm app python -m knowledgeos.db.migrate --status   # what is applied
docker compose run --rm app python -m knowledgeos.db.migrate --reset    # drop + re-apply (destroys data)
```

| Migration | Contents |
| --- | --- |
| `0001_initial_schema.sql` | companies, filings, documents, document_sections, document_chunks, vector_index_records, processing_jobs; the `document_status` / `job_status` / `job_type` enums; `updated_at` triggers |
| `0002_document_processing.sql` | `documents.doc_format`, `processed_path`, `processor_name`, `processor_version`, `processed_at`; processor columns on `document_sections` |
| `0003_generic_structural_nodes.sql` | `document_sections.node_type`, so the outline can hold structural node types beyond SECTION |

Never edit an applied migration — the runner compares checksums and refuses to
continue if one changed. Add a new numbered file instead, or use `--reset` in
development.

The runner lives in `knowledgeos/db/migrate.py`; the access layer is in
`knowledgeos/db/repositories.py`.

## Note

The data lives in `./data/postgres` on the host and is git-ignored. To wipe the
database, stop the stack and delete that folder:

```bash
docker compose down
rm -rf data/postgres
```

First start runs `initdb` and takes roughly a minute on a Windows bind mount;
later starts are ~10s.
