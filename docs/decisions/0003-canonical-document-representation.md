# ADR 0003: Canonical document representation and processing layer

- **Status:** accepted
- **Date:** 2026-08-25

## Context

Every stage after ingestion — chunking, embedding, retrieval — needs document
content. If each of them reads the original HTML/PDF/DOCX, then every new
source format changes every downstream stage, and chunking experiments become
entangled with parsing quirks.

The pipeline therefore needs one format-independent representation, produced
once per document, that everything downstream reads instead of the source.

Two constraints shaped the decisions: raw files must stay immutable, and
processing must be separable from chunking so the two can be versioned and
evaluated independently.

## Decisions

### A canonical document of nested sections and typed blocks

`CanonicalDocument` holds `SourceInfo`, `ProcessorInfo`, `metadata`, and a
tree of `Section`s. Each section carries typed `Block`s — paragraph, list,
table, heading — and its own `subsections`.

Structure is preserved rather than flattened. Tables keep `header` and
`rows`; lists keep `items`. A `text()` helper exists but is a view, never the
stored form.

### Canonical output as JSON files, state in PostgreSQL

Canonical documents are written to `data/processed/`, mirroring the raw layout.
PostgreSQL stores the *path*, the processor identity, and the section
hierarchy — not the content.

### Processor identity is versioned

Each parser declares `name` and `version`, both recorded on the document. A
document is reprocessed when either differs from the registered parser's
current values.

### Format detection is content-first

Content sniffing is trusted ahead of file extension, which is ahead of
mimetypes. Unrecognised input yields UNKNOWN rather than a guess.

### Typography-based heading detection for HTML

When a document has no `<h1>`–`<h6>` tags, headings are inferred from
emphasis and font size.

## Tradeoffs

**Nested sections vs a flat list with parent pointers.** Nesting makes the
hierarchy self-evident and serializes naturally; the cost is that walking to a
specific section requires recursion, and the tree carries no back-references
(hence `iter_with_parents()` for persistence). A flat list would be easier to
query in SQL but would push tree reconstruction onto every consumer. The
database gets the flat form, the JSON keeps the tree — each shaped for how it
is read.

**Dataclasses vs pydantic.** Pydantic would give validation and schema
generation. The representation is small and produced only by our own parsers,
so the validation would mostly restate the type hints. `SCHEMA_VERSION` covers
the real risk, which is readers meeting an older shape. Revisit if canonical
documents ever arrive from outside this codebase.

**JSON files vs a JSONB column.** A 10-K canonicalises to roughly 700 KB. Kept
in a column, every status query risks dragging that along, and comparing two
processor versions means dumping rows instead of diffing files. Kept on disk,
PostgreSQL stays the source of truth for *state* while bulk content sits where
raw files already are. The cost is a second place that can drift from the
database; the deterministic path and the `processed_path` unique index keep
that manageable. A `documents.processed_path` pointing at a missing file is
the failure mode to watch.

**Typographic heading levels vs semantic ones.** Font-size ranking is generic
and needs no per-source rules, but it produces depths that are internally
consistent rather than semantically ideal: on a 10-K cover page the company
name is the largest text, so body sections nest beneath it. Encoding SEC
section names (`Item 1A`, `Part II`) into the HTML parser would fix the depth
but would make a generic parser source-specific. That mapping belongs in its
own stage, where it can be versioned and evaluated on its own.

**Replacing section rows wholesale vs merging.** Reprocessing deletes and
reinserts a document's sections. Diffing two hierarchies to update in place
would preserve section UUIDs across runs, which will matter once chunks
reference sections. Today nothing references them, so the simple approach
wins; when chunking lands, stable section identity is the thing to revisit.

## Consequences

- Downstream stages depend only on `canonical.py`. Adding PDF or DOCX support
  means a new parser and a registry entry — no change to ingestion, the
  pipeline, or anything downstream.
- Processing and chunking are cleanly separable: chunking will read canonical
  documents and can be re-run without re-parsing HTML.
- `data/processed/` becomes real data that must be regenerable. It is
  git-ignored and rebuildable with `--force`.
- Two processor versions can be compared by diffing their JSON output.
- Section UUIDs change on every reprocess.

## Revisit when

- Canonical documents start arriving from outside this codebase (validation
  becomes worth its weight), or
- chunks begin referencing `document_sections` rows (section identity must
  survive reprocessing), or
- semantic section mapping is needed for SEC-aware retrieval.
