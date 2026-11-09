"""Notacao descritiva em espanhol e ingles, e a pontuacao desses livros.

Os casos vem de livros reais usados como teste de campo (pasta
`PDF/Descritiva`): *ABC de las aperturas* (Panov), *Tactica moderna en ajedrez*
(Pachman) e *Complete Chess Strategy* (Pachman, ed. inglesa). Antes destes
testes o espanhol nao era lido (0 de 40 lances de uma pagina do Panov) e o
ingles falhava nas formas mais comuns (`N-B3`, `PxP`, `P-QB3` virava `g3`).
"""

from __future__ import annotations

import unittest

import chess

from pgn_live_editor.core.candidates import looks_like_move
from pgn_live_editor.core.issues import AMBIGUOUS_MOVE, CITED_MOVE, MOVE_NUMBER_MISMATCH
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.notation_detect import detect_notation_locale
from pgn_live_editor.core.parser import TolerantParser, iter_moves
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str, locale: str, start_fen: str = chess.STARTING_FEN):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized), start_fen=start_fen)


def sans(moves) -> list[str]:
    return [move.display_san for move in moves]


def errors(ast) -> list:
    return [issue for issue in ast.issues if issue.severity == "error"]


def board_after(*moves: str) -> str:
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return board.fen()


class SpanishDescriptiveTests(unittest.TestCase):
    def test_opening_moves_use_alfil_and_the_ad_ar_files(self):
        ast = parse("1. P4R P4R 2. C3AR C3AD 3. A4A A4A 4. P3AD", "es")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3"], sans(ast.moves))
        self.assertEqual([], errors(ast))

    def test_captures_with_the_multiplication_sign(self):
        ast = parse("1. P4R P4R 2. C3AR C3AD 3. A5C P3TD 4. A×C PD×A", "es")

        self.assertEqual(["Bxc6", "dxc6"], sans(ast.moves[-2:]))

    def test_panov_sicilian_page_with_variations(self):
        # Panov, ABC de las aperturas, p. 38 -- a pagina que abriu a analise.
        text = (
            "1. P4R P4AD 2. C3AR P3R (2. ..., C3AR 3. P5R, C4D; 4. C3A, P3R; 5. C×C, P×C) "
            "3. P4D P×P 4. C×P C3AR 5. C3AD C3A (5. ..., A5C 6. P5R, C4D; 7. A2D, C×C; 8. P×C, A2R; 9. D4C) "
            "6. C(4D)5C A5C (6. ..., P4D? 7. P×P, P×P; 8. C×P!, C×C; 9. D×C, D×D; 10. C7A+) 7. P3TD"
        )
        ast = parse(text, "es")

        self.assertEqual(
            ["e4", "c5", "Nf3", "e6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "Nc6", "Ndb5", "Bb4", "a3"],
            sans(ast.moves),
        )
        self.assertEqual(["Nf6", "e5", "Nd5", "Nc3", "e6", "Nxd5", "exd5"], sans(ast.moves[2].variations[0]))
        self.assertEqual("Nc7+", ast.moves[10].variations[0][-1].display_san)
        self.assertEqual([], errors(ast))
        self.assertTrue(all(move.is_valid for move in iter_moves(ast.moves)))

    def test_capture_naming_the_victims_square_and_announced_check(self):
        # Pachman, Tactica moderna, diagrama 95 (Duz-Khotimirsky - Kotov).
        fen = "r3r1k1/1b1nbppp/p3pn2/q2p2B1/Pp1P4/3BPN2/1P1N1PPP/2RQ1RK1 w - - 0 1"
        text = (
            "1. C3C! D×P; 2. T7AD, TD1C 3. C5R!, C1A 4. A×C!, A×A; 5. D5T, C3C "
            "6. C7D!, D×C3C; 7. C×A+, P×C; 8. T×P!, R×T 9. D×P+, R1A; 10. A×C"
        )
        ast = parse(text, "es", fen)

        self.assertEqual(
            ["Nb3", "Qxa4", "Rc7", "Rab8", "Ne5", "Nf8", "Bxf6", "Bxf6", "Qh5", "Ng6", "Nd7", "Qxb3"],
            sans(ast.moves[:12]),
        )
        self.assertEqual(["Qxh7+", "Kf8", "Bxg6"], sans(ast.moves[-3:]))
        self.assertEqual([], [issue for issue in ast.issues if issue.severity in ("error", "warning")])

    def test_spanish_book_opened_with_portuguese_locale_still_reads(self):
        ast = parse("1. P4R P4AD 2. C3AR P3R 3. A4A", "pt")

        self.assertEqual(["e4", "c5", "Nf3", "e6", "Bc4"], sans(ast.moves))

    def test_unknown_file_name_never_matches_every_file(self):
        # Antes, `AD` desconhecido em portugues virava "qualquer coluna" e
        # `P4AD` saia `h5` -- lance errado aplicado com 82% de confianca.
        ast = parse("1. P4R P4AD", "pt")

        self.assertEqual("c5", ast.moves[1].display_san)


class EnglishDescriptiveTests(unittest.TestCase):
    def test_file_then_rank_squares_without_a_wing(self):
        ast = parse("1 P-K4 P-K4 2 B-B4 P-QB3 3 Q-K2 P-Q3 4 P-QB3 P-KB4", "en")

        self.assertEqual(["e4", "e5", "Bc4", "c6", "Qe2", "d6", "c3", "f5"], sans(ast.moves))
        self.assertEqual([], errors(ast))

    def test_queens_bishop_and_knight_files_are_not_swapped(self):
        # `QB` e a coluna c e `QN` a b. A versao anterior trocava as duas.
        fen = board_after("e4", "e5")
        self.assertEqual(["c3"], sans(parse("2. P-QB3", "en", fen).moves))
        self.assertEqual(["b3"], sans(parse("2. P-QN3", "en", fen).moves))

    def test_lowercase_x_and_qualified_pawn_captures(self):
        fen = board_after("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6")
        self.assertEqual(["dxc6"], sans(parse("4... QPxB", "en", fen).moves))
        self.assertEqual(["bxc6"], sans(parse("4... NPxB", "en", fen).moves))
        self.assertEqual(["exd5"], sans(parse("1. P-K4 P-Q4 2. PxP", "en").moves[-1:]))

    def test_wing_qualified_pieces(self):
        ast = parse("1 P-Q4 P-Q4 2 P-QB4 P-K3 3 N-QB3 N-KB3 4 B-N5 QN-Q2", "en")

        self.assertEqual("Nbd7", ast.moves[-1].display_san)

    def test_brühl_philidor_1783(self):
        # Pachman, Complete Chess Strategy 1, partida 2 -- o gabarito e a partida real.
        text = (
            "1 P-K4 P-K4 2 B-B4 P-QB3 3 Q-K2 P-Q3 4 P-QB3 P-KB4 5 P-Q3 N-B3 6 PxP BxP "
            "7 P-Q4 P-K5 8 B-KN5 P-Q4 9 B-N3 B-Q3 10 N-Q2 QN-Q2 11 P-KR3 P-KR3 12 B-K3 Q-K2 "
            "13 P-KB4 P-KR4 14 P-B4 P-R3 15 PxP PxP 16 Q-B2 O-O 17 N-K2 P-QN4 18 O-O N-N3 "
            "19 N-N3 P-N3 20 QR-B1 N-B5 21 NxB PxN 22 Q-N3+ Q-KN2 23 QxQ+ KxQ 24 BxN NPxB"
        )
        ast = parse(text, "en")

        expected = (
            "e4 e5 Bc4 c6 Qe2 d6 c3 f5 d3 Nf6 exf5 Bxf5 d4 e4 Bg5 d5 Bb3 Bd6 Nd2 Nbd7 h3 h6 Be3 Qe7 "
            "f4 h5 c4 a6 cxd5 cxd5 Qf2 O-O Ne2 b5 O-O Nb6 Ng3 g6 Rac1 Nc4 Nxf5 gxf5 Qg3+ Qg7 "
            "Qxg7+ Kxg7 Bxc4 bxc4"
        ).split()
        self.assertEqual(expected, sans(ast.moves))
        self.assertEqual([], errors(ast))

    def test_ocr_misread_rank_is_repaired_but_flagged(self):
        # Brühl-Philidor, lance 7 como o OCR entregou: `P-KS` e `P-K5` (e5-e4).
        fen = board_after("e4", "e5", "Bc4", "c6", "Qe2", "d6", "c3", "f5", "d3", "Nf6", "exf5", "Bxf5", "d4")
        ast = parse("7... P-KS", "en", fen)

        self.assertEqual("e4", ast.moves[0].display_san)
        self.assertLess(ast.moves[0].confidence, 0.90)

    def test_other_ocr_damage_seen_in_the_pachman_books(self):
        # Hifen dobrado e `B` de coluna lido como `8`, tal como no texto do PDF.
        self.assertEqual(["d4"], sans(parse("1 P--Q.4", "en").moves))
        self.assertEqual(["c4"], sans(parse("1 P-Q84", "en").moves))
        self.assertEqual(["e4", "e5", "Nf3"], sans(parse("1 P-K4 P-K4 2 N-K83", "en").moves))

    def test_genuine_ambiguity_is_reported_not_guessed(self):
        # Os dois cavalos vao a uma casa `B3` e os lances seguintes nao desempatam.
        ast = parse("1 P-K4 P-K4 2 N-B3", "en")

        self.assertEqual(AMBIGUOUS_MOVE, ast.issues[-1].code)
        self.assertEqual({"Nf3", "Nc3"}, {candidate.san for candidate in ast.issues[-1].candidates})

    def test_continuation_breaks_a_descriptive_tie(self):
        # `PxP` pode ser exd5 ou cxd5; so cxd5 deixa `P-K5` legal dois lances depois.
        fen = "rnbqkbnr/ppp1pppp/8/3p4/2P1P3/8/PP1P1PPP/RNBQKBNR w KQkq - 0 3"
        ast = parse("3. PxP QxP 4. P-K5", "en", fen)

        self.assertEqual(["cxd5", "Qxd5", "e5"], sans(ast.moves))
        self.assertIn("continuação", ast.moves[0].candidates[0].source)


class BookPunctuationTests(unittest.TestCase):
    def test_undotted_move_numbers_are_not_comments(self):
        normalized = LiveTextNormalizer().normalize("1 P-K4 P-K4 2 N-KB3 N-QB3")
        self.assertEqual("1. P-K4 P-K4 2. N-KB3 N-QB3", normalized)
        self.assertEqual("1. e4 e5 2. Nf3", LiveTextNormalizer().normalize("1 e4 e5 2 Nf3"))

    def test_numbers_in_prose_stay_numbers(self):
        normalizer = LiveTextNormalizer()
        for prose in ("2 Brühl-Philidor London 1783", "diagrama 23 P4R", "ganhou 3 peoes e 2 torres", "Diagram 5 e4"):
            self.assertEqual(prose, normalizer.normalize(prose), prose)

    def test_spaced_captures_are_joined(self):
        self.assertEqual("6. PxP BxP", LiveTextNormalizer().normalize("6 P x P B x P"))

    def test_parenthesised_origin_is_not_a_variation(self):
        normalizer = LiveTextNormalizer()
        self.assertEqual("6. C/4D-5C A5C", normalizer.normalize("6. C(4D)5C A5C"))
        self.assertEqual("N/Q2-B3 R/1xP", normalizer.normalize("N(Q2)-B3 R(1)xP"))

    def test_comma_after_ellipsis_marks_a_black_move(self):
        ast = parse("1. e4 c5 2. Nf3 e6 (2. ..., Nf6 3. e5, Nd5) 3. d4", "es")

        # O parser pendura a alternativa ao lance 2... no lance anterior, com `anchor_after`.
        self.assertEqual(["Nf6", "e5", "Nd5"], sans(ast.moves[2].variations[0]))

    def test_separators_between_moves_do_not_become_comments(self):
        ast = parse("1. e4, e5; 2. Nf3, Nc6; 3. Bb5", "es")

        self.assertEqual([], [comment for move in ast.moves for comment in move.comments_after])

    def test_prose_commas_survive(self):
        ast = parse("1. e4 e5 {as brancas jogam, e ganham}", "es")

        self.assertEqual("as brancas jogam, e ganham", ast.moves[-1].comments_after[0])

    def test_move_next_to_a_parenthesis_is_not_swallowed_as_prose(self):
        ast = parse("1 P-K4 P-K4 2 N-KB3 (2 P-KB4 PxP 3 N-KB3) 2 ... N-QB3", "en")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], sans(ast.moves))
        self.assertEqual(["f4", "exf4", "Nf3"], sans(ast.moves[1].variations[0]))

    def test_descriptive_shapes_are_move_candidates(self):
        for token in ("AxC", "NPxB", "QBPxKNP", "N/Q2-B3", "C/4D-5C", "P-KS", "B-IH"):
            self.assertTrue(looks_like_move(token), token)
        for word in ("caxa", "Taxa", "UK-US"):
            self.assertFalse(looks_like_move(word), word)


class CitedMoveTests(unittest.TestCase):
    def test_threat_named_in_prose_stays_in_the_comment(self):
        fen = "r3r1k1/1b1nbppp/p3pn2/q2p2B1/Pp1P4/3BPN2/1P1N1PPP/2RQ1RK1 w - - 0 1"
        text = (
            "1. C3C! D×P; 2. T7AD, TD1C 3. C5R!, C1A "
            "(3. ..., C×C; 4. P×C, C2D; 5. A×A, T×A; 6. D2A, con la amenaza T1T) 4. A×C!"
        )
        ast = parse(text, "es", fen)

        variation = ast.moves[4].variations[0] if ast.moves[4].variations else ast.moves[5].variations[0]
        self.assertEqual("Qc2", variation[-1].display_san)
        self.assertIn("con la amenaza T1T", variation[-1].comments_after[-1])
        self.assertIn(CITED_MOVE, [issue.code for issue in ast.issues])

    def test_english_threatening_and_idea_of(self):
        for text in ("1 P-K4 P-K4 2 N-KB3 threatening NxP", "1. e4 e5 2. Nf3 com a ideia de Nxe5"):
            ast = parse(text, "en" if "threat" in text else "pt")
            self.assertEqual(3, len(ast.moves), text)

    def test_a_numbered_move_after_the_word_is_still_played(self):
        ast = parse("1. e4 e5 {a ameaça} 2. Nf3", "pt")

        self.assertEqual(["e4", "e5", "Nf3"], sans(ast.moves))


class NotationDetectionTests(unittest.TestCase):
    def test_each_family_is_recognised(self):
        cases = {
            "en": "1 P-K4 P-K4 2 N-KB3 N-QB3 3 B-N5 P-QR3 4 B-R4 N-B3 5 O-O B-K2",
            "es": "1. P4R P4R 2. C3AR C3AD 3. A5C P3TD 4. A4T C3A 5. O-O A2R",
            "pt": "1. e4 e5 2. Cf3 Cc6 3. Bb5 a6 4. Ba4 Cf6 5. O-O Be7 6. Te1 b5 7. Bb3 d6",
            "de": "1. e4 e5 2. Sf3 Sc6 3. Lb5 a6 4. La4 Sf6 5. O-O Le7",
        }
        for expected, text in cases.items():
            self.assertEqual(expected, detect_notation_locale(text), text)

    def test_english_san_is_english(self):
        self.assertEqual("en", detect_notation_locale("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7"))

    def test_no_guess_without_a_clear_signal(self):
        for text in ("1. e4 e5 2. d4 exd4 3. c3", "Cada chances Chega mate abertura 1945 C45", ""):
            self.assertIsNone(detect_notation_locale(text), text)

    def test_moves_inside_comments_do_not_count(self):
        self.assertIsNone(detect_notation_locale("{1 P-K4 P-K4 2 N-KB3 N-QB3 3 B-N5 P-QR3} 1. e4 e5"))

    def test_detached_check_sign_is_glued_to_the_move(self):
        normalizer = LiveTextNormalizer()
        self.assertEqual("3. Q-R5+ K-K2", normalizer.normalize("3 Q-R5 + K-K2"))
        self.assertEqual("as brancas + as pretas", normalizer.normalize("as brancas + as pretas"))


class MoveNumberMismatchTests(unittest.TestCase):
    def test_a_move_lost_by_ocr_is_reported_where_it_happened(self):
        # `P-K'` e ilegivel e vira prosa; o `2` impresso denuncia o buraco.
        ast = parse("1 P-K4 P-K' 2 B-B4 P-QB3", "en")

        mismatches = [issue for issue in ast.issues if issue.code == MOVE_NUMBER_MISMATCH]
        self.assertEqual(1, len(mismatches))
        self.assertEqual("2.", mismatches[0].raw_text.strip())

    def test_the_missing_move_is_proposed_when_only_one_fits(self):
        # Brühl-Philidor como o OCR entregou: `P-K'` sumiu. So `...e5` deixa
        # legal o `P-K5` do lance 7.
        text = "1 P-K4 P-K' 2 B-B4 P-QB3 3 Q-K2 P-Q3 4 P-QB3 P-KB4 5 P-Q3 N-B3 6 PxP BxP 7 P-Q4 P-K5 8 B-KN5 P-Q4"
        ast = parse(text, "en")

        mismatch = next(issue for issue in ast.issues if issue.code == MOVE_NUMBER_MISMATCH)
        self.assertEqual("e5", mismatch.best_candidate.san)
        self.assertEqual("e5 2.", mismatch.best_candidate.replacement)

    def test_no_proposal_when_many_moves_fit(self):
        ast = parse("1. e4 e5 2. Nf3 3. Bb5 a6", "en")

        mismatch = next(issue for issue in ast.issues if issue.code == MOVE_NUMBER_MISMATCH)
        self.assertEqual([], mismatch.candidates)

    def test_diagram_fen_with_a_different_move_number_is_not_a_mismatch(self):
        fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
        ast = parse("40. P4R R2D 41. R2D", "pt", fen)

        self.assertEqual([], [issue for issue in ast.issues if issue.code == MOVE_NUMBER_MISMATCH])

    def test_correct_numbering_is_silent(self):
        ast = parse("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6", "en")

        self.assertEqual([], [issue for issue in ast.issues if issue.code == MOVE_NUMBER_MISMATCH])


if __name__ == "__main__":
    unittest.main()
