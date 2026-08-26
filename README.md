# KnowledgeOS

A local, fully Dockerized AI research and knowledge platform.

The first domain is **business/company research using SEC EDGAR filings**.

> **Status: chunking.** SEC filings are ingested, processed into canonical
> structured documents, and chunked into retrieval-sized units in PostgreSQL.
> Embeddings, Qdrant collections, retrieval, RAG, agents, MCP, evaluation, and
> UI are not implemented yet.

## Principles

1. **Local first.** Everything runs on a developer machine through Docker.
2. **Modular.** Each component lives behind its own folder so it can be built,
   tested, and replaced independently.
3. **Separated concerns.** Application code, infrastructure, configuration,
   data, tests, and documentation do not mix.
4. **Incremental.** Components are added one at a time; nothing is chosen
   before it is needed.
5. **No secrets in the repository.** Only `.env.example` is committed.

## Layout

| Path              | Purpose                                                        |
| ----------------- | -------------------------------------------------------------- |
| `knowledgeos/`    | Shared core: config, logging, database layer, migrations runner  |
| `apps/`           | Deployable entry points (`api`, `web`)                          |
| `services/`       | Independent domain modules (ingestion, retrieval, agents, ...)   |
| `infrastructure/` | Database, vector store, and Docker infrastructure definitions    |
| `config/`         | Environment and application configuration files                  |
| `data/`           | Local data volumes — never committed                             |
| `tests/`          | Unit, integration, and evaluation tests                          |
| `docs/`           | Architecture, decisions, experiments, development notes          |
| `notebooks/`      | Exploratory notebooks; a runnable walkthrough of the pipeline    |
| `scripts/`        | Developer and operational helper scripts                         |

Each folder has its own `README.md` describing its intended responsibility.

## Decided so far

| Concern       | Choice                  | ADR |
| ------------- | ----------------------- | --- |
| Metadata store | PostgreSQL 16 (alpine) | [0001](docs/decisions/0001-postgresql-and-qdrant.md) |
| Vector store   | Qdrant v1.12.4         | [0001](docs/decisions/0001-postgresql-and-qdrant.md) |
| Migrations     | Plain SQL + small runner, no ORM | [0002](docs/decisions/0002-plain-sql-migrations-and-no-orm.md) |
| Canonical documents | JSON on disk, PostgreSQL for state | [0003](docs/decisions/0003-canonical-document-representation.md) |
| Canonical shape | Generic node tree; sections are one node type | [0004](docs/decisions/0004-generic-node-canonical-model.md) |
| Runtime        | Python 3.11, psycopg 3 | [0002](docs/decisions/0002-plain-sql-migrations-and-no-orm.md) |

## Deliberately undecided

No choice has been made yet for: LLM provider, embedding model, web
framework, agent orchestration library, or observability stack.
These will be recorded as ADRs in [`docs/decisions/`](docs/decisions/) when the
time comes.

## Getting started

The backing services run now; the application does not exist yet.

```bash
cp .env.example .env      # then edit POSTGRES_PASSWORD
docker compose up -d
docker compose ps         # both services should report (healthy)
```

| Service  | Host address     | Check |
| -------- | ---------------- | ----- |
| PostgreSQL | `localhost:5432` | `docker compose exec postgres psql -U knowledgeos -d knowledgeos` |
| Qdrant     | `localhost:6333` | `curl http://localhost:6333/collections` |

Qdrant's dashboard is at <http://localhost:6333/dashboard>.

Both are bound to `127.0.0.1`, so they are reachable from your machine but not
from the network. Both persist into local project folders — `./data/postgres`
and `./data/indexes/qdrant` — which are git-ignored. `docker compose down`
keeps your data; deleting those folders destroys it.

First `up` runs Postgres `initdb` and takes about a minute; later starts are
~10 seconds.

Then create the schema and ingest:

```bash
docker compose run --rm app python -m knowledgeos.db.migrate   # create the schema
docker compose run --rm app python -m services.ingestion       # download + track SEC filings
docker compose run --rm app python scripts/verify_ingestion.py # show what landed
```

Then process the raw filings into canonical documents:

```bash
docker compose run --rm app python -m services.processing        # raw -> canonical
docker compose run --rm app python scripts/verify_processing.py  # summary + integrity
docker compose run --rm app python -m pytest tests/unit -q       # unit tests
```

Re-running either stage is safe — neither creates duplicates.

Qdrant has no collections yet; the vector side is scaffolding only.

The `app` service is behind a compose profile, so `docker compose up -d`
starts only the two databases. Commands run through
`docker compose run --rm app ...`.

## Browsing the data

Both stores have a web UI. The Qdrant dashboard ships with Qdrant; PostgreSQL
gets Adminer, started only with the `ui` profile so the default stack stays at
two containers.

```bash
docker compose --profile ui up -d
```

| UI | URL | Notes |
| --- | --- | --- |
| PostgreSQL (Adminer) | <http://localhost:8080> | System `PostgreSQL`, server `postgres` (prefilled), credentials from `.env` |
| Qdrant dashboard | <http://localhost:6333/dashboard> | Built in, no extra container. No collections yet |

Both bind to `127.0.0.1` only. Adminer is a development tool, not part of the
platform - stop it with `docker compose --profile ui stop adminer`.
