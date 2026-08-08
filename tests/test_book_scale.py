"""Fase 4: o documento e um livro, nao uma partida.

Cobre a divisao em partidas (D6), a posicao inicial por fragmento (D10), a
analise incremental por segmento e a exportacao em lote.
"""

from __future__ import annotations

import io
import os
import unittest

import chess
import chess.pgn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.core.fen_tools import (  # noqa: E402
    DIAGRAM_MARK,
    clear_start_fen,
    find_diagram_marks,
    normalize_diagram_marks,
    set_start_fen,
)
from pgn_live_editor.core.game_splitter import (  # noqa: E402
    build_segment_title,
    find_segment_at,
    split_games,
)
from pgn_live_editor.core.text_ops import apply_edits  # noqa: E402
from pgn_live_editor.services.export_service import (  # noqa: E402
    build_book_pgn,
    game_file_name,
)
from pgn_live_editor.services.parse_worker import (  # noqa: E402
    AsyncParser,
    ParseConfig,
    ParsePipeline,
    build_games_for_export,
)

_app = QApplication.instance() or QApplication([])

GAME_ONE = (
    '[Event "Torneio de Moscou"]\n'
    '[Site "Moscou"]\n'
    '[Date "1945"]\n'
    '[White "Smyslov"]\n'
    '[Black "Rudakovsky"]\n'
    '[Result "1-0"]\n'
    '\n'
    '1.e4 c5 2.Nf3 e6 1-0\n'
)

GAME_TWO = (
    '[Event "Torneio de Moscou"]\n'
    '[Site "Moscou"]\n'
    '[Date "1945.10.02"]\n'
    '[White "Botvinnik"]\n'
    '[Black "Keres"]\n'
    '[Result "1/2-1/2"]\n'
    '\n'
    '1.d4 Nf6 2.c4 e6 (2... g6 3.Nc3) 1/2-1/2\n'
)

FRAGMENT = (
    '=== Estudo 3 ===\n'
    '[FEN "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"]\n'
    '[SetUp "1"]\n'
    '\n'
    '1.e4 Kf6 2.e5+ Kf5 *\n'
)

BOOK = f"{GAME_ONE}\n{GAME_TWO}\n{FRAGMENT}"


class GameSplitterTests(unittest.TestCase):
    def test_a_document_without_headers_is_a_single_game(self):
        segments = split_games("1.e4 e5 2.Nf3 Nc6")

        self.assertEqual(1, len(segments))
        self.assertEqual("1.e4 e5 2.Nf3 Nc6", segments[0].body)
        self.assertEqual(0, segments[0].raw_offset)

    def test_empty_text_still_yields_one_segment(self):
        segments = split_games("")

        self.assertEqual(1, len(segments))
        self.assertFalse(segments[0].has_content)

    def test_headers_after_a_blank_line_start_a_new_game(self):
        segments = split_games(f"{GAME_ONE}\n{GAME_TWO}")

        self.assertEqual(2, len(segments))
        self.assertEqual("Smyslov", segments[0].headers["White"])
        self.assertEqual("Botvinnik", segments[1].headers["White"])

    def test_a_missing_blank_line_between_games_still_splits(self):
        """Dois `.pgn` colados a mao nao trazem a linha em branco do padrao.

        Fundir as duas partidas produziria lance ilegal na primeira -- o pior
        desfecho possivel para este programa.
        """
        glued = "1.e4 c5 2.Nf3 e6 1-0\n" + GAME_TWO

        segments = split_games(glued)

        self.assertEqual(2, len(segments))
        self.assertEqual("Botvinnik", segments[1].headers["White"])

    def test_a_blank_line_inside_the_header_block_does_not_split_the_game(self):
        """PGN malformado de OCR: a partida nao pode ser partida ao meio."""
        malformed = '[Event "x"]\n\n[White "A"]\n[Black "B"]\n\n1.e4 e5\n'

        segments = split_games(malformed)

        self.assertEqual(1, len(segments))
        self.assertEqual("A", segments[0].headers["White"])

    def test_the_separator_splits_text_that_has_no_headers_yet(self):
        segments = split_games("1.e4 e5\n\n=== Partida 2 ===\n\n1.d4 d5")

        self.assertEqual(2, len(segments))
        self.assertEqual("1.d4 d5", segments[1].body.strip())

    def test_the_separator_line_stays_out_of_the_body(self):
        """`===` no corpo viraria tres simbolos de avaliacao no tokenizador."""
        segments = split_games("1.e4 e5\n\n=== Partida 2 ===\n\n1.d4 d5")

        self.assertNotIn("=", segments[1].body)

    def test_segments_tile_the_whole_document(self):
        segments = split_games(BOOK)

        self.assertEqual(0, segments[0].start_offset)
        self.assertEqual(len(BOOK), segments[-1].end_offset)
        for current, following in zip(segments, segments[1:], strict=False):
            self.assertEqual(current.end_offset, following.start_offset)

    def test_raw_offset_points_exactly_at_the_body(self):
        for segment in split_games(BOOK):
            with self.subTest(partida=segment.index):
                self.assertEqual(segment.body, BOOK[segment.raw_offset:segment.body_end_offset])

    def test_content_offset_skips_the_separator_but_keeps_the_headers(self):
        fragment = split_games(BOOK)[2]

        self.assertTrue(BOOK[fragment.content_offset:].startswith('[FEN '))

    def test_title_comes_from_the_headers(self):
        self.assertEqual(
            "13. Smyslov - Rudakovsky, Moscou 1945",
            build_segment_title(12, {"White": "Smyslov", "Black": "Rudakovsky", "Site": "Moscou", "Date": "1945.??.??"}, ""),
        )

    def test_title_ignores_placeholder_headers(self):
        self.assertEqual("1. Evento", build_segment_title(0, {"Event": "Evento", "Site": "?", "Date": "????.??.??"}, ""))

    def test_title_falls_back_to_the_first_line_of_the_body(self):
        self.assertEqual("1. Defesa Siciliana", build_segment_title(0, {}, "{Defesa Siciliana}\n1.e4 c5"))

    def test_find_segment_at_locates_the_game_under_the_cursor(self):
        segments = split_games(BOOK)

        self.assertEqual(0, find_segment_at(segments, BOOK.index("Smyslov")).index)
        self.assertEqual(1, find_segment_at(segments, BOOK.index("Keres")).index)
        self.assertEqual(2, find_segment_at(segments, BOOK.index("Kf6")).index)
        # Fora dos limites, o segmento mais proximo -- nunca `None`.
        self.assertEqual(2, find_segment_at(segments, len(BOOK) + 50).index)


