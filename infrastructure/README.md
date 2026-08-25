# infrastructure

Everything needed to *run* KnowledgeOS locally, kept apart from application
code.

- `database/` — relational/document store definitions, schema, migrations
- `vector_store/` — vector index infrastructure
- `docker/` — per-service Dockerfiles, compose overrides, entrypoints

Nothing here is configured yet; no engines have been chosen.
