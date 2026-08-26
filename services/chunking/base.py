"""Chunking strategy interface and registry.

The pipeline resolves a strategy by name and never imports one directly, so
adding `fixed_size`, `semantic`, or `hierarchical` later means writing a class
and registering it — nothing else in KnowledgeOS changes.

    class FixedSizeChunker(ChunkingStrategy):
        name = "fixed_size"
        version = "1.0.0"

        def chunk(self, document, config): ...

    register_strategy(FixedSizeChunker())

Several strategies can be run over the same canonical corpus and compared,
because a chunk set is identified by (strategy, version, config) rather than
by document alone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from services.chunking.models import ChunkingConfig, ChunkingResult
from services.processing.canonical import CanonicalDocument


class ChunkingError(RuntimeError):
    """Raised when a document cannot be chunked.

    The message is stored on the job row and read by a human later, so it
    should say what failed, not merely that something did.
    """


class UnknownStrategyError(ChunkingError):
    """No strategy is registered under that name."""


class ChunkingStrategy(ABC):
    """Turns a CanonicalDocument into ordered chunks.

    A strategy must be pure: same document plus same config gives the same
    chunks, in the same order. It must not touch the database, the filesystem,
    or any model — those belong to the pipeline and to later stages.
    """

    #: Stable identifier, stored on every chunk.
    name: str = "base"

    #: Bump on any change that alters output. Documents chunked by an older
    #: version stay addressable; the new version produces a separate set.
    version: str = "0.0.0"

    @abstractmethod
    def chunk(
        self, document: CanonicalDocument, config: ChunkingConfig
    ) -> ChunkingResult:
        """Produce chunks for `document` under `config`."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name} v{self.version}>"


class StrategyRegistry:
    """Maps a strategy name to its implementation."""

    def __init__(self) -> None:
        self._strategies: dict[str, ChunkingStrategy] = {}

    def register(self, strategy: ChunkingStrategy, *, replace: bool = False) -> None:
        if not strategy.name or strategy.name == "base":
            raise ValueError(f"{strategy!r} must declare a name")

        existing = self._strategies.get(strategy.name)
        if existing is not None and not replace:
            raise ValueError(
                f"{strategy.name} is already registered (v{existing.version}); "
                "pass replace=True to override"
            )
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> ChunkingStrategy:
        strategy = self._strategies.get(name)
        if strategy is None:
            known = ", ".join(sorted(self._strategies)) or "none"
            raise UnknownStrategyError(
                f"No chunking strategy registered as {name!r}. Registered: {known}"
            )
        return strategy

    def get_or_none(self, name: str) -> ChunkingStrategy | None:
        return self._strategies.get(name)

    def names(self) -> list[str]:
        return sorted(self._strategies)

    def strategies(self) -> list[ChunkingStrategy]:
        return [self._strategies[n] for n in self.names()]


#: Process-wide registry. Strategies register on import of
#: services.chunking.strategies.
registry = StrategyRegistry()


def register_strategy(
    strategy: ChunkingStrategy, *, replace: bool = False
) -> ChunkingStrategy:
    registry.register(strategy, replace=replace)
    return strategy
