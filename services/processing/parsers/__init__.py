"""Parser implementations.

Importing this package registers every available parser. Adding a format means
adding a module here and importing it below - nothing else in the codebase
changes.
"""

from services.processing.parsers import html_parser  # noqa: F401

__all__ = ["html_parser"]
