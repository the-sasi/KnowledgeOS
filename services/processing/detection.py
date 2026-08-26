"""Document format detection.

Three signals, in decreasing order of trust:

1. **Content sniffing** - magic bytes and structural markers. Trusted first
   because a file's contents cannot be wrong about what it is, while an
   extension routinely is (SEC serves .htm, .html, and .txt files that are all
   HTML).
2. **Extension** - fast and usually right.
3. **mimetypes** - stdlib fallback for extensions we do not enumerate.

Ambiguity is resolved conservatively: when sniffing is inconclusive the
extension wins, and when nothing matches the result is UNKNOWN rather than a
guess. A wrong format sends a document to the wrong parser, which is worse
than refusing to process it.
"""

from __future__ import annotations

import json
import mimetypes
from enum import Enum
from pathlib import Path


class DocumentFormat(str, Enum):
    HTML = "HTML"
    PDF = "PDF"
    DOCX = "DOCX"
    TXT = "TXT"
    MARKDOWN = "MARKDOWN"
    JSON = "JSON"
    CSV = "CSV"
    XML = "XML"
    UNKNOWN = "UNKNOWN"


EXTENSION_MAP: dict[str, DocumentFormat] = {
    ".htm": DocumentFormat.HTML,
    ".html": DocumentFormat.HTML,
    ".xhtml": DocumentFormat.HTML,
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".txt": DocumentFormat.TXT,
    ".text": DocumentFormat.TXT,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".json": DocumentFormat.JSON,
    ".csv": DocumentFormat.CSV,
    ".tsv": DocumentFormat.CSV,
    ".xml": DocumentFormat.XML,
}

MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.HTML: "text/html",
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    DocumentFormat.TXT: "text/plain",
    DocumentFormat.MARKDOWN: "text/markdown",
    DocumentFormat.JSON: "application/json",
    DocumentFormat.CSV: "text/csv",
    DocumentFormat.XML: "application/xml",
}

_SNIFF_BYTES = 8192

_HTML_MARKERS = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<body",
    b"<table",
    b"<div",
    b"<p>",
    b"<span",
)


def sniff(head: bytes) -> DocumentFormat | None:
    """Identify a format from the first bytes, or None if inconclusive."""
    if not head:
        return None

    if head.startswith(b"%PDF-"):
        return DocumentFormat.PDF

    # DOCX is a zip; so are xlsx/pptx/jar. Only claim DOCX when the OOXML
    # word/ marker is present in the local file headers.
    if head.startswith(b"PK\x03\x04"):
        return DocumentFormat.DOCX if b"word/" in head else None

    stripped = head.lstrip()
    if stripped[:1] == b"\xef\xbb\xbf":  # UTF-8 BOM
        stripped = stripped[3:].lstrip()

    lowered = stripped[:2048].lower()

    if lowered.startswith(b"<?xml"):
        # An XHTML/inline-XBRL document declares XML but is really HTML.
        return DocumentFormat.HTML if b"<html" in lowered else DocumentFormat.XML

    if any(marker in lowered for marker in _HTML_MARKERS):
        return DocumentFormat.HTML

    if stripped[:1] in (b"{", b"["):
        try:
            json.loads(stripped.decode("utf-8", errors="strict"))
            return DocumentFormat.JSON
        except (ValueError, UnicodeDecodeError):
            # Truncated at _SNIFF_BYTES, so a parse failure is expected for
            # large JSON. Fall through and let the extension decide.
            return None

    return None


def detect_format(
    path: Path | str,
    head: bytes | None = None,
) -> DocumentFormat:
    """Detect a document's format from its contents and name.

    `head` may be supplied to avoid re-reading a file already in memory.
    """
    path = Path(path)

    if head is None and path.is_file():
        with path.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)

    sniffed = sniff(head or b"")
    extension = EXTENSION_MAP.get(path.suffix.lower())

    if sniffed is not None:
        # Sniffing wins, with one exception: .md and .csv files legitimately
        # contain HTML-ish markers, and their extension is the better signal.
        if extension in (DocumentFormat.MARKDOWN, DocumentFormat.CSV):
            return extension
        return sniffed

    if extension is not None:
        return extension

    guessed, _ = mimetypes.guess_type(path.name)
    for fmt, media in MEDIA_TYPES.items():
        if guessed == media:
            return fmt

    return DocumentFormat.UNKNOWN


def media_type_for(fmt: DocumentFormat) -> str | None:
    return MEDIA_TYPES.get(fmt)
