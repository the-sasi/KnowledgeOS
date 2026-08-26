# ADR 0001: PostgreSQL for metadata, Qdrant for vectors

- **Status:** accepted
- **Date:** 2026-08-25

## Context

KnowledgeOS needs two distinct storage capabilities before any ingestion or
retrieval work can start:

1. A store for structured metadata — companies, filings, document lineage,
   ingestion state, and eventually evaluation runs. This needs transactions,
   relational integrity, and ad-hoc querying.
2. A store for dense vectors, with approximate nearest-neighbour search and
   metadata filtering, to back the retrieval module.

The platform must run entirely on a local machine through Docker, with no
cloud dependency and no managed service.

## Decision

Two separate containers, both defined in the root `docker-compose.yml`:

- **PostgreSQL 16 (alpine)** as the relational/metadata store.
- **Qdrant v1.12.4** as the vector store.

Both persist via **bind mounts into local project folders** — `./data/postgres`
and `./data/indexes/qdrant` — rather than named Docker volumes, so the on-disk
state is visible and inspectable from the host. Both are bound to `127.0.0.1`
and take all credentials, ports, and paths from `.env`.

## Alternatives considered

- **pgvector inside PostgreSQL** — one fewer service, and appealing for a
  project this size. Rejected for now because it couples the metadata schema's
  lifecycle to the index's, and makes it harder to swap the retrieval backend
  independently, which the module boundaries in `services/` are meant to
  preserve. Cheap to revisit: the extension can be added to the same Postgres
  container later.
- **SQLite for metadata** — simpler, but no concurrent writer story once
  ingestion and the API run as separate containers.
- **Chroma / Weaviate / Milvus** — Chroma is lighter but weaker on filtered
  search; Milvus needs several supporting containers, which conflicts with the
  "no extra services" constraint. Qdrant is a single container with a usable
  filtering model.

## Consequences

- Two stateful services to run, back up, and version.
- The retrieval module must keep vector records and their Postgres metadata in
  sync; there is no cross-store transaction. That consistency boundary now
  belongs to `services/retrieval/`.
- Bind mounts put the data under `./data/`, which is git-ignored. Deleting the
  repo folder deletes the databases; `docker compose down -v` does not, since
  there are no named volumes.
- `initdb` on a Windows bind mount is slow — first Postgres start takes ~55s
  before the health check passes, against ~10s on subsequent starts. The
  `start_period` accounts for this.
- Postgres writes as its own container UID. On Linux hosts the files under
  `./data/postgres` will be owned by that UID, not by your user.
- No schemas, tables, or collections are created yet — that is deliberate and
  belongs to the ingestion and retrieval work.

## Revisit when

- Keeping the two stores in sync turns into a recurring source of bugs, or
- vector volume stays small enough that pgvector in the existing Postgres would
  remove a service without costing recall or latency.
