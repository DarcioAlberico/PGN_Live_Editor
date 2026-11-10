"""Notacao descritiva: caixa mista e historico atraves de variantes (R-207).

L-01: a guarda de caixa olhava o token com os sufixos ainda colados, e
recusava formas que o proprio modulo entende -- `Kt-KB3`, `Q-R5ch`,
`Q-K2dis.ch`, `PxPe.p.`.

L-02: ao entrar e sair de uma variante o parser recriava o tabuleiro so pelo
FEN, o que zera o historico de lances. A descritiva precisa dele para saber
qual cavalo e "do rei" e qual foi o ultimo lance: inserir uma variante antes
de `7.KN-B3` trocava `Nc3` por `Nf3` -- com confianca total.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.descriptive import descriptive_candidates
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


def board_after(*sans: str) -> chess.Board:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


class MixedCaseTests(unittest.TestCase):
    def test_old_english_knight_and_check_suffixes_are_read(self):
        cases = {
            "Kt-KB3": (board_after("e4", "e5"), "Nf3"),
            "Q-R5ch": (board_after("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5", "exd5", "Na5"), None),
            "N-B3ch": (board_after("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "Ng5", "Nf6"), None),
            "PxPe.p.": (board_after("e4", "Nf6", "e5", "d5"), "exd6"),
        }
        for token, (board, expected) in cases.items():
            with self.subTest(token=token):
                found = [c.san for c in descriptive_candidates(token, board, "en")]
                self.assertTrue(found, f"{token} nao foi lido")
                if expected:
                    self.assertIn(expected, found)

    def test_the_whole_pipeline_reads_them_without_alerts(self):
        # O `ch` do texto e engano do "livro" (Nxf7 nao da xeque): o lance vale assim mesmo.
        ast = parse("1.P-K4 P-K4 2.Kt-KB3 Kt-QB3 3.B-B4 B-B4 4.Kt-KKt5 Kt-B3 5.KtxBPch K-K2")

        self.assertEqual(
            ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "Ng5", "Nf6", "Nxf7", "Ke7"], [m.display_san for m in ast.moves]
        )
        self.assertEqual([], [i.code for i in ast.issues if i.severity == "error"])

    def test_lowercase_san_is_still_not_descriptive(self):
        self.assertEqual([], descriptive_candidates("d4", chess.Board(), "pt"))


class HistoryThroughVariationsTests(unittest.TestCase):
    # Os dois cavalos brancos terminam em b5 e g5: so o historico diz que o "do
    # rei" (o que saiu de g1) e o de b5 -- entao `KN-B3` e `Nc3`.
    WINGS = "1.N-QB3 N-QB3 2.N-KB3 N-KB3 3.N-K4 N-K4 4.N-Q4 N-Q4 5.N-KN5 N-KN5 6.N-QN5 N-QN5 "

    def last_move(self, text: str) -> str:
        return parse(text).moves[-1].display_san

    def test_a_variation_before_the_move_does_not_change_which_knight_moves(self):
        self.assertEqual("Nc3", self.last_move(self.WINGS + "7.KN-B3"))
        self.assertEqual("Nc3", self.last_move(self.WINGS + "(6...P-K3) 7.KN-B3"))

    def test_a_variation_before_the_move_does_not_change_the_recapture(self):
        self.assertEqual(self.last_move("1.P-K4 P-Q4 2.P-QB4 PxP"), self.last_move("1.P-K4 P-Q4 2.P-QB4 (2.P-Q3) PxP"))


if __name__ == "__main__":
    unittest.main()
