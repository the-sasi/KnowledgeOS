# services

Independent domain modules. Each folder owns one capability, is developed and
tested on its own, and can be replaced without touching the others.

| Module          | Responsibility                                              |
| --------------- | ----------------------------------------------------------- |
| `ingestion`     | Fetch and normalize source documents (SEC EDGAR first)       |
| `retrieval`     | Chunking, indexing, search, and RAG assembly                 |
| `agents`        | Multi-step research workflows built on retrieval and models  |
| `models`        | Abstraction over LLM and embedding providers                 |
| `evaluation`    | Quality measurement for retrieval and agent output           |
| `guardrails`    | Input/output validation, safety, and policy checks           |
| `observability` | Logging, tracing, metrics, and cost accounting               |

Rules of thumb:

- A service depends on `models` and shared config, not on `apps/`.
- Cross-service communication goes through explicit interfaces, not imports of
  internal implementation details.
- Each service owns its own tests under `tests/`.
