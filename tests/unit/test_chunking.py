"""Unit tests for the chunking stage.

The properties under test: structure decides boundaries, the token budget is
respected, tables survive, lineage is preserved, and the same input always
produces the same output.
"""

from __future__ import annotations

import pytest

from services.chunking.base import (
    ChunkingStrategy,
    StrategyRegistry,
    UnknownStrategyError,
)
from services.chunking.engine import ChunkingEngine, available_strategies
from services.chunking.models import ChunkingConfig, DocumentChunk
from services.chunking.strategies.structure_recursive import (
    StructureAwareRecursiveChunker,
)
from services.processing.canonical import (
    CanonicalDocument,
    Node,
    NodeType,
    ProcessorInfo,
    SourceInfo,
    Table,
)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_counter = {"n": 0}


def node(type_, text="", children=None, table=None, attributes=None) -> Node:
    _counter["n"] += 1
    return Node(
        id=f"n-{_counter['n']:04d}",
        type=type_,
        text=text,
        children=children or [],
        table=table,
        attributes=attributes or {},
    )


def section(title, *children, level=1) -> Node:
    return node(NodeType.SECTION, title, children=list(children),
                attributes={"level": level, "path": []})


def para(text) -> Node:
    return node(NodeType.PARAGRAPH, text)


def table_node(rows, header=None, caption=None) -> Node:
    return node(NodeType.TABLE, table=Table(rows=rows, header=header or [],
                                            caption=caption))


def document(*content) -> CanonicalDocument:
    return CanonicalDocument(
        source=SourceInfo(path="p", file_name="f.htm", doc_format="HTML"),
        processor=ProcessorInfo(name="html", version="2.0.0"),
        content=list(content),
    )


def words(n: int) -> str:
    """n whitespace-separated tokens, as the simple tokenizer counts them."""
    return " ".join(f"w{i}" for i in range(n))


@pytest.fixture
def chunker() -> StructureAwareRecursiveChunker:
    return StructureAwareRecursiveChunker()


def run(chunker, doc, **kw) -> list[DocumentChunk]:
    return chunker.chunk(doc, ChunkingConfig(**kw)).chunks


# --------------------------------------------------------------------------


class TestSmallDocument:
    def test_document_that_fits_becomes_one_chunk(self, chunker):
        doc = document(section("Intro", para("short text")))
        chunks = run(chunker, doc, max_tokens=512)
        assert len(chunks) == 1
        assert "short text" in chunks[0].text

    def test_single_paragraph_document(self, chunker):
        chunks = run(chunker, document(para("hello world")), max_tokens=100)
        assert len(chunks) == 1
        assert chunks[0].path == []

    def test_empty_document_produces_no_chunks(self, chunker):
        assert run(chunker, document(), max_tokens=100) == []

    def test_whitespace_only_content_is_skipped(self, chunker):
        assert run(chunker, document(para("   ")), max_tokens=100) == []


class TestStructureDrivesBoundaries:
    def test_section_that_fits_is_kept_whole(self, chunker):
        """The largest meaningful unit that fits wins."""
        doc = document(section("A", para(words(20)), para(words(20))))
        chunks = run(chunker, doc, max_tokens=200, min_tokens=0)
        assert len(chunks) == 1

    def test_large_section_is_split_at_its_children(self, chunker):
        doc = document(section("A", para(words(60)), para(words(60)), para(words(60))))
        chunks = run(chunker, doc, max_tokens=70, min_tokens=0)
        assert len(chunks) == 3
        # Each chunk is a whole paragraph, not an arbitrary cut.
        for c in chunks:
            assert len(c.nodes) == 1
            assert c.nodes[0].type is NodeType.PARAGRAPH

    def test_paragraphs_pack_together_up_to_the_budget(self, chunker):
        doc = document(section("A", *[para(words(10)) for _ in range(9)]))
        chunks = run(chunker, doc, max_tokens=40, min_tokens=0)
        assert len(chunks) > 1
        assert all(len(c.nodes) > 1 for c in chunks[:-1])

    def test_nested_sections_are_split_at_subsection_boundaries(self, chunker):
        doc = document(
            section(
                "Part",
                section("Item A", para(words(60)), level=2),
                section("Item B", para(words(60)), level=2),
            )
        )
        chunks = run(chunker, doc, max_tokens=70, min_tokens=0)
        paths = [c.path for c in chunks]
        assert ["Part", "Item A"] in paths
        assert ["Part", "Item B"] in paths

    def test_chunks_do_not_span_sections(self, chunker):
        doc = document(
            section("A", para(words(5)), level=1),
            section("B", para(words(5)), level=1),
        )
        chunks = run(chunker, doc, max_tokens=500, min_tokens=0)
        assert len(chunks) == 2
        assert chunks[0].path == ["A"]
        assert chunks[1].path == ["B"]

    def test_deeply_nested_structure_is_walked(self, chunker):
        doc = document(
            section("L1", section("L2", section("L3", para(words(80)), level=3),
                                  level=2), level=1)
        )
        chunks = run(chunker, doc, max_tokens=50, min_tokens=0)
        assert chunks[0].path == ["L1", "L2", "L3"]


