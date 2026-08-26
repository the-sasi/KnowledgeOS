# services/chunking

Turns a canonical document into retrieval-sized **chunks**.

```
CanonicalDocument -> ChunkingEngine -> ChunkingStrategy -> DocumentChunk[] -> PostgreSQL
```

This stage does **not** embed, index, retrieve, rerank, or call a model. Its
only outputs are chunk rows and job state.

## Architecture

| File | Role |
| --- | --- |
| `tokenizer.py` | `Tokenizer` interface + `simple` / `character` implementations |
| `models.py` | `ChunkingConfig`, `DocumentChunk`, `ChunkingResult` |
| `base.py` | `ChunkingStrategy` interface, `StrategyRegistry`, errors |
| `strategies/structure_recursive.py` | The one implemented strategy |
| `engine.py` | Resolves a strategy by name, validates its output |
| `pipeline.py` | Orchestration, job state, transactional persistence |

## Adding a strategy later

Nothing outside `strategies/` changes:

```python
class FixedSizeChunker(ChunkingStrategy):
    name = "fixed_size"
    version = "1.0.0"

    def chunk(self, document, config) -> ChunkingResult:
        ...

register_strategy(FixedSizeChunker())
```

Import it from `strategies/__init__.py`; `pipeline.py` never names a strategy.
Run it with `--strategy fixed_size`, and its chunks sit alongside the existing
ones rather than replacing them.

## Structure-aware recursive chunking

The implemented strategy (`structure_recursive` v1.0.0). It uses the canonical
hierarchy first and falls back only when a unit exceeds the budget:

```
structural node (section)          largest meaningful unit that fits
    -> child structural nodes
        -> content nodes, packed in document order
            -> paragraph / sentence boundaries
                -> raw token split              last resort only
```

A section that fits becomes one chunk, whole. A section that does not is opened
up and its children are packed in order, so boundaries land on real structural
seams. The document is never simply cut every N tokens.

Chunks never span a section boundary — a chunk with ambiguous lineage is worse
than a smaller one.

### Tables

Treated as units. A table that fits is never split. One that does not is
divided **by rows**, with the caption and header repeated on every part, so each
part remains a readable table rather than a fragment of numbers that have lost
their columns. Every part records `source_node_id` and `table_part`, so the
original table is reconstructable.

A chunk stores its canonical nodes as well as its rendered text, so a table in
a chunk is still structured data. Chunks are not stored as flattened prose.

### Anything still over budget

Some units cannot be divided further — a single table row, or a sentence longer
than the budget. Those chunks carry `oversized: true` and `over_budget_by`
rather than being silently cut. The verification command reports them. This is
where a future table-specific strategy would earn its place.

## Configuration

No value here is "the correct" one; they exist to be benchmarked.

| Option | Default | Meaning |
| --- | --- | --- |
| `max_tokens` | 512 | Chunk budget |
| `min_tokens` | 64 | Below this, merge with a neighbour where the budget allows |
| `overlap_tokens` | **0** | Tokens of the previous chunk prepended. 0 is valid and is the default |
| `tokenizer` | `simple` | Named tokenizer |
| `include_path_prefix` | `false` | Prepend the section path to chunk text |

### Tokenizers

`simple` is a regex word/punctuation tokenizer, not a real BPE tokenizer. It
under-counts by roughly 25-35% against subword tokenizers on English prose, so
chunks run a little larger than the number suggests. That is acceptable while
the embedding model is undecided, and it is why `tokenizer` is recorded on
every chunk. Plugging in a model tokenizer later:

```python
@register_tokenizer
class TiktokenTokenizer(Tokenizer):
    name = "cl100k_base"
    ...
```

**Any chunk-size benchmark must be re-run once a real tokenizer is in place.**

## Lineage

Every chunk can be traced home:

```
chunk -> canonical node(s) -> document -> filing -> company
```

| Column | Holds |
| --- | --- |
| `node_ids` | Canonical node ids the chunk is made of |
| `node_path` | Section titles from the root, e.g. `{"Part II","Item 7","MD&A"}` |
| `section_node_id` | Innermost enclosing section |
| `section_id` | FK to `document_sections` |
| `content_nodes` | The canonical nodes themselves, as JSON |

Paths come from the canonical hierarchy. No source-specific section names are
hard-coded anywhere — the same chunker handles articles, manuals, and reports.

## Versioning and experiments

A chunk set is identified by:

```
(document_id, strategy, strategy_version, config_hash, chunk_index)
```

so several sets coexist per document and can be compared:

```
Document
├── structure_recursive v1.0.0  cfg=2c07e8f1  max_tokens=512  overlap=0
├── structure_recursive v1.0.0  cfg=f0615e9a  max_tokens=1024 overlap=0
└── structure_recursive v1.0.0  cfg=37c3a54c  max_tokens=512  overlap=64
```

Re-running the same strategy, version, and configuration is a no-op. Changing
any of them produces a new set rather than overwriting the old one.

## Jobs and transactions

```
CHUNKING job:  QUEUED -> PROCESSING -> COMPLETED
                                   \-> FAILED
```

One document's whole chunk set — deleting any previous set for that run key,
inserting every chunk, and completing the job — happens in a **single
transaction**. A failure rolls back to the previous state, so a partially
written chunk set can never be reported as complete. The failure reason is
recorded on its own connection so it survives that rollback.

## Running

```bash
docker compose run --rm app python -m services.chunking
docker compose run --rm app python -m services.chunking --max-tokens 1024
docker compose run --rm app python -m services.chunking --overlap-tokens 64
docker compose run --rm app python -m services.chunking --force --limit 5
docker compose run --rm app python -m services.chunking --list-strategies

docker compose run --rm app python scripts/verify_chunking.py
docker compose run --rm app python scripts/verify_chunking.py --runs
docker compose run --rm app python scripts/verify_chunking.py --by-path
docker compose run --rm app python scripts/verify_chunking.py --file <name> --chunk 12
```

`notebooks/chunking_walkthrough.ipynb` runs the same ground interactively.
