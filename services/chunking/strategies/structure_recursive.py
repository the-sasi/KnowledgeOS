"""Structure-aware recursive chunking.

Uses the canonical hierarchy to choose boundaries, and only falls back to
smaller ones when a unit does not fit the token budget:

    structural node (section)      largest meaningful unit
        -> child structural nodes
            -> content nodes, packed in document order
                -> paragraph / sentence boundaries
                    -> raw token split          last resort only

The document is never simply cut every N tokens. A section that fits becomes
one chunk, whole. A section that does not is opened up and its children are
packed in order, so chunk boundaries land on real structural seams.

Tables are treated as units. One that fits is never split; one that does not is
divided by *rows*, with the caption and header repeated on each part, so every
part is still a readable table rather than a fragment of prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from services.chunking.base import ChunkingStrategy, register_strategy
from services.chunking.models import ChunkingConfig, ChunkingResult, DocumentChunk
from services.chunking.tokenizer import Tokenizer, get_tokenizer
from services.processing.canonical import CanonicalDocument, Node, NodeType, Table

# Sentence boundary: terminator followed by whitespace and a capital/quote/digit.
# Deliberately conservative - splitting mid-sentence is a last resort.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


@dataclass
class _Pending:
    """Content nodes being packed into a chunk, with their lineage."""

    nodes: list[Node] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    section_node_id: str | None = None
    metadata: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.nodes


class StructureAwareRecursiveChunker(ChunkingStrategy):
    name = "structure_recursive"
    version = "1.0.0"

    def chunk(
        self, document: CanonicalDocument, config: ChunkingConfig
    ) -> ChunkingResult:
        tokenizer = get_tokenizer(config.tokenizer)
        state = _Run(tokenizer=tokenizer, config=config)

        for node in document.content:
            state.visit(node, ancestors=[], section_node_id=None)
        state.flush()

        chunks = state.finalize()

        return ChunkingResult(
            chunks=chunks,
            strategy=self.name,
            strategy_version=self.version,
            config=config,
        )


class _Run:
    """One chunking pass. Holds the packing buffer and emitted chunks."""

    def __init__(self, *, tokenizer: Tokenizer, config: ChunkingConfig) -> None:
        self.tok = tokenizer
        self.cfg = config
        self.pending = _Pending()
        self.emitted: list[DocumentChunk] = []
        self._token_cache: dict[int, int] = {}
        # Holds a reference to every memoized node; see node_tokens().
        self._cache_refs: list[Node] = []

    # -- measurement -------------------------------------------------------

    def node_tokens(self, node: Node) -> int:
        """Token count for a node's rendered subtree, memoized.

        The memo is keyed on `id(node)`, which is only safe while the node is
        alive: CPython recycles addresses, so a discarded temporary can hand
        its id to the next one and produce a false cache hit. Every cached
        node is therefore kept referenced. Temporary nodes built during
        splitting must use `count_nodes` instead.
        """
        key = id(node)
        hit = self._token_cache.get(key)
        if hit is not None:
            return hit

        value = self.tok.count(node.render_text())
        self._token_cache[key] = value
        self._cache_refs.append(node)
        return value

    def count_nodes(self, nodes: list[Node]) -> int:
        """Uncached token count. Use for nodes that do not outlive the call."""
        return self.tok.count(self._render(nodes))

    def fits(self, tokens: int) -> bool:
        return tokens <= self.cfg.max_tokens

    # -- traversal ---------------------------------------------------------

    def visit(
        self, node: Node, *, ancestors: list[str], section_node_id: str | None
    ) -> None:
        """Emit chunks for `node`, using structure before falling back."""
        tokens = self.node_tokens(node)

        # 1. Largest meaningful unit: the whole node fits.
        if self.fits(tokens):
            own_path = self._path_for(node, ancestors)
            own_section = node.id if node.type is NodeType.SECTION else section_node_id
            self.add(node, path=own_path, section_node_id=own_section)
            return

        # 2. Too large and it has children: open it up and pack them in order.
        if node.children:
            self.flush()
            child_path = self._path_for(node, ancestors)
            child_section = node.id if node.type is NodeType.SECTION else section_node_id

            for child in node.children:
                self.visit(child, ancestors=child_path, section_node_id=child_section)

            self.flush()
            return

        # 3. An oversized leaf. Split by its own internal boundaries.
        self.flush()
        path = self._path_for(node, ancestors)

        if node.type is NodeType.TABLE and node.table is not None:
            parts = self._split_table(node)
        else:
            parts = self._split_text_node(node)

        for part, meta in parts:
            self.emit([part], path=path, section_node_id=section_node_id, metadata=meta)

    def _path_for(self, node: Node, ancestors: list[str]) -> list[str]:
        """A chunk's path includes the title of the section it belongs to."""
        if node.type is NodeType.SECTION and node.text:
            return [*ancestors, node.text]
        return list(ancestors)

    # -- packing -----------------------------------------------------------

    def add(self, node: Node, *, path: list[str], section_node_id: str | None) -> None:
        """Append a node to the current chunk, flushing first if it would not fit.

        Nodes only pack together when they share a path: a chunk that spans a
        section boundary would have ambiguous lineage.
        """
        if not self.pending.is_empty() and self.pending.path != path:
            self.flush()

        prospective = self.pending.nodes + [node]
        if not self.pending.is_empty() and not self.fits(self.count_nodes(prospective)):
            self.flush()

        self.pending.nodes.append(node)
        self.pending.path = list(path)
        self.pending.section_node_id = section_node_id

    @staticmethod
    def _render(nodes: list[Node]) -> str:
        parts = [n.render_text() for n in nodes]
        return "\n\n".join(p for p in parts if p)

    def flush(self) -> None:
        if self.pending.is_empty():
            return
        self.emit(
            self.pending.nodes,
            path=self.pending.path,
            section_node_id=self.pending.section_node_id,
            metadata=dict(self.pending.metadata),
        )
        self.pending = _Pending()

    def emit(
        self,
        nodes: list[Node],
        *,
        path: list[str],
        section_node_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        text = self._render(nodes)
        if not text.strip():
            return

        meta = dict(metadata or {})
        meta.setdefault("node_types", [n.type.value for n in nodes])

        self.emitted.append(
            DocumentChunk(
                chunk_index=len(self.emitted),
                text=text,
                token_count=self.tok.count(text),
                nodes=list(nodes),
                node_ids=[n.id for n in nodes],
                path=list(path),
                section_node_id=section_node_id,
                metadata=meta,
            )
        )

    # -- oversized tables --------------------------------------------------

    def _split_table(self, node: Node) -> list[tuple[Node, dict]]:
        """Divide a too-large table by rows, repeating caption and header.

        Every part remains a real table with its own header, so column meaning
        survives. Splitting a table by token count instead would produce
        fragments in which numbers have lost their columns.
        """
        table = node.table
        assert table is not None

        header = list(table.header)
        caption = table.caption

        def part_node(rows: list[list[str]], index: int) -> Node:
            return Node(
                id=f"{node.id}#t{index}",
                type=NodeType.TABLE,
                ordinal=node.ordinal,
                table=Table(rows=rows, header=header, caption=caption),
                attributes=dict(node.attributes),
            )

        groups: list[list[list[str]]] = []
        current: list[list[str]] = []

        for row in table.rows:
            candidate = current + [row]
            if current and not self.fits(self.count_nodes([part_node(candidate, 0)])):
                groups.append(current)
                current = [row]
            else:
                current = candidate

        if current:
            groups.append(current)
        if not groups:
            groups = [[]]

        total = len(groups)
        out: list[tuple[Node, dict]] = []
        for i, rows in enumerate(groups):
            meta = {
                "table_split": total > 1,
                "table_part": i + 1,
                "table_parts": total,
                "source_node_id": node.id,
                "table_rows": len(rows),
            }
            out.append((part_node(rows, i + 1), meta))
        return out

    # -- oversized text ----------------------------------------------------

    def _split_text_node(self, node: Node) -> list[tuple[Node, dict]]:
        """Split an oversized text node at sentence boundaries, then tokens."""
        pieces = self._pack_sentences(node.text)

        total = len(pieces)
        out: list[tuple[Node, dict]] = []
        for i, (text, hard) in enumerate(pieces):
            part = Node(
                id=f"{node.id}#p{i + 1}",
                type=node.type,
                text=text,
                ordinal=node.ordinal,
                attributes=dict(node.attributes),
            )
            out.append(
                (
                    part,
                    {
                        "text_split": total > 1,
                        "text_part": i + 1,
                        "text_parts": total,
                        "source_node_id": node.id,
                        # True only when a single sentence exceeded the budget
                        # and had to be cut mid-sentence.
                        "hard_split": hard,
                    },
                )
            )
        return out

    def _pack_sentences(self, text: str) -> list[tuple[str, bool]]:
        sentences = [s for s in _SENTENCE_RE.split(text) if s.strip()] or [text]

        out: list[tuple[str, bool]] = []
        current = ""

        for sentence in sentences:
            candidate = f"{current} {sentence}".strip() if current else sentence

            if self.fits(self.tok.count(candidate)):
                current = candidate
                continue

            if current:
                out.append((current, False))
                current = ""

            if self.fits(self.tok.count(sentence)):
                current = sentence
                continue

            # Last resort: a single sentence larger than the budget.
            for piece in self._hard_split(sentence):
                out.append((piece, True))

        if current:
            out.append((current, False))
        return out or [(text, True)]

    def _hard_split(self, text: str) -> list[str]:
        tokens = self.tok.tokenize(text)
        size = self.cfg.max_tokens
        return [
            self.tok.detokenize(tokens[i : i + size]) for i in range(0, len(tokens), size)
        ]

    # -- finalization ------------------------------------------------------

    def finalize(self) -> list[DocumentChunk]:
        chunks = self._merge_small(self.emitted)
        chunks = self._apply_overlap(chunks)

        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
            chunk.metadata.setdefault("path_depth", len(chunk.path))

            # Anything still over budget could not be divided without
            # destroying a unit - a single table row, or a sentence longer than
            # the budget. Flag it rather than pretend the budget held, so
            # verification can report it and a future table-specific strategy
            # has something to target.
            if chunk.token_count > self.cfg.max_tokens:
                chunk.metadata["oversized"] = True
                chunk.metadata["over_budget_by"] = (
                    chunk.token_count - self.cfg.max_tokens
                )
        return chunks

    @staticmethod
    def _is_heading_only(chunk: DocumentChunk) -> bool:
        """A chunk that is nothing but section titles carries no content.

        These arise wherever a heading has no body of its own - the following
        content sits in sibling sections rather than beneath it. Standing
        alone they are retrieval noise; merged forward they become a useful
        prefix for the content that follows.
        """
        return bool(chunk.nodes) and all(
            n.type is NodeType.SECTION and not n.children for n in chunk.nodes
        )

    @staticmethod
    def _shares_parent(left: list[str], right: list[str]) -> bool:
        return left[:-1] == right[:-1]

    def _may_merge(self, previous: DocumentChunk, chunk: DocumentChunk) -> bool:
        if not self.fits(previous.token_count + chunk.token_count):
            return False
        # Never fold a split part back together - it was split for a reason and
        # its part metadata would become wrong.
        for c in (previous, chunk):
            if c.metadata.get("table_split") or c.metadata.get("text_split"):
                return False
        if previous.token_count >= self.cfg.min_tokens:
            # Only an undersized chunk pulls its neighbour in.
            return False
        if previous.path == chunk.path:
            return True
        # A bare heading absorbs into the content that follows it, provided
        # they sit under the same parent section.
        return self._is_heading_only(previous) and self._shares_parent(
            previous.path, chunk.path
        )

    def _merge_small(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        """Merge undersized chunks into a neighbour.

        Cover pages and bare headings otherwise produce a long tail of
        near-empty chunks that carry no retrievable meaning.
        """
        if self.cfg.min_tokens <= 0 or not chunks:
            return chunks

        out: list[DocumentChunk] = []
        for chunk in chunks:
            if not out or not self._may_merge(out[-1], chunk):
                out.append(chunk)
                continue

            previous = out[-1]
            merged_nodes = previous.nodes + chunk.nodes
            text = self._render(merged_nodes)

            # When a heading merges forward, the chunk belongs where its
            # content lives; the heading survives as leading text.
            crossed = previous.path != chunk.path
            path = chunk.path if crossed else previous.path
            section_node_id = (
                chunk.section_node_id if crossed else previous.section_node_id
            )

            metadata = {
                **previous.metadata,
                **chunk.metadata,
                "merged": True,
                "node_types": [n.type.value for n in merged_nodes],
            }
            if crossed:
                metadata["merged_heading"] = previous.text

            out[-1] = DocumentChunk(
                chunk_index=previous.chunk_index,
                text=text,
                token_count=self.tok.count(text),
                nodes=merged_nodes,
                node_ids=[n.id for n in merged_nodes],
                path=path,
                section_node_id=section_node_id,
                metadata=metadata,
            )
        return out

    def _apply_overlap(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        """Prepend the tail of each chunk to the next one.

        Off by default. Overlap duplicates content and inflates storage, and
        whether it improves retrieval has to be measured rather than assumed.
        """
        overlap = self.cfg.overlap_tokens
        if overlap <= 0 or len(chunks) < 2:
            if self.cfg.include_path_prefix:
                for chunk in chunks:
                    self._prefix_path(chunk)
            return chunks

        for i in range(1, len(chunks)):
            tail = self.tok.tail(chunks[i - 1].text, overlap)
            if not tail.strip():
                continue
            chunks[i].text = f"{tail}\n\n{chunks[i].text}"
            chunks[i].token_count = self.tok.count(chunks[i].text)
            chunks[i].metadata["overlap_tokens"] = overlap
            chunks[i].metadata["overlap_from"] = chunks[i - 1].chunk_index

        if self.cfg.include_path_prefix:
            for chunk in chunks:
                self._prefix_path(chunk)
        return chunks

    def _prefix_path(self, chunk: DocumentChunk) -> None:
        if not chunk.path:
            return
        chunk.text = " > ".join(chunk.path) + "\n\n" + chunk.text
        chunk.token_count = self.tok.count(chunk.text)
        chunk.metadata["path_prefixed"] = True


register_strategy(StructureAwareRecursiveChunker())