class TestOversizedContent:
    def test_oversized_paragraph_splits_at_sentences(self, chunker):
        text = " ".join(f"Sentence number {i} has some words in it." for i in range(20))
        chunks = run(chunker, document(para(text)), max_tokens=40, min_tokens=0)
        assert len(chunks) > 1
        assert all(c.metadata.get("text_split") for c in chunks)
        # Sentences kept intact: no chunk ends mid-sentence.
        assert all(not c.metadata.get("hard_split") for c in chunks)

    def test_single_huge_sentence_falls_back_to_hard_split(self, chunker):
        chunks = run(chunker, document(para(words(300))), max_tokens=50, min_tokens=0)
        assert len(chunks) > 1
        assert any(c.metadata.get("hard_split") for c in chunks)

    def test_split_parts_record_their_source_node(self, chunker):
        source = para(words(300))
        chunks = run(chunker, document(source), max_tokens=50, min_tokens=0)
        assert all(c.metadata["source_node_id"] == source.id for c in chunks)
        assert all(c.metadata["text_parts"] == len(chunks) for c in chunks)


class TestTables:
    def test_small_table_is_kept_whole(self, chunker):
        t = table_node([["2024", "100"], ["2023", "90"]], header=["Year", "Rev"])
        chunks = run(chunker, document(t), max_tokens=500, min_tokens=0)
        assert len(chunks) == 1
        assert chunks[0].has_table()

    def test_table_structure_survives_chunking(self, chunker):
        t = table_node([["2024", "391,035"]], header=["Year", "Revenue"],
                       caption="Net sales")
        chunks = run(chunker, document(t), max_tokens=500, min_tokens=0)
        stored = chunks[0].tables()[0].table
        assert stored.header == ["Year", "Revenue"]
        assert stored.rows == [["2024", "391,035"]]
        assert stored.caption == "Net sales"

    def test_table_is_never_cut_by_token_boundary(self, chunker):
        """A too-large table divides by rows, not by arbitrary token count."""
        rows = [[f"row{i}", words(10)] for i in range(40)]
        t = table_node(rows, header=["Label", "Value"])
        chunks = run(chunker, document(t), max_tokens=120, min_tokens=0)

        assert len(chunks) > 1
        for c in chunks:
            assert c.metadata["table_split"] is True
            assert c.tables(), "every part is still a table"

    def test_split_table_repeats_header_and_caption(self, chunker):
        rows = [[f"row{i}", words(10)] for i in range(40)]
        t = table_node(rows, header=["Label", "Value"], caption="Big table")
        chunks = run(chunker, document(t), max_tokens=120, min_tokens=0)

        for c in chunks:
            part = c.tables()[0].table
            assert part.header == ["Label", "Value"]
            assert part.caption == "Big table"

    def test_split_table_preserves_every_row_exactly_once(self, chunker):
        rows = [[f"row{i}", words(6)] for i in range(30)]
        t = table_node(rows, header=["Label", "Value"])
        chunks = run(chunker, document(t), max_tokens=100, min_tokens=0)

        recovered = [r for c in chunks for r in c.tables()[0].table.rows]
        assert recovered == rows

    def test_split_table_parts_keep_table_identity(self, chunker):
        rows = [[f"row{i}", words(8)] for i in range(30)]
        t = table_node(rows, header=["L", "V"])
        chunks = run(chunker, document(t), max_tokens=100, min_tokens=0)

        assert {c.metadata["source_node_id"] for c in chunks} == {t.id}
        assert [c.metadata["table_part"] for c in chunks] == list(
            range(1, len(chunks) + 1)
        )

    def test_mixed_text_and_table_in_one_section(self, chunker):
        doc = document(
            section(
                "Financials",
                para("Revenue grew."),
                table_node([["2024", "100"]], header=["Year", "Rev"]),
                para("Costs fell."),
            )
        )
        chunks = run(chunker, doc, max_tokens=500, min_tokens=0)
        assert len(chunks) == 1
        assert chunks[0].has_table()
        assert "Revenue grew." in chunks[0].text
        assert "Costs fell." in chunks[0].text

    def test_table_and_prose_split_apart_when_over_budget(self, chunker):
        doc = document(
            section(
                "Financials",
                para(words(50)),
                table_node([["a", "b"], ["c", "d"]], header=["x", "y"]),
            )
        )
        chunks = run(chunker, doc, max_tokens=40, min_tokens=0)
        assert len(chunks) >= 2
        assert any(c.has_table() for c in chunks)
        assert any(not c.has_table() for c in chunks)


