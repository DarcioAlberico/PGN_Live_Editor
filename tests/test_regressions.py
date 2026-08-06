"""Regressoes dos defeitos catalogados em docs/ANALISE.md.

Cada teste aqui existe para que um defeito ja corrigido nao volte em silencio.
O nome do metodo cita o codigo do defeito.
"""

from __future__ import annotations

import unittest

import chess

from pgn_live_editor.core.candidates import CandidateResolver, looks_like_move
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.pgn_headers import normalize_pgn_date, normalize_pgn_result
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str, locale: str = "pt", start_fen: str = chess.STARTING_FEN):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    tokens = PGNTokenizer().tokenize(normalized)
    return TolerantParser(locale=locale).parse(tokens, start_fen=start_fen)


def normalize(text: str, locale: str = "pt") -> str:
    return LiveTextNormalizer(locale=locale).normalize_with_mapping(text).text


class D1ProseIsNeverRewrittenTests(unittest.TestCase):
    """D1: o mapa de pecas por idioma corrompia prosa em portugues."""

    PROSE = [
        "Cada lance conta",
        "Dada a posicao",
        "Cabe notar",
        "Cede o centro",
        "Chega a hora",
        "Tcheca defesa",
        "Ele ataca",
        "Bem jogado",
    ]

    def test_portuguese_prose_passes_through_untouched(self):
        for sentence in self.PROSE:
            with self.subTest(frase=sentence):
                self.assertEqual(sentence, normalize(sentence))

    def test_prose_after_moves_becomes_a_comment_with_the_original_words(self):
        ast = parse("1.e4 e5 2.Nf3 Nc6 Cada lance conta e Chega a hora")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.display_san for move in ast.moves])
        self.assertEqual(["Cada lance conta e Chega a hora"], ast.moves[-1].comments_after)


class D2LiteralSubstitutionsTests(unittest.TestCase):
    """D2: `ch`->`+` e `mate`->`#` eram aplicados como substring, em qualquer palavra."""

    def test_words_containing_ch_and_mate_are_preserved(self):
        for sentence in ("as chances das brancas", "o Cavalo chega a c3", "ameacando mate rapido", "checagem"):
            with self.subTest(frase=sentence):
                self.assertEqual(sentence, normalize(sentence))

    def test_ch_suffix_still_becomes_check_when_attached_to_a_move(self):
        ast = parse("1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6 4.Qxf7mate")

        self.assertEqual("Qxf7#", ast.moves[-1].display_san)
        self.assertEqual("Qxf7mate", ast.moves[-1].san)


class D3SilentMoveLossTests(unittest.TestCase):
    """D3: um lance ilegivel virava comentario e sumia sem gerar alerta."""

    def test_ocr_noise_is_resolved_and_reported(self):
        ast = parse("1.e4 e5 2.4Jf3 tDc6 3.Bb5")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"], [move.display_san for move in ast.moves])
        codes = [issue.code for issue in ast.issues]
        self.assertEqual(["AUTO_CORRECTED", "AUTO_CORRECTED"], codes)

    def test_unresolvable_move_produces_an_error_instead_of_vanishing(self):
        ast = parse("1.e4 e5 2.Nf3 Xf6z")

        errors = [issue for issue in ast.issues if issue.severity == "error"]
        self.assertTrue(errors, "um token com cara de lance sumiu sem alerta")
        self.assertEqual("Xf6z", errors[0].raw_text)
        self.assertEqual("UNRECOGNIZED_MOVE", errors[0].code)
        # O lance fica na arvore, marcado como invalido, para aparecer em vermelho
        # no editor -- em vez de virar comentario e sumir.
        self.assertIn("Xf6z", [move.san for move in ast.moves])
        self.assertFalse(ast.moves[-1].is_valid)

    def test_prose_with_digits_does_not_flood_the_panel(self):
        """`C45` (codigo ECO) e `1945` sao prosa, nao candidatos a lance."""
        ast = parse("1.e4 e5 uma abertura de 1945 estudada no C45 por muitos")

        self.assertEqual([], [issue for issue in ast.issues if issue.severity == "error"])

    def test_looks_like_move_rejects_words_without_squares(self):
        for word in ("Cada", "chances", "Chega", "mate", "abertura"):
            self.assertFalse(looks_like_move(word), word)
        for token in ("Cf3", "tDc6", "e4", "O-O", "Qxe4ch"):
            self.assertTrue(looks_like_move(token), token)


