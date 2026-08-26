"""Unit tests for the generic canonical document representation.

The central property under test: the model carries documents with very
different structures - sectioned, flat, and mixed - without treating any one
shape as mandatory.
"""

from __future__ import annotations

import json

import pytest

from services.processing.canonical import (
    SCHEMA_VERSION,
    STRUCTURAL_TYPES,
    CanonicalDocument,
    Node,
    NodeType,
    ProcessorInfo,
    SourceInfo,
    Table,
)


def node(type_: NodeType, text: str = "", **kw) -> Node:
    kw.setdefault("id", f"n-{text[:8] or type_.value}")
    return Node(type=type_, text=text, **kw)


def document(*content: Node, metadata: dict | None = None) -> CanonicalDocument:
    return CanonicalDocument(
        source=SourceInfo(path="p", file_name="f", doc_format="HTML"),
        processor=ProcessorInfo(name="html", version="2.0.0"),
        metadata=metadata or {},
        content=list(content),
    )


@pytest.fixture
def sectioned() -> CanonicalDocument:
    """A document organized into nested sections, e.g. a regulatory filing."""
    table = Table(
        header=["Year", "Revenue"],
        rows=[["2024", "391,035"]],
        caption="Net sales",
    )
    inner = node(
        NodeType.SECTION,
        "Subsection",
        attributes={"level": 2, "path": ["Part I"]},
        children=[
            node(NodeType.PARAGRAPH, "Inner prose."),
            node(NodeType.TABLE, table=table),
        ],
    )
    return document(
        node(
            NodeType.SECTION,
            "Part I",
            attributes={"level": 1, "path": []},
            children=[node(NodeType.PARAGRAPH, "Outer prose."), inner],
        )
    )


@pytest.fixture
def flat() -> CanonicalDocument:
    """A document with no sections at all, e.g. a technical page."""
    return document(
        node(NodeType.HEADING, "Install", attributes={"level": 1}),
        node(NodeType.PARAGRAPH, "Run the following."),
        node(NodeType.CODE, "pip install x", attributes={"language": "bash"}),
        node(NodeType.TABLE, table=Table(header=["Flag"], rows=[["--fast"]])),
    )


class TestStructureIsNotMandatory:
    """Requirement: 'section' is one node type, not an assumption."""

    def test_flat_document_has_no_sections(self, flat):
        assert flat.has_sections() is False
        assert flat.sections() == []

    def test_flat_document_keeps_all_content_at_root(self, flat):
        assert [n.type for n in flat.content] == [
            NodeType.HEADING,
            NodeType.PARAGRAPH,
            NodeType.CODE,
            NodeType.TABLE,
        ]

    def test_sectioned_document_has_sections(self, sectioned):
        assert sectioned.has_sections() is True
        assert [s.text for s in sectioned.sections()] == ["Part I", "Subsection"]

    def test_both_shapes_share_one_model(self, flat, sectioned):
        for doc in (flat, sectioned):
            assert isinstance(doc, CanonicalDocument)
            assert doc.stats()["nodes"] > 0
            assert doc.text()

    def test_mixed_document_is_representable(self):
        """Preamble content before the first section, as real documents have."""
        doc = document(
            node(NodeType.PARAGRAPH, "Cover page."),
            node(NodeType.SECTION, "Body", attributes={"level": 1},
                 children=[node(NodeType.PARAGRAPH, "Inside.")]),
        )
        assert [n.type for n in doc.content] == [NodeType.PARAGRAPH, NodeType.SECTION]
        assert doc.has_sections() is True

    def test_document_of_only_a_table_is_valid(self):
        doc = document(node(NodeType.TABLE, table=Table(rows=[["a"]])))
        assert doc.has_sections() is False
        assert len(doc.tables()) == 1


class TestNodeTypes:
    def test_all_required_content_types_exist(self):
        for name in (
            "HEADING", "PARAGRAPH", "LIST", "LIST_ITEM",
            "TABLE", "CODE", "QUOTE", "CAPTION",
        ):
            assert hasattr(NodeType, name)

    def test_structural_types_are_marked(self):
        assert node(NodeType.SECTION).is_structural
        assert node(NodeType.LIST).is_structural
        assert not node(NodeType.PARAGRAPH).is_structural
        assert NodeType.SECTION in STRUCTURAL_TYPES

    def test_list_items_are_child_nodes_not_a_flat_field(self):
        """Child nodes rather than a string list, so nesting stays possible."""
        lst = node(
            NodeType.LIST,
            children=[
                node(NodeType.LIST_ITEM, "One"),
                node(NodeType.LIST_ITEM, "Two"),
            ],
        )
        assert lst.list_items() == ["One", "Two"]
        assert all(c.type is NodeType.LIST_ITEM for c in lst.children)

    def test_code_node_carries_language(self, flat):
        code = flat.find(NodeType.CODE)[0]
        assert code.attributes["language"] == "bash"

    def test_heading_and_section_expose_a_title(self):
        assert node(NodeType.HEADING, "H").title == "H"
        assert node(NodeType.SECTION, "S").title == "S"
        assert node(NodeType.PARAGRAPH, "P").title is None

    def test_level_accessor(self):
        assert node(NodeType.SECTION, attributes={"level": 3}).level == 3
        assert node(NodeType.PARAGRAPH).level is None


