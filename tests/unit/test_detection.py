"""Unit tests for document format detection."""

from __future__ import annotations

import pytest

from services.processing.base import ParserRegistry, UnsupportedFormatError
from services.processing.detection import DocumentFormat, detect_format, sniff
from services.processing.parsers.html_parser import HtmlParser


class TestSniffing:
    @pytest.mark.parametrize(
        "head,expected",
        [
            (b"%PDF-1.7\n", DocumentFormat.PDF),
            (b"<!DOCTYPE html><html>", DocumentFormat.HTML),
            (b"<html><body>x</body></html>", DocumentFormat.HTML),
            (b"  \n<div>content</div>", DocumentFormat.HTML),
            (b'<?xml version="1.0"?><root/>', DocumentFormat.XML),
            (b'{"a": 1}', DocumentFormat.JSON),
            (b"[1, 2, 3]", DocumentFormat.JSON),
            (b"", None),
            (b"just some prose", None),
        ],
    )
    def test_sniff(self, head, expected):
        assert sniff(head) is expected

    def test_xhtml_declared_as_xml_is_html(self):
        head = b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        assert sniff(head) is DocumentFormat.HTML

    def test_zip_without_word_marker_is_not_docx(self):
        assert sniff(b"PK\x03\x04\x14\x00xl/workbook.xml") is None

    def test_docx_zip_with_word_marker(self):
        assert sniff(b"PK\x03\x04\x14\x00word/document.xml") is DocumentFormat.DOCX

    def test_utf8_bom_is_skipped(self):
        assert sniff(b"\xef\xbb\xbf<html>") is DocumentFormat.HTML


class TestDetectFormat:
    def test_content_beats_extension(self, tmp_path):
        """SEC serves HTML under a .txt name; contents must win."""
        path = tmp_path / "filing.txt"
        path.write_bytes(b"<html><body><p>10-K</p></body></html>")
        assert detect_format(path) is DocumentFormat.HTML

    def test_extension_used_when_content_is_inconclusive(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_bytes(b"plain prose with no markers")
        assert detect_format(path) is DocumentFormat.TXT

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("a.htm", DocumentFormat.HTML),
            ("a.html", DocumentFormat.HTML),
            ("a.pdf", DocumentFormat.PDF),
            ("a.docx", DocumentFormat.DOCX),
            ("a.md", DocumentFormat.MARKDOWN),
            ("a.json", DocumentFormat.JSON),
            ("a.csv", DocumentFormat.CSV),
            ("a.xml", DocumentFormat.XML),
        ],
    )
    def test_extension_map(self, tmp_path, name, expected):
        path = tmp_path / name
        path.write_bytes(b"inconclusive")
        assert detect_format(path) is expected

    def test_markdown_extension_wins_over_html_markers(self, tmp_path):
        """Markdown legitimately embeds HTML; the extension is the better signal."""
        path = tmp_path / "readme.md"
        path.write_bytes(b"# Title\n\n<div>inline html</div>")
        assert detect_format(path) is DocumentFormat.MARKDOWN

    def test_unknown_when_nothing_matches(self, tmp_path):
        path = tmp_path / "mystery.zzz"
        path.write_bytes(b"\x00\x01\x02binary")
        assert detect_format(path) is DocumentFormat.UNKNOWN

    def test_supplied_head_avoids_reading_file(self):
        assert detect_format("nonexistent.htm", head=b"%PDF-1.4") is DocumentFormat.PDF

    def test_real_sec_style_inline_xbrl(self, tmp_path):
        path = tmp_path / "aapl-20240928.htm"
        path.write_bytes(
            b'<?xml version="1.0"?>\n<html xmlns:ix="http://www.xbrl.org/inlineXBRL">'
            b"<body><div>Item 1.</div></body></html>"
        )
        assert detect_format(path) is DocumentFormat.HTML


class TestRegistry:
    def test_resolves_registered_parser(self):
        registry = ParserRegistry()
        parser = HtmlParser()
        registry.register(parser)
        assert registry.get(DocumentFormat.HTML) is parser

    def test_unregistered_format_raises_with_a_useful_message(self):
        registry = ParserRegistry()
        registry.register(HtmlParser())
        with pytest.raises(UnsupportedFormatError, match="No parser registered for PDF"):
            registry.get(DocumentFormat.PDF)

    def test_duplicate_registration_is_rejected(self):
        registry = ParserRegistry()
        registry.register(HtmlParser())
        with pytest.raises(ValueError, match="already handled"):
            registry.register(HtmlParser())

    def test_replace_allows_override(self):
        registry = ParserRegistry()
        registry.register(HtmlParser())
        replacement = HtmlParser()
        registry.register(replacement, replace=True)
        assert registry.get(DocumentFormat.HTML) is replacement

    def test_parser_with_no_formats_is_rejected(self):
        class Empty(HtmlParser):
            supported_formats = frozenset()

        with pytest.raises(ValueError, match="no supported formats"):
            ParserRegistry().register(Empty())

    def test_get_or_none_returns_none(self):
        assert ParserRegistry().get_or_none(DocumentFormat.PDF) is None

    def test_future_parser_needs_no_pipeline_change(self):
        """The extension point: register a new format, resolve it immediately."""
        from services.processing.base import DocumentParser

        class FakePdfParser(DocumentParser):
            name = "pdf"
            version = "0.1.0"
            supported_formats = frozenset({DocumentFormat.PDF})

            def parse(self, source, raw):  # pragma: no cover - not exercised
                raise NotImplementedError

        registry = ParserRegistry()
        registry.register(HtmlParser())
        registry.register(FakePdfParser())

        assert registry.get(DocumentFormat.PDF).name == "pdf"
        assert registry.supported_formats() == [DocumentFormat.HTML, DocumentFormat.PDF]
