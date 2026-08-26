"""Tests that the processing layer stayed generic.

These guard the architectural properties the design depends on: the parser
handles any HTML document, namespace handling is vocabulary-agnostic, the
output shape follows the document rather than a fixed template, and no
source-specific vocabulary has leaked into the parser.
"""

from __future__ import annotations

import inspect

import pytest

from services.processing.canonical import NodeType, SourceInfo
from services.processing.parsers.html_parser import HtmlParser


@pytest.fixture
def parser() -> HtmlParser:
    return HtmlParser()


@pytest.fixture
def source() -> SourceInfo:
    return SourceInfo(path="t.htm", file_name="t.htm", doc_format="HTML")


def parse(parser, source, html: str):
    return parser.parse(source, html.encode("utf-8"))


class TestGenericNamespaceHandling:
    """Namespace rules apply to any vocabulary, not one known schema."""

    @pytest.mark.parametrize("prefix", ["ix", "xbrli", "docbook", "acme"])
    def test_any_prefix_metadata_container_is_dropped(self, parser, source, prefix):
        doc = parse(
            parser,
            source,
            f"<html><body><{prefix}:hidden>SECRETDATA</{prefix}:hidden>"
            "<p>Visible.</p></body></html>",
        )
        assert "SECRETDATA" not in doc.text()
        assert "Visible." in doc.text()

    @pytest.mark.parametrize("prefix", ["ix", "custom"])
    def test_any_prefix_content_wrapper_is_unwrapped(self, parser, source, prefix):
        doc = parse(
            parser,
            source,
            f"<html><body><p>Value <{prefix}:amount>1,234</{prefix}:amount>"
            " here.</p></body></html>",
        )
        assert "1,234" in doc.text()

    def test_html5_hidden_attribute_is_honoured(self, parser, source):
        doc = parse(
            parser, source, "<html><body><div hidden>Nope</div><p>Yes.</p></body></html>"
        )
        assert "Nope" not in doc.text()
        assert "Yes." in doc.text()


class TestGenericDocumentShapes:
    """The parser produces the shape the document has, not a fixed shape."""

    def test_document_without_headings_stays_flat(self, parser, source):
        """A technical page of prose, code, and a table gets no synthetic section."""
        doc = parse(
            parser,
            source,
            """<html><body>
            <p>Install it.</p>
            <pre><code class="language-bash">pip install x</code></pre>
            <table><tr><td>a</td><td>b</td></tr></table>
            </body></html>""",
        )
        assert doc.has_sections() is False
        assert [n.type for n in doc.content] == [
            NodeType.PARAGRAPH,
            NodeType.CODE,
            NodeType.TABLE,
        ]

    def test_document_with_headings_becomes_sections(self, parser, source):
        doc = parse(parser, source, "<html><body><h1>A</h1><p>x</p></body></html>")
        assert doc.has_sections() is True
        assert doc.content[0].type is NodeType.SECTION

    def test_preamble_before_first_heading_stays_at_root(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><p>Cover.</p><h1>Body</h1><p>Inside.</p></body></html>",
        )
        assert doc.content[0].type is NodeType.PARAGRAPH
        assert doc.content[0].text == "Cover."
        assert doc.content[1].type is NodeType.SECTION

    def test_article_shaped_html_works(self, parser, source):
        """Same parser, an entirely different kind of document."""
        doc = parse(
            parser,
            source,
            """<html><body>
            <h1>On Retrieval</h1>
            <h2>Abstract</h2><p>We study retrieval.</p>
            <h2>Method</h2><p>We ran experiments.</p>
            <blockquote>Prior work disagrees.</blockquote>
            </body></html>""",
        )
        assert [s.text for s in doc.sections()] == ["On Retrieval", "Abstract", "Method"]
        assert doc.find(NodeType.QUOTE)[0].text == "Prior work disagrees."

    def test_report_shaped_html_with_tables_and_lists(self, parser, source):
        doc = parse(
            parser,
            source,
            """<html><body>
            <h1>Quarterly Report</h1>
            <ul><li>Point one</li><li>Point two</li></ul>
            <table><tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Users</td><td>10</td></tr></table>
            </body></html>""",
        )
        section = doc.content[0]
        assert [c.type for c in section.children] == [NodeType.LIST, NodeType.TABLE]
        assert section.children[0].list_items() == ["Point one", "Point two"]


