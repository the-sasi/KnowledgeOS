# ADR 0005: Structure-aware chunking, versioned per run

- **Status:** accepted
- **Date:** 2026-08-26

## Context

Retrieval works over chunks, not documents. How a document is divided decides
what can be retrieved at all: a boundary in the wrong place separates a claim
from its evidence, and a chunk spanning two topics dilutes both.

Chunking is also the stage most worth experimenting with. Chunk size, overlap,
and strategy interact with the embedding model and the retriever, none of which
are chosen yet. Whatever is built now will be re-run and compared later, so the
architecture matters more than the first strategy's parameters.

The canonical layer already provides a format-agnostic node tree with real
hierarchy. That structure is the obvious signal for where to cut, and ignoring
it in favour of fixed-size splitting would throw away the work the processing
stage did.

## Decisions

### Structure first, smaller boundaries only on overflow

`structure_recursive` v1.0.0 tries the largest meaningful unit that fits — a
whole section — and only opens it up when it exceeds the budget, then packs
children in document order, then falls back to paragraph, sentence, and finally
raw token boundaries.

### Chunks never span a section boundary

Nodes pack together only when they share a path.

### Tables split by rows, with the header repeated

A table that fits is never split. One that does not is divided by rows, each
part carrying the caption and header. Parts record `source_node_id` and
`table_part`.

### Chunks store canonical nodes as well as text

`content` holds rendered text for a future embedding step; `content_nodes`
holds the canonical nodes.

### A chunk set is identified by (document, strategy, version, config)

`config_hash` is a stable hash of the configuration. Re-running the same key is
a no-op; changing anything produces a separate set.

### A tokenizer interface, with a heuristic default

`Tokenizer` is an interface. The default `simple` implementation is a regex
word/punctuation splitter with no model dependency.

### One transaction per document

Deleting the previous set, inserting all chunks, and completing the job happen
together.

## Tradeoffs

**Structure-aware vs fixed-size.** Fixed-size chunking is trivial, uniform, and
predictable — every chunk is exactly N tokens, which makes cost modelling easy.
Structure-aware chunking produces a wide size distribution (observed: 1 to 512
tokens, mean 262) and more code. It is chosen because the boundaries mean
something: a chunk is a section or a run of paragraphs within one, and its path
is real. The uniformity that fixed-size buys is worth little if the boundaries
land mid-argument. Fixed-size remains worth implementing later precisely so the
two can be measured against each other, which is why the registry exists.

**Never spanning sections vs packing to the budget.** Refusing to cross section
boundaries leaves chunks smaller than they could be — the size histogram has a
large bucket under 53 tokens, mostly short sections. Packing across boundaries
would fill the budget better but give the chunk an ambiguous path, and path is
what makes filtered retrieval possible later. Small chunks are cheap; wrong
lineage is not. `min_tokens` merging recovers some of the loss without crossing
a boundary.

**Row-splitting tables vs keeping them whole.** Keeping an oversized table whole
guarantees an over-budget chunk; splitting it risks separating a number from its
header. Repeating the header on each part is the compromise: parts stay
readable, at the cost of duplicated header tokens and a table that must be
reassembled from `source_node_id` to be seen whole. Rows themselves are never
split — a row larger than the budget stays oversized and is flagged.

**Storing canonical nodes alongside text.** This roughly doubles the row size
for table-heavy chunks. Text alone would be smaller and is all an embedding
model needs. But a chunk is also the unit a future answer cites, and citing a
table as prose loses the thing that made it evidence. The duplication buys
reconstructability.

**Config-hash identity vs overwriting.** Keying chunk sets by configuration
means the table grows with every experiment — three configurations over 45
documents already produced ~13,500 rows. Overwriting would keep it small and
make comparison impossible. Experiments have to be reproducible, so growth is
accepted; stale sets can be deleted explicitly.

**A heuristic tokenizer now vs waiting for the model.** `simple` under-counts
against BPE by roughly 25-35%, so a nominal 512-token chunk is genuinely larger.
Depending on tiktoken now would pin the project to one vendor's tokenizer before
the embedding model is chosen. The interface keeps the decision open, and
`tokenizer` is recorded on every chunk so counts stay interpretable — but any
size benchmark must be re-run once a real tokenizer is in place.

**One transaction per document vs batching.** Per-document transactions mean 45
round trips instead of one. Batching would be faster but a mid-batch failure
would leave an indeterminate set of documents chunked. Correctness wins at this
volume; batching is revisitable if the corpus grows by orders of magnitude.

## Consequences

- Chunk sizes are uneven by design. Any consumer assuming uniform chunks is
  making an assumption this stage does not support.
- The `document_chunks` table grows per experiment, not per corpus.
- Chunk quality is inspectable before embeddings exist
  (`scripts/verify_chunking.py --chunk N`), which is the point of doing this
  stage on its own.
- Section identity still changes on reprocessing (ADR 0004), so chunks
  reference `section_node_id` as a canonical id and `section_id` as a FK that
  is only valid until the document is reprocessed.

## Revisit when

- A real tokenizer is available — every size decision here needs re-measuring.
- Retrieval evaluation exists and can compare strategies on quality rather than
  on inspection.
- Oversized rows or very large tables become common enough to justify a
  table-specific strategy.
