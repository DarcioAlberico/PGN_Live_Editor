"""Regressao de livro vira variante no lugar padrao do PGN (L-18 / R-211).

Um paragrafo que volta a um lance ja jogado (`2.Nc3` depois de `...3.d4 cxd4`)
e alternativa ao lance que ele repete. O conversor punha o `(` no fim do
paragrafo anterior -- `3.d4 cxd4 ( 2.Nc3 ... )` --, o que so funcionava porque
o parser tolerante re-ancora a variante; qualquer outro leitor recusava
(`illegal san: 'Nc3'` no python-chess).
"""

from __future__ import annotations

import io
import unittest

import _isolation  # noqa: F401
import chess.pgn

from pgn_live_editor.core.book_import import convert_book_text
from pgn_live_editor.core.move_tree import render_game_movetext
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def external_reader(movetext: str) -> chess.pgn.Game:
    return chess.pgn.read_game(io.StringIO(movetext + " *"))


def ours(movetext: str) -> str:
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(movetext)
    return render_game_movetext(TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized)))


class RegressionPlacementTests(unittest.TestCase):
    def test_the_variation_follows_the_move_it_replaces(self):
        converted = convert_book_text("1.e4 c5 2.Nf3 d6 3.d4 cxd4\n\n2.Nc3 Nc6 3.f4").text

        self.assertEqual("1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 3.f4 ) d6 3.d4 cxd4", converted)

    def test_an_external_reader_accepts_the_converted_text(self):
        converted = convert_book_text("1.e4 c5 2.Nf3 d6 3.d4 cxd4\n\n2.Nc3 Nc6 3.f4").text

        game = external_reader(converted)

        self.assertEqual([], game.errors)
        self.assertEqual(["e4", "c5", "Nf3", "d6", "d4", "cxd4"], [node.san() for node in game.mainline()])
        self.assertEqual("Nc3", game.next().next().variations[1].san())

    def test_a_black_regression_follows_the_black_move(self):
        converted = convert_book_text("1.e4 c5 2.Nf3 d6 3.d4 cxd4\n\n2...Nc6 3.d4 cxd4").text

        self.assertEqual([], external_reader(converted).errors, converted)

    def test_the_game_we_export_does_not_change(self):
        before = "1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 3.f4 ) 2... d6 3.d4 cxd4"

        converted = convert_book_text("1.e4 c5 2.Nf3 d6 3.d4 cxd4\n\n2.Nc3 Nc6 3.f4").text

        self.assertEqual(ours(before), ours(converted))


if __name__ == "__main__":
    unittest.main()