class DiagramMarkTests(unittest.TestCase):
    def test_book_diagram_markers_are_recognized(self):
        text = "1.e4 e5 [D] 2.Nf3 (diagrama) Nc6 {[#]} 3.Bb5 [Diagram] a6"

        self.assertEqual(["[D]", "(diagrama)", "{[#]}", "[Diagram]"], [mark.raw for mark in find_diagram_marks(text)])

    def test_markers_converge_on_the_chessbase_form(self):
        text = "1.e4 e5 [D] 2.Nf3 (diagrama) Nc6"

        result = normalize_diagram_marks(text)

        self.assertEqual(f"1.e4 e5 {DIAGRAM_MARK} 2.Nf3 {DIAGRAM_MARK} Nc6", apply_edits(text, result.edits))

    def test_a_canonical_marker_is_left_alone(self):
        self.assertFalse(normalize_diagram_marks(f"1.e4 {DIAGRAM_MARK} e5").ok)

    def test_a_pgn_header_is_not_a_diagram(self):
        self.assertEqual([], find_diagram_marks('[Event "Torneio D"]\n[Site "?"]'))


class StartFenTests(unittest.TestCase):
    def test_setting_the_start_position_creates_the_header_block(self):
        text = "1.e4 e5 2.Nf3"
        fen = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"

        updated = apply_edits(text, set_start_fen(text, fen).edits)

        self.assertIn(f'[FEN "{fen}"]', updated)
        self.assertIn('[SetUp "1"]', updated)
        self.assertTrue(updated.endswith("1.e4 e5 2.Nf3"))

    def test_setting_the_start_position_updates_an_existing_block(self):
        text = '[Event "x"]\n[White "A"]\n\n1.e4 e5'
        fen = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"

        updated = apply_edits(text, set_start_fen(text, fen).edits)

        self.assertIn('[Event "x"]', updated)
        self.assertIn(f'[FEN "{fen}"]', updated)
        self.assertEqual(1, updated.count("[SetUp"))

    def test_only_the_game_under_the_cursor_gets_the_fen(self):
        fen = "4k3/8/4K3/8/8/8/4R3/8 w - - 0 1"
        segments = split_games(BOOK)
        second = segments[1]

        updated = apply_edits(
            BOOK,
            set_start_fen(BOOK, fen, second.content_offset, second.end_offset).edits,
        )

        self.assertEqual(1, updated.count(f'[FEN "{fen}"]'))
        # A FEN entrou no bloco da segunda partida, depois de Botvinnik.
        self.assertGreater(updated.index("[FEN"), updated.index("Botvinnik"))
        self.assertIn('[White "Smyslov"]', updated)

    def test_the_initial_position_removes_fen_and_setup(self):
        text = '[FEN "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"]\n[SetUp "1"]\n\n1.e4 Kf6'

        updated = apply_edits(text, set_start_fen(text, chess.STARTING_FEN).edits)

        self.assertNotIn("[FEN", updated)
        self.assertNotIn("[SetUp", updated)
        self.assertIn("1.e4 Kf6", updated)

    def test_clearing_a_document_without_fen_does_nothing(self):
        self.assertFalse(clear_start_fen("1.e4 e5").ok)

    def test_an_invalid_fen_is_refused(self):
        result = set_start_fen("1.e4 e5", "isto nao e um fen")

        self.assertFalse(result.ok)
        self.assertIn("invalida", result.message)


class ParsePipelineTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = ParsePipeline(ParseConfig.build())

    def test_only_the_game_under_the_cursor_is_analyzed(self):
        analysis = self.pipeline.analyze(BOOK, BOOK.index("Keres"))

        self.assertEqual(3, analysis.game_count)
        self.assertEqual(1, analysis.active_index)
        self.assertEqual(1, len(analysis.digests), "as demais partidas nao deviam ter sido analisadas")
        self.assertEqual(["d4", "Nf6", "c4", "e6"], [move.display_san for move in analysis.ast.moves])

    def test_move_spans_are_absolute_in_the_whole_document(self):
        analysis = self.pipeline.analyze(BOOK, BOOK.index("Keres"))
        first_move = analysis.ast.moves[0]

        self.assertEqual("d4", BOOK[first_move.san_start_index:first_move.san_end_index])

    def test_each_game_keeps_its_own_start_position(self):
        analyses = self.pipeline.analyze_all(BOOK)

        self.assertEqual(chess.STARTING_FEN, analyses[0].start_fen)
        self.assertEqual("8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", analyses[2].start_fen)
        self.assertTrue(all(move.is_valid for move in analyses[2].ast.moves))

    def test_the_header_alert_points_at_the_game_that_caused_it(self):
        analyses = self.pipeline.analyze_all(BOOK)

        issue = next(issue for issue in analyses[0].ast.issues if issue.code == "INVALID_DATE")
        self.assertEqual('[Date "1945"]', BOOK[issue.raw_start:issue.raw_end])
        self.assertEqual([], [issue for issue in analyses[1].ast.issues if issue.code == "INVALID_DATE"])

    def test_untouched_games_come_from_the_cache(self):
        first_pass = self.pipeline.analyze_all(BOOK)

        second_pass = self.pipeline.analyze_all(BOOK)

        for before, after in zip(first_pass, second_pass, strict=True):
            self.assertIs(before, after, "a partida foi reanalisada sem ter mudado")

    def test_editing_one_game_does_not_reanalyze_the_ones_before_it(self):
        first_pass = self.pipeline.analyze_all(BOOK)

        edited = BOOK.replace("1.e4 Kf6 2.e5+ Kf5 *", "1.e4 Kf6 2.e5+ Kf5 3.Kd2 *")
        second_pass = self.pipeline.analyze_all(edited)

        self.assertIs(first_pass[0], second_pass[0])
        self.assertIs(first_pass[1], second_pass[1])
        self.assertIsNot(first_pass[2], second_pass[2])
        self.assertEqual(5, second_pass[2].move_count)

    def test_learning_a_correction_invalidates_the_cache(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Bh5"
        before = self.pipeline.analyze(text, 0)
        self.assertTrue(before.ast.moves[-1].needs_review)

        self.pipeline.configure(ParseConfig.build(dictionary={"Bh5": "Bb5"}))
        after = self.pipeline.analyze(text, 0)

        self.assertFalse(after.ast.moves[-1].needs_review)

    def test_highlighting_survives_an_edit_that_pushes_the_later_games(self):
        """Digitar na partida 1 nao pode apagar o realce das partidas 2 e 3.

        Elas mudaram de lugar, mas nao de texto: as posicoes se corrigem com uma
        soma, sem reanalisar nada.
        """
        self.pipeline.analyze_all(BOOK)
        edited = BOOK.replace("1.e4 c5 2.Nf3 e6 1-0", "1.e4 c5 2.Nf3 e6 3.d4 cxd4 1-0")

        analysis = self.pipeline.analyze(edited, edited.index("c5"))

        self.assertEqual(3, len(analysis.digests), "as partidas seguintes perderam o realce")

        # Todo trecho realcado tem de cair exatamente sobre o lance que o gerou.
        self.assertEqual(
            ["1.d4", "Nf6", "2.c4", "2... g6", "3.Nc3", "e6"],
            [edited[start:end] for start, end, _kind, _variation in analysis.digests[1].spans],
        )
        self.assertEqual(
            ["1.e4", "Kf6", "2.e5+", "Kf5"],
            [edited[start:end] for start, end, _kind, _variation in analysis.digests[2].spans],
        )

    def test_summaries_report_only_what_is_known(self):
        analysis = self.pipeline.analyze(BOOK, 0)

        summaries = analysis.summaries()
        self.assertEqual(3, len(summaries))
        self.assertTrue(summaries[0].analyzed)
        self.assertEqual(4, summaries[0].move_count)
        self.assertFalse(summaries[1].analyzed)


class BookExportTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = ParsePipeline(ParseConfig.build())

    def _read_all(self, pgn_text: str) -> list[chess.pgn.Game]:
        stream = io.StringIO(pgn_text)
        games = []
        while (game := chess.pgn.read_game(stream)) is not None:
            games.append(game)
        return games

    def test_the_whole_book_goes_into_one_file(self):
        analyses = self.pipeline.analyze_all(BOOK)

        pgn_text, report = build_book_pgn(build_games_for_export(analyses))

        self.assertTrue(report.ok, report.summary())
        self.assertEqual(3, report.games)

        games = self._read_all(pgn_text)
        self.assertEqual(3, len(games))
        self.assertEqual(["Smyslov", "Botvinnik", "?"], [game.headers["White"] for game in games])
        self.assertEqual([], [error for game in games for error in game.errors])

    def test_a_fragment_keeps_its_fen_and_setup(self):
        analyses = self.pipeline.analyze_all(BOOK)

        pgn_text, _report = build_book_pgn(build_games_for_export(analyses))

        fragment = self._read_all(pgn_text)[2]
        self.assertEqual("1", fragment.headers["SetUp"])
        self.assertEqual("8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", fragment.headers["FEN"])
        self.assertEqual(["e4", "Kf6", "e5+", "Kf5"], [node.san() for node in fragment.mainline()])

    def test_a_game_that_does_not_check_out_is_reported(self):
        """Um lance perdido numa partida nao pode passar em silencio pelo lote."""
        analyses = self.pipeline.analyze_all(f"{GAME_ONE}\n=== Partida 2 ===\n\n1.e4 e5 2.Nf3 Xf6z\n")

        _pgn_text, report = build_book_pgn(build_games_for_export(analyses))

        self.assertFalse(report.ok)
        self.assertEqual([1], [index for index, _game_report in report.failed_games])
        self.assertIn("nao conferem", report.summary())

    def test_file_names_are_readable_and_sortable(self):
        self.assertEqual(
            "01_Smyslov-Rudakovsky.pgn",
            game_file_name(0, {"White": "Smyslov", "Black": "Rudakovsky"}),
        )
        self.assertEqual("03_meu_livro.pgn", game_file_name(2, {}, "meu livro"))
        self.assertEqual("10_Estudo_de_final.pgn", game_file_name(9, {"Event": "Estudo de final"}))


class AsyncParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = AsyncParser(ParseConfig.build())
        self.addCleanup(self.parser.shutdown)
        self.received = []
        self.parser.analysis_ready_signal.connect(self.received.append)

    def test_the_debounce_grows_with_the_document(self):
        self.assertEqual(AsyncParser.FAST_DEBOUNCE_MS, self.parser.debounce_interval("1.e4 e5"))
        self.assertEqual(AsyncParser.SLOW_DEBOUNCE_MS, self.parser.debounce_interval("x" * 40_000))

    def test_pending_requests_collapse_into_the_newest(self):
        self.parser.schedule("1.e4 e5", 0)
        self.parser.schedule("1.e4 e5 2.Nf3 Nc6", 0)
        QTest.qWait(700)

        self.assertEqual(1, len(self.received), "o pedido antigo devia ter sido descartado")
        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.display_san for move in self.received[0].ast.moves])

    def test_a_synchronous_parse_cancels_the_scheduled_one(self):
        self.parser.schedule("1.e4 e5", 0)

        analysis = self.parser.parse_now("1.d4 d5", 0)
        QTest.qWait(400)

        self.assertEqual(["d4", "d5"], [move.display_san for move in analysis.ast.moves])
        self.assertEqual([], self.received)

    def test_shutdown_is_safe_to_call_twice(self):
        self.parser.schedule("1.e4 e5", 0)
        QTest.qWait(400)

        self.parser.shutdown()
        self.parser.shutdown()


if __name__ == "__main__":
    unittest.main()
