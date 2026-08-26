# notebooks

Exploratory and demonstration notebooks. Nothing here is part of the pipeline —
notebooks call into `knowledgeos/` and `services/`, never the other way round.

| Notebook | Purpose |
| --- | --- |
| `knowledgeos_walkthrough.ipynb` | Ingestion and processing, end to end |
| `chunking_walkthrough.ipynb` | Chunking: boundaries, tables, budgets, lineage, experiments |

## Running

These run on the **host**, not inside Docker, and reach PostgreSQL on
`localhost:5432` via `DATABASE_URL` in `.env`.

```bash
docker compose up -d                      # PostgreSQL + Qdrant must be healthy
pip install -r requirements-dev.txt       # psycopg, beautifulsoup4, lxml, requests
```

Then open the notebook in VS Code and run all cells.

The walkthrough is saved with its outputs, so it can be read without running.
It expects an empty database; the "start from scratch" cell near the top resets
the schema if you want to re-run it cleanly.
