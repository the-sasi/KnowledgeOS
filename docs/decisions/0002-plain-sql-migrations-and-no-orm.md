# ADR 0002: Plain SQL migrations, no ORM, shared `knowledgeos` package

- **Status:** accepted
- **Date:** 2026-08-25

## Context

The first ingestion stage needed a schema, a way to create it reproducibly,
and somewhere for shared database code to live. Three questions had to be
answered at once: how migrations run, whether an ORM sits between the code and
PostgreSQL, and where cross-service code belongs given the `apps/` +
`services/` layout from the initial scaffold.

## Decision

**Migrations are numbered `.sql` files** in
`infrastructure/database/migrations/`, applied in order by a ~150-line runner
(`knowledgeos/db/migrate.py`) that records each one in a `schema_migrations`
table with a checksum.

**No ORM.** Repositories issue SQL directly through psycopg 3, returning
dict rows. `knowledgeos/db/models.py` holds status enums and dataclasses as a
typed view of the schema, not as a mapping layer.

**Shared code lives in a top-level `knowledgeos/` package** — config, logging,
and the database layer. `services/*` holds domain modules and imports from it.

## Alternatives considered

- **Alembic** — the standard answer, but its value is autogenerating diffs
  from ORM models, and there are no ORM models. Without that it is a
  dependency and a `versions/` directory wrapping SQL we would write anyway.
- **SQLAlchemy** — would earn its place once query composition gets complex.
  Today every query is a small, explicit statement, and the schema is the
  thing worth reading. Adding it later does not invalidate the SQL files.
- **`docker-entrypoint-initdb.d`** — Postgres runs those scripts only when
  initializing an empty data directory, so it cannot apply a second migration
  to an existing database. Fine for a demo, not for incremental schema work.
- **Putting the DB layer under `services/database/`** — rejected because
  `services/` is for domain modules, and retrieval and the API will need this
  code too. A shared package states that relationship honestly.

## Consequences

- Schema changes are hand-written SQL. That is more typing and more control;
  indexes, partial unique indexes, and CHECK constraints are all expressed
  directly.
- An applied migration must never be edited. The runner compares checksums and
  refuses to continue if one changed, pointing at `--reset` for development.
- No model-to-table drift detection. `models.py` and the SQL can diverge
  silently; keeping them together is a review responsibility.
- Migrations run inside a transaction, so a failure leaves the schema
  untouched.

## Revisit when

- Query composition (dynamic filters for retrieval) starts producing string
  concatenation, which is when an ORM or query builder pays for itself, or
- the schema changes often enough that hand-writing migrations is the
  bottleneck.
