"""Tokenizer abstraction.

Chunking needs to count and split by tokens, but the *right* tokenizer depends
on the embedding model, which has not been chosen. This interface keeps that
decision open: a model-specific tokenizer (tiktoken, SentencePiece, a HF
tokenizer) drops in later without touching a chunking strategy.

The default `simple` tokenizer is a heuristic, not a real BPE tokenizer. It is
honest about that: token counts are approximate, and any chunk-size benchmark
must be re-run once a real tokenizer is in place.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod


class Tokenizer(ABC):
    """Counts and splits text in token units."""

    #: Stable identifier, recorded on every chunk so counts stay interpretable.
    name: str = "base"

    @abstractmethod
    def tokenize(self, text: str) -> list[str]:
        """Split text into token strings, preserving enough to rejoin it."""

    @abstractmethod
    def detokenize(self, tokens: list[str]) -> str:
        """Rejoin tokens into text. Need not be byte-identical to the input."""

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self.tokenize(text))

    def truncate(self, text: str, max_tokens: int) -> str:
        tokens = self.tokenize(text)
        if len(tokens) <= max_tokens:
            return text
        return self.detokenize(tokens[:max_tokens])

    def tail(self, text: str, max_tokens: int) -> str:
        """Last `max_tokens` tokens of text. Used to build overlap."""
        if max_tokens <= 0:
            return ""
        tokens = self.tokenize(text)
        if len(tokens) <= max_tokens:
            return text
        return self.detokenize(tokens[-max_tokens:])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name}>"


# Words, numbers (including 1,234.56), and standalone punctuation. Splitting
# punctuation out matters: subword tokenizers do the same, so counts track
# them more closely than a plain whitespace split would.
_TOKEN_RE = re.compile(r"\w+(?:[.,]\w+)*|[^\w\s]", re.UNICODE)


class SimpleTokenizer(Tokenizer):
    """Regex word/punctuation tokenizer.

    Approximates subword token counts without a model dependency. Real BPE
    tokenizers split long or rare words further, so this under-counts by
    roughly 25-35% on English prose. Chunks sized with it therefore run a
    little larger than the same number would give with a model tokenizer -
    acceptable while the embedding model is undecided, and the reason
    `tokenizer` is recorded on every chunk.
    """

    name = "simple"

    def tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        return _TOKEN_RE.findall(text)

    def detokenize(self, tokens: list[str]) -> str:
        if not tokens:
            return ""
        out: list[str] = []
        for token in tokens:
            # No space before closing punctuation, or after an opening bracket.
            if out and (token in ",.;:!?)]}%" or out[-1] in "([{$"):
                out.append(token)
            else:
                out.append(" " + token if out else token)
        return "".join(out)


class CharacterTokenizer(Tokenizer):
    """One token per character.

    Useful for tests that need exact, predictable budgets, and as a worst-case
    upper bound on token count.
    """

    name = "character"

    def tokenize(self, text: str) -> list[str]:
        return list(text)

    def detokenize(self, tokens: list[str]) -> str:
        return "".join(tokens)


_TOKENIZERS: dict[str, type[Tokenizer]] = {
    SimpleTokenizer.name: SimpleTokenizer,
    CharacterTokenizer.name: CharacterTokenizer,
}


def register_tokenizer(cls: type[Tokenizer]) -> type[Tokenizer]:
    """Register a tokenizer implementation under its `name`."""
    if not cls.name or cls.name == "base":
        raise ValueError(f"{cls.__name__} must declare a name")
    _TOKENIZERS[cls.name] = cls
    return cls


def get_tokenizer(name: str = SimpleTokenizer.name) -> Tokenizer:
    try:
        return _TOKENIZERS[name]()
    except KeyError:
        known = ", ".join(sorted(_TOKENIZERS))
        raise ValueError(f"Unknown tokenizer {name!r}. Registered: {known}") from None


def available_tokenizers() -> list[str]:
    return sorted(_TOKENIZERS)
