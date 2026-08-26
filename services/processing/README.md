# services/processing

Turns a raw source file into a **canonical structured document**, independent
of the original format.

```
RAW DOCUMENT  ->  GENERIC DOCUMENT PROCESSING  ->  CANONICAL DOCUMENT
```

This stage does **not** chunk, embed, index, retrieve, or call a model. Its
only outputs are a canonical JSON file and database state.

## Architecture

```
                 detection.py          base.py
                 (what is it?)     (interface + registry)
                       |                   |
    documents ---------+-------------------+--> parsers/html_parser.py
    (PostgreSQL)                           |     (first implementation)
                                           |
                                    canonical.py
                                (format-independent shape)
                                           |
                        +------------------+------------------+
                        |                                     |
                  storage.py                            pipeline.py
          data/processed/**.canonical.json      document + job state machine
```

| File | Role |
| --- | --- |
| `detection.py` | Identify the format from content, then extension, then mimetype |
| `base.py` | `DocumentParser` interface, `ParserRegistry`, error types |
| `canonical.py` | `CanonicalDocument` / `Node` / `NodeType` / `Table` |
| `parsers/html_parser.py` | HTML parser (`html` v2.0.0) |
| `storage.py` | Deterministic output paths, atomic JSON writes |
| `pipeline.py` | Orchestration and status transitions |

## Adding a format later

Nothing outside `parsers/` changes:

```python
class PdfParser(DocumentParser):
    name = "pdf"
    version = "0.1.0"
    supported_formats = frozenset({DocumentFormat.PDF})

    def parse(self, source: SourceInfo, raw: bytes) -> CanonicalDocument:
        ...

register_parser(PdfParser())
```

Import it from `parsers/__init__.py` and the pipeline resolves it through the
registry. `pipeline.py` never names a parser.

## Canonical representation

A document is a tree of generic **nodes**. There is no privileged "section"
concept - a section is one node type, used when a document actually has
sections. See [ADR 0004](../../docs/decisions/0004-generic-node-canonical-model.md).

```
CanonicalDocument
├── schema_version            "2.0"
├── source        SourceInfo  path, file_name, doc_format, media_type,
│                             byte_size, checksum_sha256, source_url
├── processor     ProcessorInfo   name, version, processed_at
├── metadata      dict        whatever the source supplied
├── stats         dict        derived counts, per node type
└── content       [Node]      root nodes, in document order
    ├── id, type, text, ordinal
    ├── attributes  dict      level, path, language - type-specific
    ├── table       Table     header[], rows[][], caption  (TABLE nodes)
    └── children    [Node]    ← recursive; hierarchy is real nesting
```

| Node type | Kind | Carries |
| --- | --- | --- |
| `SECTION` | structural | title in `text`, `level`/`path`, child nodes |
| `LIST` | structural | `LIST_ITEM` children |
| `FIGURE`, `CONTAINER` | structural | grouped children |
| `HEADING` | content | heading text, `level` |
| `PARAGRAPH` | content | text |
| `LIST_ITEM` | content | text |
| `TABLE` | content | `Table(header, rows, caption)` |
| `CODE` | content | source text with line structure, `language` |
| `QUOTE` | content | quoted text |
| `CAPTION` | content | caption text |

The same model carries very different documents:

```
Document              Document              Document
├── section           ├── heading           ├── section "Header"
│   ├── paragraph     ├── paragraph         ├── section "Customer"
│   └── section       ├── code              ├── table
└── section           └── table             └── section "Totals"
(sectioned filing)    (technical page)      (invoice)
```

Structure is preserved, not flattened. `document.text()` is a **derived view**,
never the stored form. Consume the tree with `has_sections()`,
`find(NodeType...)`, `walk()`, and `tables()`.

## Where output goes

| | |
| --- | --- |
| Canonical JSON | `data/processed/<mirror of raw path>.canonical.json` |
| Structural outline | `document_sections` rows - one per structural node, tagged with `node_type` |
| Document state | `documents.status`, `processed_path`, `processor_name`, `processor_version`, `processed_at` |

The split is deliberate: PostgreSQL holds queryable **state and structure**,
the filesystem holds **bulk content**, mirroring how raw files are handled.
`data/raw` is never written to.

## State transitions

```
document:  DOWNLOADED / FAILED --> PROCESSING --> PROCESSED
                                              \--> FAILED (error_message set)

job:       QUEUED               --> PROCESSING --> COMPLETED
                                              \--> FAILED (error_message set)
```

A failure clears `processed_path`, so a FAILED document never points at stale
output, and deletes any partial section rows from that attempt.

## Idempotency and versioning

The output path is derived from the input path, so reprocessing overwrites
rather than accumulating. Section rows are replaced wholesale per document.

A document is reprocessed when `processor_name` or `processor_version` differs
from the registered parser's current values — so bumping `HtmlParser.version`
makes exactly the stale documents eligible, and nothing else.

A document whose structure is flat produces **no** `document_sections` rows.
That is correct rather than a failure: its full structure lives in the
canonical JSON.

```bash
# only what is stale
docker compose run --rm app python -m services.processing

# everything, regardless of version
docker compose run --rm app python -m services.processing --force
```

## Running

```bash
docker compose run --rm app python -m services.processing
docker compose run --rm app python -m services.processing --limit 5
docker compose run --rm app python -m services.processing --document <uuid>
docker compose run --rm app python scripts/verify_processing.py
docker compose run --rm app python -m pytest tests/unit -q
```

## HTML parser notes

The parser knows about HTML constructs only - never about what kind of
document it is reading. Two generic behaviours, both driven by real-world HTML
and both covered by tests:

- **Typographic heading fallback.** Plenty of generated HTML carries no
  `<h1>`–`<h6>` and expresses headings purely through styling. When a document
  has no native heading tags, short fully-emphasized blocks become headings and
  distinct font sizes rank into levels. Native heading tags, when present, win
  outright and disable the fallback entirely.
- **Namespaced inline markup.** XML-namespaced elements embedded in HTML come
  in two kinds. Containers whose *local name* is `hidden`, `header`,
  `references`, `resources`, or `metadata` hold machine-readable data and are
  dropped. Any other prefixed element is unwrapped so the visible text it
  surrounds survives. The rule matches on structure, not on a known
  vocabulary, and is tested across several prefixes.

Tables drop rows and columns that are empty everywhere — HTML tables are
routinely used for layout with spacer cells — and expand `colspan` so column
alignment survives.

### Known limitation: heading levels are typographic, not semantic

Font-size ranking produces a hierarchy that is internally consistent but not
always semantically ideal. When a document's cover page uses the largest type,
body sections end up nested beneath it rather than at the top level. Sibling
relationships within the body are correct; absolute depth is not.

This is generic behaviour with a generic limitation. Mapping headings onto a
*known* document taxonomy would fix the depth, but it would make the parser
source-specific — exactly what this layer must not become. Such a mapping
belongs in a separate versioned component (`domain/<source>/`), where it can
be evaluated and re-run independently. It is deliberately not implemented.

### What this layer never does

No chunking, embeddings, vector indexing, retrieval, RAG, summarization, LLM
enrichment, semantic analysis, or domain-specific reasoning. Its outputs are a
canonical document and database state, nothing else.
