# infrastructure/vector_store

**Qdrant v1.12.4** — the vector store backing retrieval.
See [ADR 0001](../../docs/decisions/0001-postgresql-and-qdrant.md).

Defined as the `qdrant` service in the root `docker-compose.yml`.

| | |
| --- | --- |
| Container | `knowledgeos-qdrant` |
| HTTP / REST | `localhost:${QDRANT_HTTP_PORT}` (default `6333`, loopback only) |
| gRPC | `localhost:${QDRANT_GRPC_PORT}` (default `6334`) |
| Address from other containers | `qdrant:6333` |
| Persistence | bind mount `${QDRANT_DATA_DIR}` (default `./data/indexes/qdrant`) → `/qdrant/storage` |
| Health check | `GET /readyz` probed over bash `/dev/tcp` (the image ships no curl) |

The web dashboard is at <http://localhost:6333/dashboard>. It ships with
Qdrant and needs no extra container. It shows no collections until the
indexing stage exists.

## Usage

```bash
docker compose up -d qdrant
curl http://localhost:6333/collections     # -> {"result":{"collections":[]},...}
```

## Not done yet

No collections, no vector configuration, no data. Collection creation depends
on the embedding model, which has not been chosen — that decision, including
vector dimension and distance metric, belongs to `services/retrieval/` and
needs its own ADR.

## Note

Qdrant runs without an API key here because it is bound to loopback on a local
machine. Anything beyond local development needs `QDRANT__SERVICE__API_KEY`
set from `.env`.
