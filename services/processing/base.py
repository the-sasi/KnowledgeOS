"""Parser interface and registry.

Adding a format later means writing a parser and registering it. The pipeline
resolves parsers through the registry and never names one directly, so
`services/ingestion` and `pipeline.py` do not change when PDF or DOCX arrives.

    class PdfParser(DocumentParser):
        name = "pdf"
        version = "0.1.0"
        supported_formats = frozenset({DocumentFormat.PDF})

        def parse(self, source, raw): ...

    register_parser(PdfParser())
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from services.processing.canonical import CanonicalDocument, SourceInfo
from services.processing.detection import DocumentFormat


class ProcessingError(RuntimeError):
    """Raised when a document cannot be parsed.

    Carries a message meant to be stored on the document row and read by a
    human later, so it should say what failed, not just that something did.
    """


class UnsupportedFormatError(ProcessingError):
    """No registered parser handles this format."""


class DocumentParser(ABC):
    """Turns raw bytes of one format into a CanonicalDocument.

    A parser must be pure with respect to the filesystem: it receives bytes and
    returns a document. It must never write to `data/raw`, and it must not know
    about the database, chunking, embeddings, or retrieval.
    """

    #: Stable identifier, stored on the document row.
    name: str = "base"

    #: Bump on any change that alters output. Documents processed by an older
    #: version are then detected as stale and can be reprocessed.
    version: str = "0.0.0"

    #: Formats this parser claims.
    supported_formats: frozenset[DocumentFormat] = frozenset()

    def supports(self, fmt: DocumentFormat) -> bool:
        return fmt in self.supported_formats

    @abstractmethod
    def parse(self, source: SourceInfo, raw: bytes) -> CanonicalDocument:
        """Parse `raw` into canonical form.

        Should raise ProcessingError (not a bare exception) for input it
        cannot handle, so the pipeline can record a useful message.
        """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        formats = ",".join(sorted(f.value for f in self.supported_formats))
        return f"<{type(self).__name__} {self.name} v{self.version} [{formats}]>"


class ParserRegistry:
    """Maps a document format to the parser that handles it."""

    def __init__(self) -> None:
        self._parsers: dict[DocumentFormat, DocumentParser] = {}

    def register(self, parser: DocumentParser, *, replace: bool = False) -> None:
        if not parser.supported_formats:
            raise ValueError(f"{parser!r} declares no supported formats")

        for fmt in parser.supported_formats:
            existing = self._parsers.get(fmt)
            if existing is not None and not replace:
                raise ValueError(
                    f"{fmt.value} is already handled by {existing.name}; "
                    f"pass replace=True to override with {parser.name}"
                )
            self._parsers[fmt] = parser

    def get(self, fmt: DocumentFormat) -> DocumentParser:
        parser = self._parsers.get(fmt)
        if parser is None:
            supported = ", ".join(sorted(f.value for f in self._parsers)) or "none"
            raise UnsupportedFormatError(
                f"No parser registered for {fmt.value}. Registered formats: {supported}"
            )
        return parser

    def get_or_none(self, fmt: DocumentFormat) -> DocumentParser | None:
        return self._parsers.get(fmt)

    def supported_formats(self) -> list[DocumentFormat]:
        return sorted(self._parsers, key=lambda f: f.value)

    def parsers(self) -> list[DocumentParser]:
        return list(dict.fromkeys(self._parsers.values()))


#: Process-wide registry. Parsers register themselves on import of
#: services.processing.parsers.
registry = ParserRegistry()


def register_parser(parser: DocumentParser, *, replace: bool = False) -> DocumentParser:
    registry.register(parser, replace=replace)
    return parser