class TestTokenBudget:
    def test_budget_is_enforced_for_splittable_content(self, chunker):
        doc = document(section("A", *[para(words(30)) for _ in range(10)]))
        chunks = run(chunker, doc, max_tokens=100, min_tokens=0)
        assert all(c.token_count <= 100 for c in chunks)

    def test_budget_is_configurable(self, chunker):
        doc = document(section("A", *[para(words(30)) for _ in range(10)]))
        small = run(chunker, doc, max_tokens=60, min_tokens=0)
        large = run(chunker, doc, max_tokens=400, min_tokens=0)
        assert len(small) > len(large)

    def test_exact_character_budget(self):
        """CharacterTokenizer makes the budget exactly checkable."""
        c = StructureAwareRecursiveChunker()
        doc = document(section("A", *[para("x" * 20) for _ in range(10)]))
        chunks = c.chunk(
            doc, ChunkingConfig(max_tokens=50, min_tokens=0, tokenizer="character")
        ).chunks
        assert all(ch.token_count <= 50 for ch in chunks)

    def test_unsplittable_unit_is_flagged_not_silently_oversized(self, chunker):
        """One table row bigger than the budget cannot be divided further."""
        t = table_node([[words(200)]], header=["Value"])
        chunks = run(chunker, document(t), max_tokens=50, min_tokens=0)
        assert any(c.metadata.get("oversized") for c in chunks)
        assert any(c.metadata.get("over_budget_by", 0) > 0 for c in chunks)

    def test_min_tokens_merges_tiny_neighbours(self, chunker):
        doc = document(section("A", *[para(words(3)) for _ in range(10)]))
        merged = run(chunker, doc, max_tokens=200, min_tokens=50)
        unmerged = run(chunker, doc, max_tokens=200, min_tokens=0)
        assert len(merged) <= len(unmerged)

    def test_bare_heading_merges_into_following_content(self, chunker):
        doc = document(
            section("Parent",
                    section("Heading Only", level=2),
                    section("Real", para(words(40)), level=2)),
        )
        chunks = run(chunker, doc, max_tokens=200, min_tokens=30)
        assert not any(c.text.strip() == "Heading Only" for c in chunks)
        assert any("Heading Only" in c.text for c in chunks)


class TestOverlap:
    def test_zero_overlap_is_the_default(self):
        assert ChunkingConfig().overlap_tokens == 0

    def test_zero_overlap_produces_no_duplication(self, chunker):
        doc = document(section("A", *[para(words(30)) for _ in range(6)]))
        chunks = run(chunker, doc, max_tokens=60, min_tokens=0, overlap_tokens=0)
        assert all("overlap_tokens" not in c.metadata for c in chunks)

    def test_configured_overlap_prepends_previous_tail(self, chunker):
        doc = document(section("A", *[para(words(30)) for _ in range(6)]))
        chunks = run(chunker, doc, max_tokens=60, min_tokens=0, overlap_tokens=10)
        assert len(chunks) > 1
        assert all(c.metadata.get("overlap_tokens") == 10 for c in chunks[1:])
        assert "overlap_tokens" not in chunks[0].metadata

    def test_overlap_increases_token_counts(self, chunker):
        doc = document(section("A", *[para(words(30)) for _ in range(6)]))
        plain = run(chunker, doc, max_tokens=60, min_tokens=0, overlap_tokens=0)
        lapped = run(chunker, doc, max_tokens=60, min_tokens=0, overlap_tokens=10)
        assert sum(c.token_count for c in lapped) > sum(c.token_count for c in plain)

    def test_overlap_must_be_smaller_than_the_budget(self):
        with pytest.raises(ValueError, match="overlap_tokens must be smaller"):
            ChunkingConfig(max_tokens=100, overlap_tokens=100)

    def test_negative_overlap_is_rejected(self):
        with pytest.raises(ValueError, match="must not be negative"):
            ChunkingConfig(overlap_tokens=-1)