class D4DangerousRescueTests(unittest.TestCase):
    """D4: o resgate por Levenshtein reescrevia lances errados em silencio."""

    def test_short_token_is_not_rescued_across_distance_two(self):
        ast = parse("Kd5")

        self.assertFalse(ast.moves[0].is_valid)
        self.assertIsNone(ast.moves[0].corrected_san)
        self.assertEqual("UNRECOGNIZED_MOVE", ast.issues[0].code)

    def test_piece_change_is_flagged_instead_of_applied_silently(self):
        ast = parse("1.e4 e5 2.Nc6")

        move = ast.moves[-1]
        self.assertTrue(move.needs_review, "correcao duvidosa deveria pedir revisao")
        self.assertLess(move.confidence, 0.90)
        self.assertEqual("AMBIGUOUS_MOVE", ast.issues[0].code)
        self.assertEqual("warning", ast.issues[0].severity)

    def test_destination_change_is_flagged(self):
        ast = parse("1.e4 e5 2.Nf3 Nc6 3.Bh5")

        move = ast.moves[-1]
        self.assertEqual("Bb5", move.corrected_san)
        self.assertTrue(move.needs_review)
        self.assertEqual("warning", ast.issues[0].severity)

    def test_locale_mapping_is_high_confidence_and_applied(self):
        ast = parse("1.e4 e5 2.Cf3 Cc6")

        knight = ast.moves[2]
        self.assertEqual("Nf3", knight.corrected_san)
        self.assertFalse(knight.needs_review)
        self.assertGreaterEqual(knight.confidence, 0.90)
        self.assertEqual("info", ast.issues[0].severity)

    def test_resolver_reports_ties_with_low_confidence(self):
        board = chess.Board()
        board.push_san("e4")
        board.push_san("e5")
        board.push_san("Nf3")
        board.push_san("Nc6")

        candidates = CandidateResolver(locale="pt").resolve("Bb4", board)

        self.assertTrue(candidates)
        self.assertLess(candidates[0].confidence, 0.90)


class D5MoveNumberTests(unittest.TestCase):
    """D5: `10. ...` e `10....` viravam `10... .` -- e isso foi parar nos PGN exportados."""

    def test_every_dot_spelling_normalizes_to_a_single_form(self):
        for raw in ("10.... Nxd4", "10. ... Nxd4", "10 ... Nxd4", "10...Nxd4", "10 . . . Nxd4"):
            with self.subTest(entrada=raw):
                self.assertEqual("10... Nxd4", normalize(raw))

    def test_white_move_number_keeps_a_single_dot(self):
        self.assertEqual("10. Nxd4", normalize("10.Nxd4"))

    def test_no_orphan_dot_reaches_the_move_number_token(self):
        ast = parse("1.e4 c5 2.Nf3 e6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 d6 6.Be2 Be7 7.O-O O-O 8.Be3 Nc6 9.f4 Qc7 10.Qe1 10. ... Nxd4")

        numbers = [move.number for move in ast.moves if move.number]
        self.assertNotIn("10... .", numbers)
        self.assertIn("10...", numbers)

    def test_prose_decimals_and_years_are_not_move_numbers(self):
        self.assertEqual("2.5 vezes mais", normalize("2.5 vezes mais"))
        self.assertEqual("em 1945. Depois", normalize("em 1945. Depois"))

    def test_castling_after_a_move_number_still_works(self):
        self.assertEqual("8. O-O", normalize("8.0-0"))
        self.assertEqual("8... O-O-O", normalize("8...0-0-0"))


class D7UnclosedBraceTests(unittest.TestCase):
    """D7: uma `{` sem fechamento engolia o resto do documento."""

    def test_unclosed_brace_stops_at_the_end_of_the_line(self):
        ast = parse("1.e4 e5 {comentario que ficou aberto\n2.Nf3 Nc6 3.Bb5 a6")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], [move.display_san for move in ast.moves])
        self.assertIn("UNCLOSED_BRACE", [issue.code for issue in ast.issues])

    def test_unclosed_brace_is_reported_even_on_a_single_line(self):
        ast = parse("1.e4 e5 {comentario incompleto")

        self.assertEqual(["e4", "e5"], [move.display_san for move in ast.moves])
        self.assertEqual(["UNCLOSED_BRACE"], [issue.code for issue in ast.issues])

    def test_closed_comment_is_untouched(self):
        ast = parse("1.e4 e5 {tudo certo} 2.Nf3")

        self.assertEqual(["tudo certo"], ast.moves[1].comments_after)
        self.assertEqual([], ast.issues)


