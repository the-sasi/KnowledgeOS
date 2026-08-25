# KnowledgeOS

A local, fully Dockerized AI research and knowledge platform.

The first domain is **business/company research using SEC EDGAR filings**.

> **Status: scaffold only.** This repository currently contains the folder
> structure and placeholder files. No application logic, APIs, RAG, agents,
> database connections, ingestion pipelines, models, embeddings, or UI have
> been implemented yet.

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
| `apps/`           | Deployable entry points (`api`, `web`)                          |
| `services/`       | Independent domain modules (ingestion, retrieval, agents, ...)   |
| `infrastructure/` | Database, vector store, and Docker infrastructure definitions    |
| `config/`         | Environment and application configuration files                  |
| `data/`           | Local data volumes — never committed                             |
| `tests/`          | Unit, integration, and evaluation tests                          |
| `docs/`           | Architecture, decisions, experiments, development notes          |
| `scripts/`        | Developer and operational helper scripts                         |

Each folder has its own `README.md` describing its intended responsibility.

## Deliberately undecided

No choice has been made yet for: database, vector database, LLM provider,
embedding model, application framework, web framework, orchestration library,
or observability stack. These will be recorded as ADRs in
[`docs/decisions/`](docs/decisions/) when the time comes.

## Getting started

Nothing is runnable yet. Once the first service exists:

```bash
cp .env.example .env
docker compose up --build
```

`Dockerfile` and `docker-compose.yml` are intentionally left as commented
placeholders — they will not build until a base image and a first service are
chosen.
