"""Chunking strategy implementations.

Importing this package registers every available strategy. Adding one means
adding a module here and importing it below - nothing else changes.
"""

from services.chunking.strategies import structure_recursive  # noqa: F401

__all__ = ["structure_recursive"]
