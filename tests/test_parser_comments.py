"""Comentarios do parser: nada some, nada muda de dono, e prosa longa e linear (R-203)."""

from __future__ import annotations

import time
import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


class CommentOwnershipTests(unittest.TestCase):
    def test_n03_a_comment_before_two_variations_is_not_deleted(self):
        # A prosa entre as variantes se colava ao `{A}` numa lista e nao na
        # outra; a transferencia para a segunda variante apagava o `{A}`.
        ast = parse("1. e4 e5 {A} (2. Nf3) text (1... c5) 2. Nf3")

        e5 = ast.moves[1]
        self.assertIn("A", e5.comments_after)
        everything = " ".join(
            [
                *e5.comments_after,
                *(c for v in ast.moves[0].variations + e5.variations for m in v for c in m.comments_before),
            ]
        )
        self.assertIn("text", everything)

    def test_prose_after_a_variation_does_not_glue_to_the_comment_before_it(self):
        ast = parse("1. e4 e5 {antes} (1... c5) depois 2. Nf3")

        self.assertNotIn("antes depois", " ".join(ast.moves[1].comments_after))

    def test_prose_words_are_joined_with_single_spaces(self):
        ast = parse("1. e4 e5 a strong   central   move 2. Nf3")

        self.assertEqual(["a strong central move"], ast.moves[1].comments_after)


class LongProseTests(unittest.TestCase):
    def test_n16_long_prose_is_linear_in_the_parser(self):
        # So o parser: normalizar e tokenizar tem custo proprio (Fase 18). Com a
        # juncao string a string, 200 mil palavras levavam 80 s aqui; agora 0,14 s.
        text = "1. e4 e5 " + "pressure " * 200_000 + "2. Nf3"
        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))

        started = time.perf_counter()
        ast = TolerantParser(locale="en").parse(tokens)
        elapsed = time.perf_counter() - started

        self.assertEqual(["e4", "e5", "Nf3"], [m.san for m in ast.moves])
        self.assertLess(elapsed, 2.0, f"{elapsed:.1f} s para 200 mil palavras")


if __name__ == "__main__":
    unittest.main()