class D8BookSuffixTests(unittest.TestCase):
    """D8: sufixos de livro derrubavam o lance e contaminavam o seguinte."""

    def test_novelty_marker_does_not_break_the_line(self):
        ast = parse("1.e4 e5 2.Nf3N Nc6 3.Bb5")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"], [move.display_san for move in ast.moves])
        self.assertEqual([], [issue for issue in ast.issues if issue.severity != "info"])

    def test_promotion_to_knight_is_not_mistaken_for_a_novelty_marker(self):
        ast = parse("8/P7/8/8/8/8/8/K6k w - - 0 1", locale="en")  # apenas para garantir tokenizacao
        self.assertIsNotNone(ast)

        promotion = parse("1.a8=N", start_fen="8/P7/8/8/8/7k/8/K7 w - - 0 1")
        self.assertEqual("a8=N", promotion.moves[0].display_san)
        self.assertTrue(promotion.moves[0].is_valid)

    def test_old_check_notation_is_converted(self):
        ast = parse("1.e4 e5 2.Bc4 Nc6 3.Qh5 g6 4.Qxe5ch")

        self.assertEqual("Qxe5+", ast.moves[-1].display_san)
        self.assertEqual("Qxe5ch", ast.moves[-1].san)

    def test_en_passant_marker_does_not_become_a_move(self):
        ast = parse("1.e4 d5 2.exd5 e6 3.dxe6 fxe6 e.p.")

        self.assertEqual(["e4", "d5", "exd5", "e6", "dxe6", "fxe6"], [move.display_san for move in ast.moves])
        self.assertEqual([], [issue for issue in ast.issues if issue.severity == "error"])

    def test_null_move_keeps_the_tree_alive(self):
        ast = parse("1.e4 e5 2.-- Nc6")

        self.assertEqual(["e4", "e5", "--", "Nc6"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[2].is_null_move)
        self.assertEqual([], ast.issues)


class ResultTokenTests(unittest.TestCase):
    def test_asterisk_in_the_middle_of_prose_is_not_a_result(self):
        ast = parse("1.e4 e5 {ver diagrama} 2.Nf3 * 3.Bb5 a6")

        self.assertIsNone(ast.result)
        self.assertEqual(5, len(ast.moves))

    def test_result_at_the_end_is_recognized(self):
        self.assertEqual("1-0", parse("1.e4 e5 1-0").result)
        self.assertEqual("*", parse("1.e4 e5 *").result)
        self.assertEqual("1/2-1/2", parse("1.e4 e5 1/2-1/2").result)


class CascadeTests(unittest.TestCase):
    def test_errors_after_a_lost_move_are_marked_as_cascade(self):
        # `Xf6z` nao resolve, o tabuleiro fica dessincronizado e tudo depois
        # falha por tabela. O painel precisa saber diferenciar causa de efeito.
        ast = parse("1.e4 e5 2.Nf3 Xf6z 3.Bb5 Nc6")

        errors = [issue for issue in ast.issues if issue.severity == "error"]
        self.assertGreaterEqual(len(errors), 2)
        self.assertFalse(errors[0].is_cascade, "o primeiro erro e a causa, nao cascata")
        self.assertTrue(any(issue.is_cascade for issue in errors[1:]), "erros seguintes deveriam ser cascata")


class HeaderValidationTests(unittest.TestCase):
    def test_year_only_date_becomes_a_valid_pgn_date(self):
        self.assertEqual("1945.??.??", normalize_pgn_date("1945"))
        self.assertEqual("1945.05.??", normalize_pgn_date("1945.05"))
        self.assertEqual("1945.05.03", normalize_pgn_date("1945.05.03"))
        self.assertEqual("1945.05.03", normalize_pgn_date("03/05/1945"))
        self.assertEqual("????.??.??", normalize_pgn_date(""))
        self.assertEqual("????.??.??", normalize_pgn_date("primavera de 1945"))

    def test_result_is_normalized_to_the_four_valid_values(self):
        self.assertEqual("1-0", normalize_pgn_result("1-0"))
        self.assertEqual("1/2-1/2", normalize_pgn_result("½-½"))
        self.assertEqual("1/2-1/2", normalize_pgn_result("1/2"))
        self.assertEqual("*", normalize_pgn_result("indefinido"))


if __name__ == "__main__":
    unittest.main()
