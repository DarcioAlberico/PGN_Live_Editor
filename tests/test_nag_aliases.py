"""Aliases de NAG que tambem sao tipografia so valem colados a um lance (N-10 / R-206).

`„ ‚ ƒ ‰` sao como as fontes de simbolos dos livros (ChessBase) codificam
contrajogo, ataque, iniciativa e desenvolvimento -- mas tambem sao a aspa
baixa alema/polonesa, a aspa simples baixa, o florim e o por mil. Aplicados em
todo o texto, `Nf3 „gute Idee“` virava `Nf3 $132 {gute Idee"}`.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.nag_table import map_book_symbols
from pgn_live_editor.core.normalizer import LiveTextNormalizer


def normalized(text: str) -> str:
    return LiveTextNormalizer(locale="en").normalize_with_mapping(text).text


class TypographicAliasTests(unittest.TestCase):
    def test_a_german_opening_quote_stays_a_quote(self):
        self.assertIn("„gute Idee", normalized("1. e4 e5 2. Nf3 „gute Idee“ 2... Nc6"))

    def test_a_low_single_quote_and_per_mille_in_prose_stay(self):
        text = "1. e4 e5 {o ‚melhor‘ lance, 3‰ dos casos}"

        self.assertEqual(text, normalized(text))

    def test_glued_to_a_move_they_are_still_book_symbols(self):
        for alias, glyph in (("„", "⇆"), ("‚", "→"), ("ƒ", "↑"), ("‰", "⟳")):
            with self.subTest(alias=alias):
                self.assertIn(f"Nf3{glyph}", normalized(f"1. e4 e5 2. Nf3{alias} Nc6"))

    def test_the_book_converter_follows_the_same_rule(self):
        mapped, count = map_book_symbols("2. Nf3‚ „Zitat“")

        self.assertEqual(("2. Nf3→ „Zitat“", 1), (mapped, count))


if __name__ == "__main__":
    unittest.main()
