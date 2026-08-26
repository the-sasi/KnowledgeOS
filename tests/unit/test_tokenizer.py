"""Unit tests for the tokenizer abstraction."""

from __future__ import annotations

import pytest

from services.chunking.tokenizer import (
    CharacterTokenizer,
    SimpleTokenizer,
    Tokenizer,
    available_tokenizers,
    get_tokenizer,
    register_tokenizer,
)


class TestSimpleTokenizer:
    def test_counts_words(self):
        assert SimpleTokenizer().count("one two three") == 3

    def test_empty_text_is_zero(self):
        assert SimpleTokenizer().count("") == 0

    def test_punctuation_counts_separately(self):
        """Subword tokenizers do the same, so counts track them more closely."""
        assert SimpleTokenizer().count("Hello, world!") == 4  # Hello , world !

    def test_numbers_with_separators_stay_one_token(self):
        assert SimpleTokenizer().count("391,035.50") == 1

    def test_roundtrip_is_readable(self):
        tok = SimpleTokenizer()
        text = "Revenue rose 12% in 2024."
        assert tok.detokenize(tok.tokenize(text)) == text

    def test_truncate_respects_budget(self):
        tok = SimpleTokenizer()
        assert tok.count(tok.truncate("a b c d e f", 3)) == 3

    def test_truncate_leaves_short_text_alone(self):
        assert SimpleTokenizer().truncate("a b", 10) == "a b"

    def test_tail_returns_the_end(self):
        tok = SimpleTokenizer()
        assert tok.tail("a b c d e", 2) == "d e"

    def test_tail_of_zero_is_empty(self):
        assert SimpleTokenizer().tail("a b c", 0) == ""

    def test_tail_shorter_than_request_returns_all(self):
        assert SimpleTokenizer().tail("a b", 10) == "a b"


class TestCharacterTokenizer:
    def test_one_token_per_character(self):
        assert CharacterTokenizer().count("abcd") == 4

    def test_exact_roundtrip(self):
        tok = CharacterTokenizer()
        assert tok.detokenize(tok.tokenize("a b\nc")) == "a b\nc"


class TestRegistry:
    def test_default_is_simple(self):
        assert get_tokenizer().name == "simple"

    def test_lookup_by_name(self):
        assert isinstance(get_tokenizer("character"), CharacterTokenizer)

    def test_unknown_name_raises_with_options(self):
        with pytest.raises(ValueError, match="Unknown tokenizer"):
            get_tokenizer("gpt-9")

    def test_available_lists_registered(self):
        assert {"simple", "character"} <= set(available_tokenizers())

    def test_a_model_tokenizer_can_be_plugged_in(self):
        """The point of the abstraction: swap in a model-specific tokenizer."""

        @register_tokenizer
        class FakeBpe(Tokenizer):
            name = "fake-bpe"

            def tokenize(self, text):
                # Pretend subword splitting: every 4 characters is a token.
                return [text[i : i + 4] for i in range(0, len(text), 4)]

            def detokenize(self, tokens):
                return "".join(tokens)

        tok = get_tokenizer("fake-bpe")
        assert tok.count("abcdefgh") == 2
        assert "fake-bpe" in available_tokenizers()

    def test_tokenizer_without_a_name_is_rejected(self):
        class Nameless(Tokenizer):
            def tokenize(self, text):
                return []

            def detokenize(self, tokens):
                return ""

        with pytest.raises(ValueError, match="must declare a name"):
            register_tokenizer(Nameless)
