"""Chunking engine.

The seam between the pipeline and a strategy:

    CanonicalDocument -> ChunkingEngine -> ChunkingStrategy -> DocumentChunk[]

Thin on purpose. It resolves the strategy by name and validates the result, so
callers name a strategy in configuration rather than importing one, and no
caller is coupled to a particular algorithm.
"""

from __future__ import annotations

from services.chunking import strategies  # noqa: F401  (registers strategies)
from services.chunking.base import ChunkingError, registry
from services.chunking.models import ChunkingConfig, ChunkingResult
from services.processing.canonical import CanonicalDocument

DEFAULT_STRATEGY = "structure_recursive"


class ChunkingEngine:
    def __init__(self, strategy: str = DEFAULT_STRATEGY) -> None:
        self.strategy = registry.get(strategy)

    @property
    def name(self) -> str:
        return self.strategy.name

    @property
    def version(self) -> str:
        return self.strategy.version

    def run(
        self, document: CanonicalDocument, config: ChunkingConfig | None = None
    ) -> ChunkingResult:
        config = config or ChunkingConfig()
        result = self.strategy.chunk(document, config)

        # A strategy returning mis-ordered chunks would silently corrupt
        # lineage, so check rather than trust.
        for expected, chunk in enumerate(result.chunks):
            if chunk.chunk_index != expected:
                raise ChunkingError(
                    f"{self.name} produced chunk_index {chunk.chunk_index} "
                    f"at position {expected}; chunks must be contiguous and ordered"
                )
        return result


def available_strategies() -> list[str]:
    return registry.names()
