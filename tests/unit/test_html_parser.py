"""Unit tests for the HTML parser."""

from __future__ import annotations

import pytest

from services.processing.base import ProcessingError
from services.processing.canonical import NodeType, SourceInfo
from services.processing.detection import DocumentFormat
from services.processing.parsers.html_parser import (
    HtmlParser,
    normalize_text,
    parse_table,
)


@pytest.fixture
def parser() -> HtmlParser:
    return HtmlParser()


@pytest.fixture
def source() -> SourceInfo:
    return SourceInfo(path="t.htm", file_name="t.htm", doc_format="HTML")


def parse(parser, source, html: str):
    return parser.parse(source, html.encode("utf-8"))


class TestNoiseRemoval:
    def test_scripts_and_styles_are_dropped(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <script>var secret = 'js';</script>
            <style>.x { color: red; }</style>
            <p>Real content.</p>
            </body></html>""",
        )
        text = doc.text()
        assert "Real content." in text
        assert "secret" not in text
        assert "color: red" not in text

    def test_navigation_chrome_is_dropped(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <nav>Home About</nav><header>Site header</header>
            <p>Body text.</p>
            <footer>Copyright</footer><aside>Sidebar</aside>
            </body></html>""",
        )
        text = doc.text()
        assert "Body text." in text
        for noise in ("Home About", "Site header", "Copyright", "Sidebar"):
            assert noise not in text

    def test_hidden_elements_are_dropped(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <div style="display:none">Hidden junk</div>
            <p>Visible.</p></body></html>""",
        )
        assert "Hidden junk" not in doc.text()
        assert "Visible." in doc.text()

    def test_namespaced_metadata_containers_are_dropped(self, parser, source):
        """Namespaced containers named hidden/header/metadata hold machine data."""
        doc = parse(
            parser,
            source,
            """<html><body>
            <ix:hidden><ix:nonNumeric>0000320193</ix:nonNumeric></ix:hidden>
            <p>Item 1. Business</p></body></html>""",
        )
        assert "0000320193" not in doc.text()
        assert "Business" in doc.text()

    def test_namespaced_wrappers_around_text_are_unwrapped(self, parser, source):
        """Other namespaced elements wrap visible text; unwrap, do not drop."""
        doc = parse(
            parser,
            source,
            """<html><body><p>Revenue was
            <ix:nonFraction>391,035</ix:nonFraction> million.</p></body></html>""",
        )
        assert "391,035" in doc.text()


class TestHeadingsAndHierarchy:
    def test_native_headings_build_a_tree(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <h1>Part I</h1><p>Intro.</p>
            <h2>Item 1</h2><p>Business.</p>
            <h3>Segments</h3><p>Details.</p>
            <h2>Item 1A</h2><p>Risks.</p>
            </body></html>""",
        )
        titles = [(s.text, s.level) for s in doc.sections()]
        assert titles == [
            ("Part I", 1),
            ("Item 1", 2),
            ("Segments", 3),
            ("Item 1A", 2),
        ]

    def test_subsection_nesting_is_preserved(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><h1>A</h1><h2>B</h2><h3>C</h3></body></html>",
        )
        a = doc.content[0]
        assert a.type is NodeType.SECTION and a.text == "A"
        assert a.children[0].text == "B"
        assert a.children[0].children[0].text == "C"

    def test_section_path_records_ancestors(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><h1>A</h1><h2>B</h2><h3>C</h3></body></html>",
        )
        c = [s for s in doc.sections() if s.text == "C"][0]
        assert c.attributes["path"] == ["A", "B"]

    def test_deeper_heading_closes_at_shallower_sibling(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><h1>A</h1><h3>deep</h3><h1>B</h1></body></html>",
        )
        assert [n.text for n in doc.content] == ["A", "B"]

    def test_styled_headings_used_when_no_heading_tags(self, parser, source):
        """Generated HTML often expresses headings through styling alone."""
        doc = parse(
            parser,
            source,
            """<html><body>
            <div><span style="font-weight:700;font-size:12pt">Item 1. Business</span></div>
            <div><span style="font-weight:400">We make devices.</span></div>
            <div><span style="font-weight:700;font-size:9pt">Products</span></div>
            <div><span style="font-weight:400">Phones and laptops.</span></div>
            </body></html>""",
        )
        titles = [s.text for s in doc.sections()]
        assert "Item 1. Business" in titles
        assert "Products" in titles

    def test_larger_font_becomes_higher_level(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <div><span style="font-weight:700;font-size:14pt">Big</span></div>
            <div><span style="font-weight:700;font-size:9pt">Small</span></div>
            </body></html>""",
        )
        levels = {s.text: s.level for s in doc.sections()}
        assert levels["Big"] < levels["Small"]

    def test_native_headings_suppress_the_styled_fallback(self, parser, source):
        """A bold sentence next to real <h*> tags is a paragraph, not a heading."""
        doc = parse(
            parser,
            source,
            """<html><body>
            <h1>Real</h1>
            <div><span style="font-weight:700">Bold text</span></div>
            </body></html>""",
        )
        assert [s.text for s in doc.sections()] == ["Real"]
        assert "Bold text" in doc.text()

    def test_long_bold_text_is_not_a_heading(self, parser, source):
        long_text = "word " * 100
        doc = parse(
            parser,
            source,
            f'<html><body><div><span style="font-weight:700">{long_text}</span></div>'
            "</body></html>",
        )
        assert doc.sections() == []

    def test_content_before_any_heading_is_kept(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><p>Preamble.</p><h1>Later</h1><p>Body.</p></body></html>",
        )
        assert "Preamble." in doc.text()


class TestTables:
    def test_table_kept_as_rows_not_prose(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body><table>
            <tr><th>Year</th><th>Revenue</th></tr>
            <tr><td>2024</td><td>391,035</td></tr>
            </table></body></html>""",
        )
        tables = doc.tables()
        assert len(tables) == 1
        assert tables[0].header == ["Year", "Revenue"]
        assert tables[0].rows == [["2024", "391,035"]]

    def test_colspan_preserves_alignment(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            """<table>
            <tr><td colspan="2">Wide</td><td>C</td></tr>
            <tr><td>1</td><td>2</td><td>3</td></tr>
            </table>""",
            "lxml",
        )
        table = parse_table(soup.find("table"))
        assert table.rows[0] == ["Wide", "", "C"]

    def test_empty_spacer_columns_are_dropped(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            """<table>
            <tr><td>A</td><td></td><td>B</td></tr>
            <tr><td>1</td><td></td><td>2</td></tr>
            </table>""",
            "lxml",
        )
        table = parse_table(soup.find("table"))
        assert table.rows == [["A", "B"], ["1", "2"]]

    def test_fully_empty_table_is_skipped(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><table><tr><td></td></tr></table><p>x</p></body></html>",
        )
        assert doc.tables() == []

    def test_table_lands_in_its_section(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <h1>Financials</h1>
            <table><tr><td>1</td><td>2</td></tr></table>
            </body></html>""",
        )
        section = doc.content[0]
        assert section.text == "Financials"
        assert section.children[0].type is NodeType.TABLE


class TestLists:
    def test_unordered_list_items(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><ul><li>One</li><li>Two</li></ul></body></html>",
        )
        lists = doc.find(NodeType.LIST)
        assert lists[0].list_items() == ["One", "Two"]

    def test_ordered_list_items(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><ol><li>First</li><li>Second</li></ol></body></html>",
        )
        lists = doc.find(NodeType.LIST)
        assert lists[0].list_items() == ["First", "Second"]

    def test_empty_list_is_skipped(self, parser, source):
        doc = parse(parser, source, "<html><body><ul></ul><p>x</p></body></html>")
        assert doc.find(NodeType.LIST) == []


class TestNormalization:
    def test_collapses_whitespace(self):
        assert normalize_text("  a \n\t  b  ") == "a b"

    def test_folds_non_breaking_space(self):
        assert normalize_text("Item\xa01A.\xa0\xa0Risk") == "Item 1A. Risk"

    def test_strips_zero_width_characters(self):
        assert normalize_text("a​b﻿") == "ab"

    def test_empty_input(self):
        assert normalize_text("") == ""

    def test_entities_are_decoded(self, parser, source):
        doc = parse(
            parser, source, "<html><body><p>Apple&#8217;s &amp; Co.</p></body></html>"
        )
        # The typographic apostrophe is preserved rather than folded to ASCII:
        # the canonical document stays faithful to the source, and normalizing
        # punctuation for matching belongs to retrieval, not to processing.
        assert "Apple’s & Co." in doc.text()


class TestMetadataAndSource:
    def test_source_info_is_retained(self, parser, source):
        doc = parse(parser, source, "<html><body><p>x</p></body></html>")
        assert doc.source.file_name == "t.htm"
        assert doc.source.doc_format == "HTML"

    def test_processor_identity_is_recorded(self, parser, source):
        doc = parse(parser, source, "<html><body><p>x</p></body></html>")
        assert doc.processor.name == "html"
        assert doc.processor.version == HtmlParser.version
        assert doc.schema_version == "2.0"

    def test_html_title_and_meta_are_captured(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html lang="en"><head><title>Form 10-K</title>
            <meta name="author" content="Apple Inc."></head>
            <body><p>x</p></body></html>""",
        )
        assert doc.metadata["html_title"] == "Form 10-K"
        assert doc.metadata["language"] == "en"
        assert doc.metadata["html_meta"]["author"] == "Apple Inc."


class TestErrors:
    def test_empty_file_raises_processing_error(self, parser, source):
        with pytest.raises(ProcessingError, match="Empty file"):
            parser.parse(source, b"")

    def test_document_with_no_content_raises(self, parser, source):
        with pytest.raises(ProcessingError, match="No extractable content"):
            parse(parser, source, "<html><body><script>x</script></body></html>")

    def test_malformed_html_still_parses(self, parser, source):
        doc = parse(parser, source, "<html><body><p>Unclosed<div>Nested")
        assert "Unclosed" in doc.text()


class TestParserContract:
    def test_declares_html_support(self, parser):
        assert parser.supports(DocumentFormat.HTML)
        assert not parser.supports(DocumentFormat.PDF)

    def test_is_registered(self):
        from services.processing.base import registry

        assert registry.get(DocumentFormat.HTML).name == "html"