class TestCodeQuotesAndCaptions:
    def test_pre_becomes_a_code_node(self, parser, source):
        doc = parse(
            parser, source, "<html><body><pre>line one\nline two</pre></body></html>"
        )
        code = doc.find(NodeType.CODE)
        assert len(code) == 1
        assert "line one" in code[0].text

    def test_code_preserves_line_structure(self, parser, source):
        """Collapsing whitespace here would destroy what makes it code."""
        doc = parse(
            parser, source, "<html><body><pre>a\n    b\n        c</pre></body></html>"
        )
        assert "\n    b" in doc.find(NodeType.CODE)[0].text

    def test_code_language_from_class(self, parser, source):
        doc = parse(
            parser,
            source,
            '<html><body><pre><code class="language-python">x=1</code></pre>'
            "</body></html>",
        )
        assert doc.find(NodeType.CODE)[0].attributes["language"] == "python"

    def test_code_without_language_has_no_attribute(self, parser, source):
        doc = parse(parser, source, "<html><body><pre>plain</pre></body></html>")
        assert "language" not in doc.find(NodeType.CODE)[0].attributes

    def test_blockquote_becomes_a_quote_node(self, parser, source):
        doc = parse(
            parser, source, "<html><body><blockquote>Quoted.</blockquote></body></html>"
        )
        assert doc.find(NodeType.QUOTE)[0].text == "Quoted."

    def test_figcaption_becomes_a_caption_node(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><figure><figcaption>Fig 1.</figcaption></figure></body></html>",
        )
        assert doc.find(NodeType.CAPTION)[0].text == "Fig 1."

    def test_table_caption_is_kept_on_the_table(self, parser, source):
        doc = parse(
            parser,
            source,
            "<html><body><table><caption>T1</caption>"
            "<tr><td>a</td><td>b</td></tr></table></body></html>",
        )
        assert doc.tables()[0].caption == "T1"


def _executable_source(module) -> str:
    """Module source with comments and string literals removed.

    Domain vocabulary in prose is fine - the docstrings name the kinds of
    document the parser handles. What must not appear is domain vocabulary in
    *logic*: identifiers, constants, or literals the code branches on.
    """
    import io as _io
    import tokenize

    kept: list[str] = []
    readline = _io.StringIO(inspect.getsource(module)).readline
    for token in tokenize.generate_tokens(readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL):
            continue
        kept.append(token.string)
    return " ".join(kept).lower()


class TestNoDomainLogic:
    """Guard against the parser acquiring source-specific business logic."""

    def test_parser_logic_contains_no_domain_vocabulary(self):
        from services.processing.parsers import html_parser

        code = _executable_source(html_parser)

        # Assembled at runtime so this test does not trip over its own strings.
        forbidden = ["item" + "1a", "10-" + "k", "accession", "edgar", "cik", "filing"]
        for token in forbidden:
            assert token not in code, f"domain term leaked into parser logic: {token}"

    def test_parser_declares_no_domain_constants(self):
        """No lookup table of known section names, item numbers, or form types."""
        from services.processing.parsers import html_parser

        for name in dir(html_parser):
            if name.startswith("_") or not name.isupper():
                continue
            value = getattr(html_parser, name)
            if isinstance(value, (frozenset, set, tuple, list, dict)):
                flat = " ".join(str(v) for v in value).lower()
                for token in ["item", "part i", "10-" + "k", "risk factor"]:
                    assert token not in flat, f"{name} contains domain term: {token}"

    def test_headings_are_not_interpreted(self, parser, source):
        """A heading is a heading; the parser gives it no special meaning."""
        doc = parse(
            parser,
            source,
            "<html><body><h1>Item 1A. Risk Factors</h1><p>x</p></body></html>",
        )
        section = doc.sections()[0]
        assert section.text == "Item 1A. Risk Factors"
        # No domain fields such as item_number or statement_type.
        assert set(section.attributes) == {"level", "path"}

    def test_canonical_model_has_no_domain_vocabulary(self):
        from services.processing import canonical

        code = _executable_source(canonical)
        for token in ["accession", "edgar", "cik", "10-" + "k", "filing"]:
            assert token not in code, f"domain term leaked into model: {token}"
