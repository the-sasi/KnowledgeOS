# ADR 0004: Generic node model replaces the mandatory section tree

- **Status:** accepted
- **Date:** 2026-08-25
- **Amends:** [ADR 0003](0003-canonical-document-representation.md)

## Context

ADR 0003 defined `CanonicalDocument` as a list of `Section`s, each holding
typed `Block`s. Validating it against the first corpus worked, but the shape
encoded an assumption that does not generalize: **every document has
sections**.

That is false for a large share of the formats KnowledgeOS is meant to ingest.
A technical page is often a flat run of headings, paragraphs, code, and tables
with no sectioning at all. An invoice is a header, a party block, a line-item
table, and totals. Forcing those into a section tree means inventing synthetic
sections that carry no meaning, and every downstream consumer then has to know
which sections are real and which are scaffolding.

The processing layer is the point where format-specific detail is supposed to
disappear. Baking one document shape into the canonical model would push that
detail downstream instead of removing it.

## Decisions

### One generic `Node` type, with `SECTION` as one type among several

`CanonicalDocument.sections: list[Section]` became
`CanonicalDocument.content: list[Node]`. A `Node` has a `type`, optional
`text`, optional `table` payload, `children`, and `attributes`.

`NodeType` covers structural kinds (`SECTION`, `LIST`, `FIGURE`, `CONTAINER`)
and content kinds (`HEADING`, `PARAGRAPH`, `LIST_ITEM`, `TABLE`, `CODE`,
`QUOTE`, `CAPTION`).

The same model now carries all three shapes:

```
Document              Document              Document
|-- section           |-- heading           |-- section "Header"
|   |-- paragraph     |-- paragraph         |-- section "Customer"
|   `-- section       |-- code              |-- table
`-- section           `-- table             `-- section "Totals"
(sectioned filing)    (technical page)      (invoice)
```

### Parsers emit the shape the document has

The HTML parser groups content into `SECTION` nodes when the document has
headings, and emits flat content nodes when it does not. Content appearing
before the first heading stays at the root rather than being forced into a
synthetic wrapper.

### Namespace handling generalized

Handling of XML-namespaced markup embedded in HTML is now a structural rule
rather than a list of known vocabularies. Any prefixed element whose local
name is `hidden`, `header`, `references`, `resources`, or `metadata` is a
metadata container and is dropped; any other prefixed element is unwrapped so
the visible text it surrounds survives. The rule is tested against several
prefixes, not one.

### `document_sections` keeps its name, gains `node_type`

The table records structural nodes and now stores which node type each row
came from. Documents with no structural nodes legitimately produce no rows.

### Versions bumped

`SCHEMA_VERSION` 1.0 -> 2.0, `HtmlParser.version` 1.0.0 -> 2.0.0. The existing
version-comparison logic made all 45 stored documents stale and reprocessed
them without any special migration path — which is the versioning mechanism
doing exactly its job.

## Tradeoffs

**One `Node` class vs a class per node type.** Subclasses would make invalid
states unrepresentable — a paragraph cannot accidentally carry a table. But
the tree is walked generically far more often than any single type is handled
specially, and polymorphic deserialization needs a type registry anyway. One
class with a `type` discriminator keeps `to_dict`/`from_dict` trivial and the
walk uniform. The cost is that nothing structurally prevents a nonsensical
combination; parsers are the guard, and they are tested.

**`attributes` dict vs typed fields.** `level` and `language` are type-specific
and would be dead weight on every node. A dict avoids that at the cost of
losing type-checking on those values, so `Node.level` and `Node.title` exist as
typed accessors over the common ones.

**Generic node model vs the section model it replaces.** The section model was
easier to consume: `document.sections()` was the whole API. The node model
requires callers to think about what they are traversing. That is the honest
tradeoff — the previous simplicity came from an assumption that was going to
break on the second format, and breaking it later would have meant changing
every consumer instead of one.

**Flat output vs always wrapping in a root section.** A synthetic root would
make every document uniform and every consumer simpler. It would also be a
lie about the document's structure, and consumers would eventually need to
distinguish real sections from scaffolding anyway. `has_sections()` makes the
distinction explicit and cheap instead.

**Namespace rules by local name vs by known vocabulary.** Matching local names
like `hidden` across any prefix is generic but heuristic: a vocabulary that
uses `<foo:metadata>` for visible content would lose it. Enumerating known
vocabularies would be precise but would make a generic parser carry a registry
of specific standards. The generic rule is the right default here, and the
limitation is documented.

## Consequences

- Adding PDF, DOCX, Markdown, or CSV requires no change to the canonical
  model. A CSV parser emits a single `TABLE` node; a Markdown parser emits
  sections or flat content depending on the file.
- Downstream stages must handle documents with and without sections.
  `has_sections()` and `find(NodeType...)` are the intended entry points.
- Canonical JSON from schema 1.0 cannot be read by 2.0 code. Nothing consumes
  it yet, and reprocessing regenerates everything, so no converter was written.
- Domain interpretation stays out of the parser. Mapping headings onto a
  known taxonomy remains a separate future component with its own version.

## Revisit when

- A second parser lands and reveals a structural kind the node types cannot
  express, or
- downstream consumers accumulate repeated tree-shape checks, which would
  suggest the model needs a richer query surface rather than a different shape.
