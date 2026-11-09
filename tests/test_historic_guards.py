"""Travas antigas que nenhum teste segurava (Q-07 / R-010 do docs/ROADMAP_V3.md).

A auditoria de 2026-11 desligou cada uma destas regras numa copia do projeto e
a suite inteira continuou verde. Aqui elas ficam fixadas *pelo comportamento*
-- o que o usuario ve ou o que a proxima etapa recebe --, para que as
refatoracoes das fases 16 e 20 nao as desfacam em silencio.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.candidates import CONFIDENCE_AUTO_APPLY, CandidateResolver
from pgn_live_editor.core.nag_table import SEARCHABLE_GLYPHS
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def board_after(*sans: str) -> chess.Board:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def parse(text: str, locale: str = "en"):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized))


def levenshtein_guesses(token: str, board: chess.Board) -> list[tuple[str, float]]:
    candidates = CandidateResolver(locale="en").resolve(token, board)
    return [(c.san, c.confidence) for c in candidates if c.source == "levenshtein"]


class LevenshteinRescueGuardTests(unittest.TestCase):
    """D4: o resgate por distancia de edicao nunca vira correcao segura."""

    def test_kd5_is_never_turned_into_d5_in_silence(self):
        ast = parse("1. e4 e5 2. Nf3 Kd5")

        flagged = [issue for issue in ast.issues if issue.raw_text == "Kd5"]

        self.assertTrue(flagged, "Kd5 foi trocado sem alerta nenhum")

    def test_changing_the_piece_caps_the_confidence(self):
        # `Kd5` -> `d5` esta a 1 caractere, mas troca o rei por um peao.
        guesses = levenshtein_guesses("Kd5", board_after("e4", "e5", "Nf3"))

        self.assertEqual("d5", guesses[0][0])
        self.assertLessEqual(guesses[0][1], 0.60)

    def test_changing_the_destination_caps_the_confidence(self):
        # `Nh7` -> `Nh6`: mesma peca, outra casa.
        guesses = levenshtein_guesses("Nh7", board_after("e4"))

        self.assertEqual("Nh6", guesses[0][0])
        self.assertLessEqual(guesses[0][1], 0.70)

    def test_no_levenshtein_guess_is_ever_applied_automatically(self):
        for token, board in (("Kd5", board_after("e4", "e5", "Nf3")), ("Nh7", board_after("e4"))):
            for _san, confidence in levenshtein_guesses(token, board):
                self.assertLess(confidence, CONFIDENCE_AUTO_APPLY, token)

    def test_a_short_token_tolerates_only_one_wrong_character(self):
        # `Qa9` esta a 2 caracteres de varios lances legais; num token de 3
        # caracteres isso "conserta" qualquer coisa.
        self.assertEqual([], levenshtein_guesses("Qa9", board_after("e4")))

    def test_a_long_token_tolerates_at_most_two_wrong_characters(self):
        # `Nf3xy` -> `Nf3` sao 2 delecoes: ainda vale como hipotese (fraca).
        self.assertEqual([("Nf3", 0.55)], levenshtein_guesses("Nf3xy", chess.Board()))
        # `Nf3xyz` -> `Nf3` sao 3: nao vale.
        self.assertEqual([], levenshtein_guesses("Nf3xyz", chess.Board()))

    def test_swapping_one_piece_for_another_caps_the_confidence(self):
        # `Qa2` -> `Ra2`: troca dama por torre, a 1 caractere e sem empate.
        board = chess.Board("rnbqkbnr/ppppp1pp/8/8/1P3p1P/P5P1/2PPPP2/RNBQKBNR w KQkq - 0 5")

        guesses = levenshtein_guesses("Qa2", board)

        self.assertEqual("Ra2", guesses[0][0])
        self.assertLessEqual(guesses[0][1], 0.60)

    def test_a_four_character_token_still_tolerates_two_wrong_characters(self):
        # O limite "curto" e de ate 3 caracteres; `Ngf4` tem 4 e esta a 2 de `Nf3`.
        self.assertTrue(levenshtein_guesses("Ngf4", chess.Board()))


class OcrRepairRuleTests(unittest.TestCase):
    """Reparos de OCR seguros: cada um tem fonte e confianca proprias."""

    def best(self, token: str, board: chess.Board, locale: str = "en") -> tuple[str, float, str]:
        candidate = CandidateResolver(locale=locale).resolve(token, board)[0]
        return candidate.san, candidate.confidence, candidate.source

    def test_a_lowercase_piece_letter_is_fixed(self):
        san, confidence, source = self.best("nf3", chess.Board())

        self.assertEqual(("Nf3", "caixa"), (san, source))
        self.assertGreaterEqual(confidence, 0.90)

    def test_a_correct_move_is_read_as_it_is(self):
        self.assertEqual(("Nf3", 1.0, "exato"), self.best("Nf3", chess.Board()))

    def test_a_known_ocr_prefix_becomes_the_piece_it_stands_for(self):
        # `tD` e como o OCR costuma ler o `C` de cavalo nos livros em portugues.
        san, _confidence, source = self.best("tDc6", board_after("e4"), locale="pt")

        self.assertEqual("Nc6", san)
        self.assertIn("ocr", source)

    def test_an_evaluation_glued_to_the_move_is_set_aside(self):
        san, confidence, source = self.best("Nf3!", chess.Board())

        self.assertEqual(("Nf3", 1.0), (san, confidence))
        self.assertIn("anota", source)


class LongestGlyphFirstTests(unittest.TestCase):
    """D20: `+-` e uma avaliacao inteira, nao xeque seguido de traco."""

    def test_plus_minus_glued_to_a_move_is_one_evaluation(self):
        ast = parse("1. e4 e5 2. Qh5+- Nc6")

        queen_move = ast.moves[2]
        self.assertEqual("Qh5", queen_move.san)
        self.assertEqual(["+-"], queen_move.nags)

    def test_minus_plus_is_one_evaluation_too(self):
        ast = parse("1. e4 e5 2. Qh5 -+ Nc6")

        self.assertEqual(["-+"], ast.moves[2].nags)
        self.assertEqual("Nc6", ast.moves[3].san)

    def test_every_glyph_of_the_table_survives_glued_to_a_move(self):
        # Hoje nenhum glifo simbolico e prefixo de outro, e por isso a ordem
        # "mais longo primeiro" ainda nao faz diferenca (a mutacao dela e
        # equivalente). Este teste e o que quebra no dia em que um glifo novo
        # se sobrepuser a outro e a ordem passar a importar.
        for glyph in SEARCHABLE_GLYPHS:
            with self.subTest(glyph=glyph):
                ast = parse(f"1. e4{glyph} e5")
                self.assertEqual("e4", ast.moves[0].san)
                self.assertEqual([glyph], ast.moves[0].nags)
                self.assertEqual("e5", ast.moves[1].san)


class UndottedMoveNumberTests(unittest.TestCase):
    """Numero sem ponto so vira numero de lance quando nao e de outra coisa."""

    def normalized(self, text: str) -> str:
        return LiveTextNormalizer(locale="en").normalize_with_mapping(text).text

    def test_a_diagram_number_is_not_a_move_number(self):
        self.assertIn("diagram 12 P-K4", self.normalized("See diagram 12 P-K4 P-K4"))

    def test_a_bare_number_before_a_move_is_a_move_number(self):
        self.assertIn("1. P-K4", self.normalized("Game 3 1 P-K4 P-K4"))

    def test_zero_is_never_a_move_number(self):
        self.assertNotIn("0. ", self.normalized("After 0 P-K4 P-K4"))

    def test_two_dots_mean_black_to_move(self):
        self.assertIn("10... Nf3", self.normalized("1. e4 e5 10.. Nf3"))


if __name__ == "__main__":
    unittest.main()