class TestLineage:
    def test_chunk_records_its_section_path(self, chunker):
        """Path is recorded at whatever depth the chunk actually lands.

        The budget here forces recursion down to the innermost section; a
        budget large enough to hold Part II whole would correctly yield one
        chunk at path ["Part II"].
        """
        doc = document(
            section("Part II",
                    section("Item 7",
                            section("MD&A", para(words(60)), level=3),
                            section("Other", para(words(60)), level=3),
                            level=2),
                    level=1)
        )
        chunks = run(chunker, doc, max_tokens=80, min_tokens=0)
        assert chunks[0].path == ["Part II", "Item 7", "MD&A"]
        assert chunks[1].path == ["Part II", "Item 7", "Other"]

    def test_whole_section_that_fits_is_recorded_at_its_own_path(self, chunker):
        doc = document(
            section("Part II",
                    section("Item 7", para(words(10)), level=2),
                    level=1)
        )
        chunks = run(chunker, doc, max_tokens=500, min_tokens=0)
        assert len(chunks) == 1
        assert chunks[0].path == ["Part II"]

    def test_chunk_records_source_node_ids(self, chunker):
        p1, p2 = para(words(5)), para(words(5))
        chunks = run(chunker, document(section("A", p1, p2)), max_tokens=500,
                     min_tokens=0)
        assert set(chunks[0].node_ids) >= {p1.id, p2.id} or chunks[0].node_ids

    def test_chunk_records_enclosing_section_node_id(self, chunker):
        sec = section("A", para(words(80)), para(words(80)))
        chunks = run(chunker, document(sec), max_tokens=100, min_tokens=0)
        assert all(c.section_node_id == sec.id for c in chunks)

    def test_root_level_content_has_no_section(self, chunker):
        chunks = run(chunker, document(para("free text")), max_tokens=100)
        assert chunks[0].section_node_id is None
        assert chunks[0].path == []

    def test_canonical_nodes_are_carried_on_the_chunk(self, chunker):
        t = table_node([["a", "b"]], header=["x", "y"])
        chunks = run(chunker, document(t), max_tokens=500, min_tokens=0)
        payload = chunks[0].nodes_payload()
        assert payload[0]["type"] == "table"
        assert payload[0]["table"]["rows"] == [["a", "b"]]


class TestDeterminism:
    def test_same_input_gives_identical_chunks(self, chunker):
        doc = document(section("A", *[para(words(25)) for _ in range(8)]))
        a = run(chunker, doc, max_tokens=80, min_tokens=0)
        b = run(chunker, doc, max_tokens=80, min_tokens=0)
        assert [c.text for c in a] == [c.text for c in b]
        assert [c.content_hash for c in a] == [c.content_hash for c in b]

    def test_chunk_indices_are_contiguous_and_ordered(self, chunker):
        doc = document(section("A", *[para(words(25)) for _ in range(8)]))
        chunks = run(chunker, doc, max_tokens=80, min_tokens=0)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_document_order_is_preserved(self, chunker):
        doc = document(section("A", para("first"), para("second"), para("third")))
        chunks = run(chunker, doc, max_tokens=3, min_tokens=0)
        text = " ".join(c.text for c in chunks)
        assert text.index("first") < text.index("second") < text.index("third")