class TestTraversal:
    def test_walk_is_depth_first(self, sectioned):
        assert [n.type.value for n in sectioned.walk()][:4] == [
            "section", "paragraph", "section", "paragraph",
        ]

    def test_find_filters_by_type(self, sectioned):
        assert len(sectioned.find(NodeType.PARAGRAPH)) == 2
        assert len(sectioned.find(NodeType.TABLE)) == 1

    def test_find_accepts_several_types(self, sectioned):
        assert len(sectioned.find(NodeType.PARAGRAPH, NodeType.TABLE)) == 3

    def test_iter_with_parents_yields_parent_before_child(self, sectioned):
        pairs = sectioned.iter_with_parents()
        assert pairs[0][1] is None
        assert pairs[1][1].text == "Part I"

    def test_iter_with_parents_covers_every_node(self, sectioned):
        assert len(sectioned.iter_with_parents()) == len(list(sectioned.walk()))

    def test_nested_section_path_is_retained(self, sectioned):
        inner = [s for s in sectioned.sections() if s.text == "Subsection"][0]
        assert inner.attributes["path"] == ["Part I"]
        assert inner.level == 2


class TestTable:
    def test_dimensions(self):
        t = Table(header=["a", "b"], rows=[["1", "2"], ["3", "4"]])
        assert (t.n_rows, t.n_cols) == (2, 2)

    def test_ragged_rows_use_widest(self):
        assert Table(rows=[["1"], ["1", "2", "3"]]).n_cols == 3

    def test_empty(self):
        assert (Table().n_rows, Table().n_cols) == (0, 0)

    def test_roundtrip(self):
        t = Table(header=["h"], rows=[["v"]], caption="c")
        assert Table.from_dict(t.to_dict()) == t


class TestStats:
    def test_counts_by_node_type(self, sectioned):
        stats = sectioned.stats()
        assert stats["section"] == 2
        assert stats["paragraph"] == 2
        assert stats["table"] == 1
        assert stats["nodes"] == 5

    def test_max_depth_reflects_nesting(self, sectioned, flat):
        assert sectioned.stats()["max_depth"] == 3
        assert flat.stats()["max_depth"] == 1

    def test_text_length_includes_table_cells(self):
        doc = document(node(NodeType.TABLE, table=Table(header=["ab"], rows=[["cd"]])))
        assert doc.stats()["text_length"] == 4

    def test_every_node_type_is_present_in_stats(self, flat):
        stats = flat.stats()
        for t in NodeType:
            assert t.value in stats


class TestSerialization:
    def test_roundtrip_preserves_tree(self, sectioned):
        restored = CanonicalDocument.from_dict(sectioned.to_dict())
        assert restored.schema_version == SCHEMA_VERSION
        assert [n.id for n in restored.walk()] == [n.id for n in sectioned.walk()]
        assert [n.type for n in restored.walk()] == [n.type for n in sectioned.walk()]

    def test_roundtrip_preserves_flat_document(self, flat):
        restored = CanonicalDocument.from_dict(flat.to_dict())
        assert restored.has_sections() is False
        assert restored.find(NodeType.CODE)[0].attributes["language"] == "bash"

    def test_is_json_serializable(self, sectioned):
        payload = json.dumps(sectioned.to_dict())
        assert json.loads(payload)["schema_version"] == SCHEMA_VERSION

    def test_schema_version_is_2(self):
        assert SCHEMA_VERSION == "2.0"

    def test_to_dict_omits_empty_fields(self):
        data = node(NodeType.PARAGRAPH, "x").to_dict()
        assert "children" not in data and "table" not in data
        assert "attributes" not in data

    def test_structure_is_not_flattened_to_text(self, sectioned):
        """The whole point: a table survives as rows, not as prose."""
        data = sectioned.to_dict()
        table = data["content"][0]["children"][1]["children"][1]["table"]
        assert table["rows"] == [["2024", "391,035"]]
        assert table["header"] == ["Year", "Revenue"]

    def test_stats_are_included(self, sectioned):
        assert sectioned.to_dict()["stats"]["section"] == 2


class TestDerivedTextView:
    def test_text_is_derived_not_stored(self, sectioned):
        """Plain text is a view; nothing in to_dict() stores a rendered blob."""
        data = sectioned.to_dict()
        assert "text" not in {k for k in data}
        assert sectioned.text()

    def test_text_includes_titles_and_content(self, sectioned):
        text = sectioned.text()
        assert "Part I" in text
        assert "Outer prose." in text
        assert "Year | Revenue" in text

    def test_list_items_render_as_bullets(self):
        doc = document(
            node(NodeType.LIST, children=[node(NodeType.LIST_ITEM, "One")])
        )
        assert "- One" in doc.text()

    def test_flat_document_renders_in_order(self, flat):
        text = flat.text()
        assert text.index("Install") < text.index("Run the following.")
        assert text.index("Run the following.") < text.index("pip install x")


class TestSourceAndProcessor:
    def test_source_roundtrip(self):
        src = SourceInfo(
            path="a", file_name="b", doc_format="HTML",
            media_type="text/html", byte_size=1, checksum_sha256="c", source_url="u",
        )
        assert SourceInfo.from_dict(src.to_dict()) == src

    def test_processor_roundtrip(self):
        p = ProcessorInfo(name="html", version="2.0.0", processed_at="2026-01-01")
        assert ProcessorInfo.from_dict(p.to_dict()) == p

    def test_metadata_is_preserved(self):
        doc = document(node(NodeType.PARAGRAPH, "x"), metadata={"any": "value"})
        assert CanonicalDocument.from_dict(doc.to_dict()).metadata == {"any": "value"}
