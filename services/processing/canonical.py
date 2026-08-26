"""Canonical document representation.

The format-independent shape every parser produces. Downstream stages read
this, never the original HTML/PDF/DOCX, so a new source format changes nothing
downstream.

A document is a tree of generic **nodes**. There is no privileged "section"
concept: a section is one node type among several, used when a document
actually has sections. Documents whose structure is flat produce flat content.

    Document                 Document                  Document
    |-- section              |-- heading               |-- section "Header"
    |   |-- heading          |-- paragraph             |-- section "Customer"
    |   |-- paragraph        |-- code                  |-- table
    |   `-- section          `-- table                 `-- section "Totals"
    `-- section
    (SEC filing)             (technical doc)           (invoice)

The same model carries all three. Parsers choose the shape that matches the
document in front of them.

Deliberately plain dataclasses with explicit to_dict/from_dict rather than
pydantic: the schema is small and produced only by our own parsers.
`SCHEMA_VERSION` is what protects readers when the shape changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterator

#: Bumped when the canonical shape changes. 2.0 replaced the mandatory
#: section tree of 1.0 with generic nodes.
SCHEMA_VERSION = "2.0"


class NodeType(str, Enum):
    """Kinds of structural and content node.

    Structural nodes group other nodes; content nodes carry text or data.
    Kept small on purpose: a parser that cannot classify something should emit
    PARAGRAPH rather than invent a type.
    """

    # Structural
    SECTION = "section"
    LIST = "list"
    FIGURE = "figure"
    CONTAINER = "container"

    # Content
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"
    QUOTE = "quote"
    CAPTION = "caption"


#: Node types that group other nodes rather than carrying their own content.
STRUCTURAL_TYPES = frozenset(
    {NodeType.SECTION, NodeType.LIST, NodeType.FIGURE, NodeType.CONTAINER}
)


@dataclass
class Table:
    """A table kept as rows and columns, not flattened into prose."""

    rows: list[list[str]] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    caption: str | None = None

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        widths = [len(r) for r in self.rows] + ([len(self.header)] if self.header else [])
        return max(widths) if widths else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption": self.caption,
            "header": self.header,
            "rows": self.rows,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Table":
        return cls(
            rows=[list(r) for r in data.get("rows", [])],
            header=list(data.get("header", [])),
            caption=data.get("caption"),
        )


@dataclass
class Node:
    """One structural or content node.

    A single node class rather than a class per type: the tree is walked
    generically far more often than any one type is handled specially, and one
    class keeps serialization trivial.

    `attributes` carries type-specific facts that are not worth a field on
    every node - `level` for headings and sections, `language` for code.
    """

    id: str
    type: NodeType
    text: str = ""
    ordinal: int = 0
    children: list["Node"] = field(default_factory=list)
    table: Table | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- convenience -------------------------------------------------------

    @property
    def is_structural(self) -> bool:
        return self.type in STRUCTURAL_TYPES

    @property
    def level(self) -> int | None:
        value = self.attributes.get("level")
        return int(value) if value is not None else None

    @property
    def title(self) -> str | None:
        """Heading text for a section, or the node's own text for a heading."""
        if self.type in (NodeType.SECTION, NodeType.HEADING):
            return self.text or None
        return None

    def list_items(self) -> list[str]:
        """Text of direct LIST_ITEM children. Empty for non-list nodes."""
        return [c.text for c in self.children if c.type is NodeType.LIST_ITEM]

    # -- traversal ---------------------------------------------------------

    def walk(self) -> Iterator["Node"]:
        """Depth-first, self first, document order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, *types: NodeType) -> list["Node"]:
        return [n for n in self.walk() if n.type in types]

    def text_length(self) -> int:
        """Characters of content carried by this node alone."""
        if self.type is NodeType.TABLE and self.table is not None:
            return sum(len(c) for row in self.table.rows for c in row) + sum(
                len(c) for c in self.table.header
            )
        return len(self.text)

    # -- derived text view -------------------------------------------------

    def render_text(self) -> str:
        """Readable plain text for this subtree.

        A *derived view*, never the stored form. Chunking will read the nodes.
        """
        parts: list[str] = []

        if self.type is NodeType.TABLE and self.table is not None:
            if self.table.caption:
                parts.append(self.table.caption)
            if self.table.header:
                parts.append(" | ".join(self.table.header))
            parts.extend(" | ".join(r) for r in self.table.rows)
        elif self.type is NodeType.LIST_ITEM:
            if self.text:
                parts.append(f"- {self.text}")
        elif self.text:
            parts.append(self.text)

        for child in self.children:
            rendered = child.render_text()
            if rendered:
                parts.append(rendered)

        return "\n\n".join(parts)

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type.value,
            "ordinal": self.ordinal,
        }
        if self.text:
            out["text"] = self.text
        if self.table is not None:
            out["table"] = self.table.to_dict()
        if self.attributes:
            out["attributes"] = self.attributes
        if self.metadata:
            out["metadata"] = self.metadata
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            text=data.get("text", ""),
            ordinal=data.get("ordinal", 0),
            children=[Node.from_dict(c) for c in data.get("children", [])],
            table=Table.from_dict(data["table"]) if data.get("table") else None,
            attributes=dict(data.get("attributes", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class SourceInfo:
    """Where the bytes came from. The raw file itself is never modified."""

    path: str
    file_name: str
    doc_format: str
    media_type: str | None = None
    byte_size: int | None = None
    checksum_sha256: str | None = None
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceInfo":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


@dataclass
class ProcessorInfo:
    """Which processor produced this document, and when.

    `version` is what makes reprocessing decidable: if the stored version is
    behind the parser's current version, the document is stale.
    """

    name: str
    version: str
    processed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessorInfo":
        return cls(
            name=data["name"],
            version=data["version"],
            processed_at=data.get("processed_at"),
        )


@dataclass
class CanonicalDocument:
    """A parsed document in format-independent form."""

    source: SourceInfo
    processor: ProcessorInfo
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Root-level nodes, in document order. May be sections, or flat content,
    #: or a mix - whatever matches the source document.
    content: list[Node] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    # -- traversal ---------------------------------------------------------

    def walk(self) -> Iterator[Node]:
        for node in self.content:
            yield from node.walk()

    def find(self, *types: NodeType) -> list[Node]:
        return [n for n in self.walk() if n.type in types]

    def iter_with_parents(self) -> list[tuple[Node, Node | None]]:
        """Depth-first (node, parent) pairs, parents always before children.

        Persisting the tree needs the parent link, which Node does not store:
        keeping it free of back-references makes it trivially serializable.
        """
        out: list[tuple[Node, Node | None]] = []

        def visit(node: Node, parent: Node | None) -> None:
            out.append((node, parent))
            for child in node.children:
                visit(child, node)

        for root in self.content:
            visit(root, None)
        return out

    def sections(self) -> list[Node]:
        """SECTION nodes, if this document has any.

        A convenience for documents that are organized into sections, not an
        assumption that every document is.
        """
        return self.find(NodeType.SECTION)

    def headings(self) -> list[Node]:
        return self.find(NodeType.HEADING)

    def tables(self) -> list[Table]:
        return [n.table for n in self.find(NodeType.TABLE) if n.table is not None]

    def has_sections(self) -> bool:
        return any(n.type is NodeType.SECTION for n in self.walk())

    # -- derived text view -------------------------------------------------

    def text(self) -> str:
        """Plain text derived from the node tree. Never the stored form."""
        return "\n\n".join(n.render_text() for n in self.content if n.render_text())

    # -- stats -------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        nodes = list(self.walk())
        counts = {t.value: 0 for t in NodeType}
        for node in nodes:
            counts[node.type.value] += 1

        depths: list[int] = []

        def depth_of(node: Node, depth: int) -> None:
            depths.append(depth)
            for child in node.children:
                depth_of(child, depth + 1)

        for root in self.content:
            depth_of(root, 1)

        return {
            "nodes": len(nodes),
            **counts,
            "text_length": sum(n.text_length() for n in nodes),
            "max_depth": max(depths, default=0),
        }

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "processor": self.processor.to_dict(),
            "metadata": self.metadata,
            "stats": self.stats(),
            "content": [n.to_dict() for n in self.content],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalDocument":
        return cls(
            source=SourceInfo.from_dict(data["source"]),
            processor=ProcessorInfo.from_dict(data["processor"]),
            metadata=dict(data.get("metadata", {})),
            content=[Node.from_dict(n) for n in data.get("content", [])],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )
