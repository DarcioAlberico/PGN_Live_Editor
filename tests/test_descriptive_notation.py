from __future__ import annotations

import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.candidates import _origin_file_by_square
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str, locale: str = "pt", start_fen: str = chess.STARTING_FEN):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized), start_fen=start_fen)


class PortugueseDescriptiveNotationTests(unittest.TestCase):
    POSITION_FEN = "1qr2rk1/pb3pp1/1p3n1p/1N1pNQ2/P1PP4/1P6/6PP/1R1R2K1 w - - 1 24"

    def test_user_fen_selects_queen_rook_then_resolves_knight_move(self):
        rook = parse("24. TD1B", start_fen=self.POSITION_FEN)
        knight = parse("24. C3BD", start_fen=self.POSITION_FEN)

        self.assertEqual(["Rbc1"], [move.display_san for move in rook.moves])
        self.assertEqual(["Nc3"], [move.display_san for move in knight.moves])
        self.assertTrue(rook.moves[0].is_valid)
        self.assertTrue(knight.moves[0].is_valid)

    def test_rook_and_knight_disambiguation_uses_the_full_game_history(self):
        text = (
            "1. P4D C3BR 2. P4BD P3R 3. C3BD B5C 4. P3R P4D "
            "5. P3TD B2R 6. C3B 0-0 7. B3D P3CD 8. 0-0 P4B "
            "9. D2R C3B 10. T1D PBXP 11. PRXP B3T 12. P3CD T1B "
            "13. T1C D2B 14. C5CD D1C 15. B5C P3T 16. B4T C4TR "
            "17. BXB CXB 18. C5R C3BR 19. P4TD C3B 20. P4B C5CD "
            "21. P5BR CXB 22. DXC PRXP 23. DXP B2C "
            "24. TD1B P3T 25. C3BD D3D 26. P5T PDXP "
            "27. PCXP P4CD 28. P5B"
        )

        ast = parse(text)

        self.assertEqual(self.POSITION_FEN, ast.moves[46].parent_fen)
        self.assertEqual(
            ["Rbc1", "a6", "Nc3", "Qd6", "a5", "dxc4", "bxc4", "b5", "c5"],
            [move.display_san for move in ast.moves[-9:]],
        )
        self.assertTrue(all(move.is_valid for move in ast.moves))
        self.assertFalse([issue for issue in ast.issues if issue.severity == "error"])

    def test_capture_can_name_the_pawns_file_without_a_rank(self):
        # Position after 27... P4CD; the variation continues 28. PXP PXP.
        fen = "2r2rk1/1b3pp1/p2q1n1p/Pp2NQ2/2PP4/2N5/6PP/2RR2K1 w - - 0 28"
        ast = parse("28. PXP PXP 29. CXPC", start_fen=fen)

        self.assertEqual(["cxb5", "axb5", "Nxb5"], [move.display_san for move in ast.moves])
        self.assertTrue(all(move.is_valid for move in ast.moves))
        self.assertFalse([issue for issue in ast.issues if issue.severity == "error"])

    def test_annotated_piece_capture_without_destination_is_kept_as_a_move(self):
        fen = "4k3/8/2n5/1B6/8/8/8/4K3 w - - 0 1"
        ast = parse("1. BXC!", start_fen=fen)

        self.assertEqual(["Bxc6+"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_long_descriptive_move_filters_origin_and_destination(self):
        fen = "6k1/ppr3p1/2n1br1p/PR1p1p2/3P4/2B2N2/2PK1PPP/4R3 w - - 2 21"
        ast = parse("21. T1R-1CD", start_fen=fen)

        self.assertEqual(["Reb1"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_long_descriptive_capture_filters_origin(self):
        fen = "4k3/8/4Rr2/8/8/8/8/4K3 b - - 0 1"
        ast = parse("1... T3BXT", start_fen=fen)

        self.assertEqual(["Rxe6+"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_long_descriptive_move_can_qualify_only_the_origin_rank(self):
        fen = "1n2k3/8/8/8/8/8/8/4K3 b - - 0 1"
        ast = parse("1... C1-3B", start_fen=fen)

        self.assertEqual(["Nc6"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_ocr_eight_can_stand_for_bishop_file_in_long_notation(self):
        fen = "1n2k3/8/8/8/8/8/8/4K3 b - - 0 1"
        ast = parse("1... C1-38", start_fen=fen)

        self.assertEqual(["Nc6"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_unique_two_ply_legal_continuation_repairs_an_obvious_san_misprint(self):
        fen = "2r2rk1/1b3pp1/pp1q1n1p/P2pNQ2/2PP4/1PN5/6PP/2RR2K1 b - - 0 26"
        ast = parse("26... bxa5 27. bxc4 b5", start_fen=fen)

        self.assertEqual(["dxc4", "bxc4", "b5"], [move.display_san for move in ast.moves])
        self.assertTrue(all(move.is_valid for move in ast.moves))
        self.assertEqual("AUTO_CORRECTED", ast.issues[0].code)

    def test_sample_opening_row_is_recognized_without_rewriting_algebraic_san(self):
        ast = parse("1. P4D C3BR 2. P4BD P3R 3. C3BD B5C 4. P3R P4D 5. P3TD B2R")

        self.assertEqual(
            ["d4", "Nf6", "c4", "e6", "Nc3", "Bb4", "e3", "d5", "a3", "Be7"],
            [move.display_san for move in ast.moves],
        )
        self.assertTrue(all(move.is_valid for move in ast.moves))

    def test_portuguese_descriptive_notation_is_detected_with_english_preference(self):
        ast = parse("1. P4D C3BR", locale="en")

        self.assertEqual(["d4", "Nf6"], [move.display_san for move in ast.moves])
        self.assertTrue(all(move.is_valid for move in ast.moves))

    def test_pawn_move_and_capture_resolve_against_position(self):
        ast = parse("1. P4R d5 2. PXP")

        self.assertEqual(["e4", "d5", "exd5"], [move.display_san for move in ast.moves])
        self.assertTrue(all(move.is_valid for move in ast.moves))
        self.assertEqual("descritiva", ast.moves[0].candidates[0].source)

    def test_piece_destination_and_capture_piece_are_resolved(self):
        ast = parse("1. P4R P4R 2. C3BR C3BD")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.display_san for move in ast.moves])
        self.assertTrue(all(move.is_valid for move in ast.moves))

    def test_portuguese_side_qualified_file_names_resolve(self):
        ast = parse("1. P4BD")

        self.assertEqual(["c4"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_cr5r_selects_the_kingside_knight_when_both_knights_can_move(self):
        fen = "4k3/8/3n1n2/8/8/8/8/4K3 b - - 0 1"
        ast = parse("1... CR5R", start_fen=fen)

        self.assertEqual(["Nfe4"], [move.display_san for move in ast.moves])
        self.assertTrue(ast.moves[0].is_valid)

    def test_side_qualified_rook_follows_its_original_identity(self):
        board = chess.Board()
        board.push_san("Nf3")
        file_by_square = _origin_file_by_square(board)

        self.assertEqual(chess.FILE_NAMES.index("g"), file_by_square[chess.F3])

    def test_side_qualified_rook_identity_survives_castling(self):
        board = chess.Board()
        for san in ("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "O-O"):
            board.push_san(san)
        file_by_square = _origin_file_by_square(board)

        self.assertEqual(chess.FILE_NAMES.index("h"), file_by_square[chess.F1])

    def test_descriptive_variation_and_algebraic_mainline_can_mix(self):
        ast = parse("1. d4 d5 2. c4 e6 3. Nc3 c6 4. Nf3 (4. P4R, PXP; 5. CXP)")

        self.assertEqual(["d4", "d5", "c4", "e6", "Nc3", "c6", "Nf3"], [move.display_san for move in ast.moves])
        self.assertEqual(["e4", "dxe4", "Nxe4"], [move.display_san for move in ast.moves[5].variations[0]])
        self.assertEqual([], [issue for issue in ast.issues if issue.severity == "error"])

    def test_english_descriptive_piece_letters_are_supported(self):
        ast = parse("1. P-K4 P-K4 2. N-KB3", locale="en")

        self.assertEqual(["e4", "e5", "Nf3"], [move.display_san for move in ast.moves])

    def test_prose_with_move_like_words_is_not_rewritten(self):
        ast = parse("1. e4 e5 {a expressão PXP aparece no comentário e deve ficar intacta}")
        self.assertEqual("a expressão PXP aparece no comentário e deve ficar intacta", ast.moves[-1].comments_after[0])


if __name__ == "__main__":
    unittest.main()