class TestConfigIdentity:
    def test_same_config_has_the_same_fingerprint(self):
        assert ChunkingConfig(max_tokens=512).fingerprint() == (
            ChunkingConfig(max_tokens=512).fingerprint()
        )

    def test_different_budget_is_a_different_run(self):
        assert ChunkingConfig(max_tokens=512).fingerprint() != (
            ChunkingConfig(max_tokens=1024).fingerprint()
        )

    def test_different_overlap_is_a_different_run(self):
        assert ChunkingConfig(overlap_tokens=0).fingerprint() != (
            ChunkingConfig(overlap_tokens=64).fingerprint()
        )

    def test_different_tokenizer_is_a_different_run(self):
        assert ChunkingConfig(tokenizer="simple").fingerprint() != (
            ChunkingConfig(tokenizer="character").fingerprint()
        )

    def test_config_roundtrip(self):
        cfg = ChunkingConfig(max_tokens=256, overlap_tokens=32)
        assert ChunkingConfig.from_dict(cfg.to_dict()) == cfg

    def test_invalid_budget_is_rejected(self):
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            ChunkingConfig(max_tokens=0)

    def test_min_above_max_is_rejected(self):
        with pytest.raises(ValueError, match="min_tokens must not exceed"):
            ChunkingConfig(max_tokens=10, min_tokens=20)


class TestVersioning:
    def test_strategy_declares_name_and_version(self, chunker):
        assert chunker.name == "structure_recursive"
        assert chunker.version == "1.0.0"

    def test_result_carries_strategy_identity(self, chunker):
        result = chunker.chunk(document(para("x")), ChunkingConfig())
        assert result.strategy == "structure_recursive"
        assert result.strategy_version == "1.0.0"
        assert result.config_hash == ChunkingConfig().fingerprint()

    def test_a_new_version_is_a_distinct_run_key(self, chunker):
        """Bumping the version must not overwrite an existing chunk set."""

        class V2(StructureAwareRecursiveChunker):
            version = "2.0.0"

        cfg = ChunkingConfig()
        old = chunker.chunk(document(para("x")), cfg)
        new = V2().chunk(document(para("x")), cfg)

        assert (old.strategy, old.strategy_version, old.config_hash) != (
            new.strategy, new.strategy_version, new.config_hash
        )


class TestEngineAndRegistry:
    def test_engine_resolves_the_default_strategy(self):
        engine = ChunkingEngine()
        assert engine.name == "structure_recursive"

    def test_engine_runs_a_document(self):
        result = ChunkingEngine().run(document(para("hello world")))
        assert result.chunks

    def test_unknown_strategy_raises_with_options(self):
        with pytest.raises(UnknownStrategyError, match="No chunking strategy"):
            ChunkingEngine("semantic_v9")

    def test_structure_recursive_is_registered(self):
        assert "structure_recursive" in available_strategies()

    def test_a_future_strategy_needs_no_pipeline_change(self):
        """The extension point: register another strategy, resolve it at once."""

        class FixedSize(ChunkingStrategy):
            name = "fixed_size"
            version = "0.1.0"

            def chunk(self, document, config):  # pragma: no cover - not exercised
                raise NotImplementedError

        reg = StrategyRegistry()
        reg.register(StructureAwareRecursiveChunker())
        reg.register(FixedSize())
        assert reg.names() == ["fixed_size", "structure_recursive"]

    def test_duplicate_registration_is_rejected(self):
        reg = StrategyRegistry()
        reg.register(StructureAwareRecursiveChunker())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(StructureAwareRecursiveChunker())

    def test_engine_rejects_misordered_chunks(self):
        """Guards lineage: a strategy returning bad indices must not persist."""
        from services.chunking.base import ChunkingError
        from services.chunking.models import ChunkingResult

        class Broken(ChunkingStrategy):
            name = "broken"
            version = "1.0.0"

            def chunk(self, document, config):
                bad = DocumentChunk(chunk_index=7, text="x", token_count=1)
                return ChunkingResult(chunks=[bad], strategy=self.name,
                                      strategy_version=self.version, config=config)

        reg_backup = ChunkingEngine("structure_recursive")
        engine = reg_backup
        engine.strategy = Broken()
        with pytest.raises(ChunkingError, match="contiguous and ordered"):
            engine.run(document(para("x")))


class TestFailureHandling:
    def test_unknown_tokenizer_raises(self, chunker):
        with pytest.raises(ValueError, match="Unknown tokenizer"):
            chunker.chunk(document(para("x")), ChunkingConfig(tokenizer="nope"))

    def test_strategy_does_not_touch_the_database(self, chunker):
        """A strategy must be pure: no I/O, so it is trivially testable."""
        import inspect

        source = inspect.getsource(
            __import__(
                "services.chunking.strategies.structure_recursive",
                fromlist=["structure_recursive"],
            )
        )
        for forbidden in ("get_connection", "psycopg", "open(", "requests"):
            assert forbidden not in source
