"""Chunk model and chunking configuration.

A chunk is a slice of a canonical document that carries its lineage: which
canonical nodes it came from, where those sit in the document hierarchy, and
which strategy/version/config produced it.

The chunk keeps **both** representations:

* `text`  - rendered, ready for a future embedding step.
* `nodes` - the canonical nodes themselves, so a table stays a table and the
            chunk can be traced back to exact positions in the document.

Storing only text would make tables unreconstructable, which the canonical
layer went to some trouble to preserve.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from services.processing.canonical import Node, NodeType


@dataclass(frozen=True)
class ChunkingConfig:
    """Knobs a chunking run is parameterized by.

    Frozen and fingerprinted: the fingerprint is what makes two runs with
    different settings distinguishable chunk sets rather than a silent
    overwrite. No value here is "the correct" one - they exist to be
    benchmarked.
    """

    max_tokens: int = 512
    #: Chunks smaller than this are merged with an adjacent sibling where the
    #: budget allows. 0 disables merging.
    min_tokens: int = 64
    #: 0 is valid and is the default. Overlap costs storage and duplicates
    #: content; whether it earns that has to be measured.
    overlap_tokens: int = 0
    tokenizer: str = "simple"
    #: Prepend the section path to the chunk text (a contextual-retrieval
    #: technique). Off by default so stored text stays faithful to the source;
    #: the path is always available in metadata regardless.
    include_path_prefix: bool = False

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must not be negative")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")
        if self.min_tokens < 0:
            raise ValueError("min_tokens must not be negative")
        if self.min_tokens > self.max_tokens:
            raise ValueError("min_tokens must not exceed max_tokens")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "min_tokens": self.min_tokens,
            "overlap_tokens": self.overlap_tokens,
            "tokenizer": self.tokenizer,
            "include_path_prefix": self.include_path_prefix,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChunkingConfig":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def fingerprint(self) -> str:
        """Stable short hash of the configuration.

        Part of the chunk-set identity, so changing `max_tokens` produces a
        new set rather than overwriting the old one.
        """
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class DocumentChunk:
    """One chunk, with the lineage needed to trace it home.

        chunk -> canonical node(s) -> document -> filing -> company
    """

    chunk_index: int
    text: str
    token_count: int

    #: Canonical nodes this chunk is made of, in document order.
    nodes: list[Node] = field(default_factory=list)
    #: Their ids, denormalized for querying without parsing the JSON.
    node_ids: list[str] = field(default_factory=list)
    #: Section titles from the document root down to this chunk, e.g.
    #: ["Part II", "Item 7", "Management's Discussion and Analysis"].
    path: list[str] = field(default_factory=list)
    #: Canonical id of the innermost enclosing SECTION node, when there is one.
    section_node_id: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def node_types(self) -> list[str]:
        """Types of the top-level nodes, describing how the chunk is composed."""
        return [n.type.value for n in self.nodes]

    def walk(self):
        """Every node in the chunk, including nested ones.

        A chunk often holds a whole section, so its tables and paragraphs are
        descendants rather than top-level entries.
        """
        for node in self.nodes:
            yield from node.walk()

    def tables(self) -> list[Node]:
        return [n for n in self.walk() if n.type is NodeType.TABLE]

    def has_table(self) -> bool:
        return any(n.type is NodeType.TABLE for n in self.walk())

    def nodes_payload(self) -> list[dict[str, Any]]:
        """Canonical nodes as JSON, so tables survive persistence intact."""
        return [n.to_dict() for n in self.nodes]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "text": self.text,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "node_ids": self.node_ids,
            "node_types": self.node_types(),
            "path": self.path,
            "section_node_id": self.section_node_id,
            "metadata": self.metadata,
            "nodes": self.nodes_payload(),
        }


@dataclass
class ChunkingResult:
    """Everything one chunking run produced for one document."""

    chunks: list[DocumentChunk]
    strategy: str
    strategy_version: str
    config: ChunkingConfig

    @property
    def config_hash(self) -> str:
        return self.config.fingerprint()

    def stats(self) -> dict[str, Any]:
        if not self.chunks:
            return {"chunks": 0, "tokens_total": 0}
        counts = [c.token_count for c in self.chunks]
        return {
            "chunks": len(self.chunks),
            "tokens_total": sum(counts),
            "tokens_min": min(counts),
            "tokens_max": max(counts),
            "tokens_avg": round(sum(counts) / len(counts), 1),
            "with_tables": sum(1 for c in self.chunks if c.has_table()),
            "over_budget": sum(
                1 for c in self.chunks if c.token_count > self.config.max_tokens
            ),
        }
