"""Testes de fumaca da interface (rodam sem tela, com QT_QPA_PLATFORM=offscreen)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QTextCursor  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# `tests/` nao e pacote: o discover (e o pytest) poem a pasta no sys.path.
from test_engine import HANGING_KNIGHT_FEN, shallow_search  # noqa: E402

from pgn_live_editor.core.issues import SUSPECT_BLUNDER  # noqa: E402
from pgn_live_editor.services.engine_service import (  # noqa: E402
    EngineSettings,
    scan_mainline,
)
from pgn_live_editor.ui.dictionary_dialog import DictionaryDialog  # noqa: E402
from pgn_live_editor.ui.editor_panel import EditorPanel  # noqa: E402
from pgn_live_editor.ui.header_dialog import HeaderDialog  # noqa: E402
from pgn_live_editor.ui.main_window import MainWindow  # noqa: E402

BOOK_TEXT = "{Defesa Siciliana} 1.e4 c5 2.Nf3 e6 3.d4 cxd4 (3... Nf6 4.e5) 4.Nxd4"

_app = QApplication.instance() or QApplication([])


class EditorPanelEditingTests(unittest.TestCase):
    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)

    def test_join_lines_keeps_undo_history(self):
        """Antes, `setPlainText` apagava a pilha de Ctrl+Z."""
        self.panel.load_document_text("1.e4 e5\n2.Nf3 Nc6")

        self.panel.action_join_lines()
        self.assertEqual("1.e4 e5 2.Nf3 Nc6", self.panel.document_text())

        self.assertTrue(self.panel.text_edit.document().isUndoAvailable())
        self.panel.text_edit.undo()
        self.assertEqual("1.e4 e5\n2.Nf3 Nc6", self.panel.document_text())

    def test_insert_headers_keeps_undo_history(self):
        self.panel.load_document_text("1.e4 e5")

        self.panel.insert_default_headers()
        self.assertIn('[Event "PGN Live Editor Export"]', self.panel.document_text())

        self.panel.text_edit.undo()
        self.assertEqual("1.e4 e5", self.panel.document_text())

    def test_replace_all_text_keeps_the_cursor_where_it_was(self):
        self.panel.load_document_text("1.e4 e5 2.Nf3 Nc6")
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(8)
        self.panel.text_edit.setTextCursor(cursor)

        self.panel.replace_all_text("1.e4 e5 2.Nf3 Nc6 3.Bb5")

        self.assertEqual(8, self.panel.cursor_position())

    def test_replace_span_is_undoable_and_reparses(self):
        self.panel.load_document_text("1.e4 e5 2.Nf3 Nc6")

        self.panel.replace_span(0, 4, "1.d4")

        self.assertTrue(self.panel.document_text().startswith("1.d4"))
        self.panel.text_edit.undo()
        self.assertTrue(self.panel.document_text().startswith("1.e4"))

    def test_fen_header_sets_the_starting_position(self):
        """Fragmento de livro que parte de um diagrama."""
        self.panel.load_document_text('[FEN "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"]\n[SetUp "1"]\n\n1.e4 Kf6 2.e5+ Kf5')

        self.assertEqual("8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", self.panel.current_start_fen)
        self.assertEqual(["e4", "Kf6", "e5+", "Kf5"], [move.display_san for move in self.panel.current_ast.moves])
        self.assertTrue(all(move.is_valid for move in self.panel.current_ast.moves))

    def test_invalid_fen_falls_back_to_the_initial_position(self):
        self.panel.load_document_text('[FEN "isto nao e um fen"]\n\n1.e4 e5')

        self.assertEqual(["e4", "e5"], [move.display_san for move in self.panel.current_ast.moves])


class EditorGestureTests(unittest.TestCase):
    """Fase 3: transcrever sem digitar delimitador nenhum."""

    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)

    def _select(self, start: int, end: int):
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.panel.text_edit.setTextCursor(cursor)

    def _place(self, position: int):
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(position)
        self.panel.text_edit.setTextCursor(cursor)

    def test_wrap_comment_gesture(self):
        text = "1.e4 e5 as brancas ficam melhor 2.Nf3"
        self.panel.load_document_text(text)
        self._select(text.index("as brancas"), text.index(" 2.Nf3"))

        self.panel.action_wrap_comment()

        self.assertEqual("1.e4 e5 {as brancas ficam melhor} 2.Nf3", self.panel.document_text())

    def test_wrap_variation_gesture_adds_the_move_number(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Bb5 a6"
        self.panel.load_document_text(text)
        self._select(text.index("Nc6"), text.index(" 3.Bb5"))

        self.panel.action_wrap_variation()

        self.assertEqual("1.e4 e5 2.Nf3 (2... Nc6) 3.Bb5 a6", self.panel.document_text())

    def test_a_gesture_is_a_single_undo_step(self):
        text = "1.e4 e5 2.Nf3 Nc6 Bb5"
        self.panel.load_document_text(text)
        self._select(text.index("Nc6"), text.index("Nc6") + 3)

        self.panel.action_wrap_variation()
        self.assertNotEqual(text, self.panel.document_text())

        self.panel.text_edit.undo()
        self.assertEqual(text, self.panel.document_text())

    def test_unwrap_gesture(self):
        text = "1.e4 e5 {um comentario} 2.Nf3"
        self.panel.load_document_text(text)
        self._place(text.index("comentario"))

        self.panel.action_unwrap()

        self.assertEqual("1.e4 e5 um comentario 2.Nf3", self.panel.document_text())

    def test_paragraph_to_comment_gesture(self):
        text = "1.e4 e5 2.Nf3 recurso tatico fortissimo 3.Bb5"
        self.panel.load_document_text(text)
        self._place(text.index("tatico"))

        self.panel.action_paragraph_to_comment()

        self.assertEqual("1.e4 e5 2.Nf3 {recurso tatico fortissimo} 3.Bb5", self.panel.document_text())

    def test_renumber_gesture_repairs_the_whole_document(self):
        self.panel.load_document_text("1.e4 e5 7.Nf3 Nc6 3.Bb5 (9.Bc4 d6)")

        self.panel.action_renumber()

        self.assertEqual("1. e4 e5 2. Nf3 Nc6 3. Bb5 (3. Bc4 d6)", self.panel.document_text())

    def test_typing_an_opening_brace_wraps_the_selection(self):
        text = "1.e4 e5 comentario solto 2.Nf3"
        self.panel.load_document_text(text)
        self._select(text.index("comentario"), text.index(" 2.Nf3"))

        QTest.keyClicks(self.panel.text_edit, "{")

        self.assertEqual("1.e4 e5 {comentario solto} 2.Nf3", self.panel.document_text())

    def test_typing_an_opening_brace_auto_closes_it(self):
        self.panel.load_document_text("1.e4 e5 ")
        self._place(len("1.e4 e5 "))

        QTest.keyClicks(self.panel.text_edit, "{")

        self.assertEqual("1.e4 e5 {}", self.panel.document_text())
        self.assertEqual(len("1.e4 e5 {"), self.panel.cursor_position())

    def test_typing_the_closing_brace_steps_over_it(self):
        self.panel.load_document_text("1.e4 e5 ")
        self._place(len("1.e4 e5 "))

        QTest.keyClicks(self.panel.text_edit, "{nota}")

        self.assertEqual("1.e4 e5 {nota}", self.panel.document_text())

    def test_balance_check_finds_the_unclosed_paren(self):
        self.panel.load_document_text("1.e4 (2.Nf3 Nc6")

        problems = self.panel.balance_problems()

        self.assertEqual(1, len(problems))
        self.assertEqual(5, problems[0].position)


class MainWindowSmokeTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def test_window_builds_and_wires_the_panels(self):
        self.window.editor_panel.load_document_text(BOOK_TEXT)

        self.assertEqual(9, len(self.window.chess_panel.entries))
        self.assertEqual("*", self.window.chess_panel.game_ast.result or "*")

    def test_board_navigation_moves_the_position(self):
        self.window.editor_panel.load_document_text(BOOK_TEXT)
        panel = self.window.chess_panel

        panel.go_next()
        panel.go_next()

        self.assertEqual("e4", panel.entries[panel.current_entry_index - 1].node.display_san)
        self.assertGreater(panel.board.fullmove_number, 1)

    def test_flip_board_toggles_orientation(self):
        panel = self.window.chess_panel
        self.assertFalse(panel.board_flipped)

        panel.flip_board()
        self.assertTrue(panel.board_flipped)

    def test_issues_panel_shows_what_the_parser_did(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.4Jf3 Cc6 3.Bh5")
        panel = self.window.issues_panel

        codes = [issue.code for issue in panel.issues]
        self.assertIn("AUTO_CORRECTED", codes)
        self.assertIn("AMBIGUOUS_MOVE", codes)
        self.assertGreater(panel.table.rowCount(), 0)
        self.assertIn("aviso", panel.lbl_summary.text())

    def test_accepting_a_correction_rewrites_the_raw_text_and_clears_the_alert(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "AMBIGUOUS_MOVE")
        self.window._accept_issue(issue)

        self.assertEqual("1.e4 e5 2.Nf3 Nc6 3.Bb5", self.window.editor_panel.document_text())
        self.assertEqual([], panel.issues)

    def test_turning_an_unrecognized_token_into_a_comment(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Xf6z")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "UNRECOGNIZED_MOVE")
        self.window._comment_issue(issue)

        self.assertEqual("1.e4 e5 2.Nf3 {Xf6z}", self.window.editor_panel.document_text())
        self.assertEqual([], panel.issues)

    def test_learning_a_correction_applies_it_from_then_on(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "AMBIGUOUS_MOVE")
        self.window._learn_issue(issue)

        self.assertEqual({"Bh5": "Bb5"}, self.window.project.dictionary.token_map("pt"))
        # Agora a mesma leitura entra com confianca de dicionario, sem pedir revisao.
        self.assertEqual([], [i for i in panel.issues if i.severity == "warning"])
        self.assertFalse(self.window.editor_panel.current_ast.moves[-1].needs_review)

    def test_ignoring_a_token_silences_it_for_the_project(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Xf6z")
        panel = self.window.issues_panel

        issue = next(issue for issue in panel.issues if issue.code == "UNRECOGNIZED_MOVE")
        self.window._ignore_issue(issue)

        self.assertIn("Xf6z", self.window.project.state.ignored_issue_tokens)
        self.assertEqual([], panel.issues)

    def test_invalid_date_header_is_reported(self):
        self.window.editor_panel.load_document_text('[Date "1945"]\n\n1.e4 e5')

        codes = [issue.code for issue in self.window.issues_panel.issues]
        self.assertIn("INVALID_DATE", codes)

    def test_dirty_flag_tracks_unsaved_work(self):
        self.assertFalse(self.window._is_dirty())

        self.window.editor_panel.load_document_text(BOOK_TEXT)
        self.assertTrue(self.window._is_dirty())

        self.window.project.raw_text = BOOK_TEXT
        self.assertFalse(self.window._is_dirty())


BOOK_DOCUMENT = (
    '[Event "Torneio"]\n[Site "Moscou"]\n[White "Smyslov"]\n[Black "Rudakovsky"]\n[Result "1-0"]\n'
    "\n1.e4 c5 2.Nf3 e6 1-0\n"
    "\n=== Estudo 2 ===\n"
    '[FEN "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"]\n[SetUp "1"]\n'
    "\n1.e4 Kf6 2.e5+ Kf5 *\n"
)


class DictionaryInTheWindowTests(unittest.TestCase):
    """Fase 5: o dicionario aprendiz."""

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def _ambiguous_issue(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")
        return next(issue for issue in self.window.issues_panel.issues if issue.code == "AMBIGUOUS_MOVE")

    def test_the_third_acceptance_offers_to_learn_the_correction(self):
        counters = self.window.project.state.accepted_corrections
        issue = self._ambiguous_issue()
        candidate = issue.best_candidate

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as asked:
            for _ in range(2):
                self.window._count_acceptance(issue, candidate)
            self.assertEqual(0, asked.call_count, "duas vezes ainda e coincidencia")

            self.window._count_acceptance(issue, candidate)
            self.assertEqual(1, asked.call_count, "na terceira, o programa pergunta")

        self.assertEqual(3, counters["Bh5→Bb5"])
        self.assertEqual({"Bh5": "Bb5"}, self.window.project.dictionary.token_map("pt"))

    def test_declining_the_offer_leaves_the_dictionary_alone(self):
        issue = self._ambiguous_issue()
        candidate = issue.best_candidate

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            for _ in range(3):
                self.window._count_acceptance(issue, candidate)

        self.assertEqual(0, len(self.window.project.dictionary))

    def test_the_dialog_lists_the_three_layers_with_their_reach(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5 Bh5")
        self.window._learn_substitution("Bh5", "Bb5")

        dialog = DictionaryDialog(
            self.window.dictionary_service,
            self.window.editor_panel.document_text(),
        )
        self.addCleanup(dialog.deleteLater)

        rows = [(entry.layer, entry.pattern, count) for entry, count, _ in dialog._rows]
        self.assertIn(("projeto", "Bh5", 2), rows)
        self.assertTrue(any(layer == "base" for layer, _pattern, _count in rows))


class ReviewModeTests(unittest.TestCase):
    """Fase 5: dupla visao -- o que foi mexido contra o que so foi confirmado."""

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def test_the_counts_separate_confirmed_from_touched(self):
        # `Bh5` e corrigido em carater provisorio; o resto entra sem duvida.
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")

        counts = self.window.editor_panel.touch_counts()

        self.assertEqual(4, counts["confirmado"])
        self.assertEqual(1, counts["por_conferir"])
        self.assertEqual(0, counts["nao_entendido"])

    def test_an_unreadable_move_is_counted_apart(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Xf6z")

        self.assertEqual(1, self.window.editor_panel.touch_counts()["nao_entendido"])

    def test_turning_the_mode_on_dims_what_was_only_confirmed(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6 3.Bh5")
        self.assertFalse(self.window.editor_panel.review_mode)

        self.window.action_review_mode.setChecked(True)
        self.window._toggle_review_mode()

        self.assertTrue(self.window.editor_panel.review_mode)
        self.assertIn("confirmados 4", self.window.lbl_touched.text())
        self.assertIn("por conferir 1", self.window.lbl_touched.text())

    def test_turning_it_off_clears_the_status(self):
        self.window.editor_panel.load_document_text("1.e4 e5")
        self.window.action_review_mode.setChecked(True)
        self.window._toggle_review_mode()

        self.window.action_review_mode.setChecked(False)
        self.window._toggle_review_mode()

        self.assertFalse(self.window.editor_panel.review_mode)
        self.assertEqual("", self.window.lbl_touched.text())

    def test_the_counts_cover_the_whole_book_not_just_the_current_game(self):
        self.window.editor_panel.load_document_text(
            "1.e4 e5 2.Nf3 Nc6\n\n=== Partida 2 ===\n\n1.d4 d5 2.c4"
        )
        self.window.editor_panel.analyze_whole_book()

        counts = self.window.editor_panel.touch_counts()

        self.assertEqual(7, counts["confirmado"])


class EngineIssuesInTheWindowTests(unittest.TestCase):
    """Fase 5: a suspeita do motor convive com os alertas do parser.

    Nenhum teste aqui abre motor nenhum: a varredura roda com o avaliador
    material dos testes de `test_engine`, e o que se verifica e a costura com a
    janela -- os alertas entram na lista, sobrevivem a uma edicao acima e somem
    quando a partida deles muda.
    """

    GAME = "1. Nd5"

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def _scan(self, text: str = "") -> None:
        """Faz o que `_scan_with_engine` faria, sem o processo do motor."""
        self.window.editor_panel.load_document_text(text or self.GAME)
        ast = self.window.editor_panel.reparse_now()
        segment = self.window.editor_panel.active_segment()

        self.window._pending_engine_segment = (
            segment.body if segment else self.window.editor_panel.document_text(),
            segment.raw_offset if segment else 0,
        )
        report = scan_mainline(
            ast,
            self.window.editor_panel.current_start_fen,
            shallow_search,
            EngineSettings(threshold_cp=250),
        )
        self.window._on_engine_scan_finished(report)
        return report

    def test_a_suspect_move_reaches_the_issues_panel(self):
        self._scan(f'[FEN "{HANGING_KNIGHT_FEN}"]\n[SetUp "1"]\n\n{self.GAME}')

        codes = [issue.code for issue in self.window.issues_panel.issues]
        self.assertIn(SUSPECT_BLUNDER, codes)
        self.assertGreater(self.window.issues_panel.table.rowCount(), 0)

    def test_accepting_the_engine_suggestion_rewrites_the_raw_text(self):
        self._scan(f'[FEN "{HANGING_KNIGHT_FEN}"]\n[SetUp "1"]\n\n{self.GAME}')

        issue = next(i for i in self.window.issues_panel.issues if i.code == SUSPECT_BLUNDER)
        self.assertEqual(issue.best_candidate.san, "Nd1")

        self.window._accept_issue(issue)
        self.assertIn("1. Nd1", self.window.editor_panel.document_text())

    def test_dismissing_a_suspicion_does_not_touch_the_text(self):
        """'Ignorar' um alerta do motor nao pode virar token silenciado no parser."""
        self._scan(f'[FEN "{HANGING_KNIGHT_FEN}"]\n[SetUp "1"]\n\n{self.GAME}')
        before = self.window.editor_panel.document_text()

        issue = next(i for i in self.window.issues_panel.issues if i.code == SUSPECT_BLUNDER)
        self.window._ignore_issue(issue)

        self.assertEqual(before, self.window.editor_panel.document_text())
        self.assertEqual([], self.window.project.state.ignored_issue_tokens)
        self.assertNotIn(SUSPECT_BLUNDER, [i.code for i in self.window.issues_panel.issues])

    def test_editing_the_scanned_game_drops_the_stale_suspicion(self):
        self._scan(f'[FEN "{HANGING_KNIGHT_FEN}"]\n[SetUp "1"]\n\n{self.GAME}')
        self.assertIn(SUSPECT_BLUNDER, [i.code for i in self.window.issues_panel.issues])

        # O lance conferido mudou: o alerta falava de outro texto.
        self.window.editor_panel.load_document_text(
            f'[FEN "{HANGING_KNIGHT_FEN}"]\n[SetUp "1"]\n\n1. Nd1'
        )
        self.window.editor_panel.reparse_now()
        self.window._refresh_issues()

        self.assertNotIn(SUSPECT_BLUNDER, [i.code for i in self.window.issues_panel.issues])
        self.assertIsNone(self.window._engine_findings)


class BookWindowTests(unittest.TestCase):
    """Fase 4: um documento com varias partidas."""

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)
        self.window.editor_panel.load_document_text(BOOK_DOCUMENT)

    def test_the_games_panel_lists_every_game(self):
        panel = self.window.games_panel

        self.assertEqual(2, panel.list_widget.count())
        self.assertIn("Smyslov", panel.list_widget.item(0).text())
        self.assertIn("partida 1/2", panel.lbl_progress.text())

    def test_the_status_bar_shows_where_in_the_book_we_are(self):
        self.assertIn("partida 1/2", self.window.lbl_book.text())
        self.assertIn("4 lances", self.window.lbl_book.text())

    def test_moving_the_cursor_switches_the_active_game(self):
        cursor = self.window.editor_panel.text_edit.textCursor()
        cursor.setPosition(BOOK_DOCUMENT.index("Kf6"))
        self.window.editor_panel.text_edit.setTextCursor(cursor)

        self.assertEqual(1, self.window.editor_panel.analysis.active_index)
        # O tabuleiro passa a partir do diagrama do estudo, nao da posicao inicial.
        self.assertEqual("8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", self.window.chess_panel.start_fen)
        self.assertEqual(["e4", "Kf6", "e5+", "Kf5"], [
            move.display_san for move in self.window.editor_panel.current_ast.moves
        ])

    def test_stepping_to_the_next_game_moves_the_cursor_there(self):
        self.window._step_game(1)

        self.assertEqual(1, self.window.editor_panel.analysis.active_index)
        self.assertIn("partida 2/2", self.window.lbl_book.text())

        self.window._step_game(1)
        self.assertEqual(0, self.window.editor_panel.analysis.active_index, "deve dar a volta")

    def test_clicking_a_game_in_the_list_navigates_to_it(self):
        self.window.games_panel.list_widget.setCurrentRow(1)

        self.assertEqual(1, self.window.editor_panel.analysis.active_index)

    def test_marking_a_game_as_done_is_unsaved_work(self):
        self.window.project.raw_text = self.window.editor_panel.document_text()
        self.assertFalse(self.window._is_dirty())

        self.window.games_panel.list_widget.setCurrentRow(1)
        self.window.games_panel.toggle_current_completion()

        self.assertEqual([1], self.window.project.state.completed_games)
        self.assertTrue(self.window._is_dirty(), "marcar como concluida nao pode sumir ao fechar")
        self.assertIn("1 concluida", self.window.lbl_book.text())

    def test_analyzing_the_book_fills_in_the_list(self):
        self.window._analyze_book()

        self.assertIn("4 lances", self.window.games_panel.list_widget.item(1).text())

    def test_headers_are_written_into_the_game_under_the_cursor(self):
        self.window._step_game(1)

        self.window.editor_panel.action_apply_headers({
            "Event": "Final de peoes",
            "White": "Estudo",
            "FEN": "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1",
            "SetUp": "1",
        })
        text = self.window.editor_panel.document_text()

        self.assertEqual(1, text.count('[Event "Final de peoes"]'))
        self.assertIn('[White "Smyslov"]', text, "a primeira partida nao pode ser tocada")
        self.assertGreater(text.index("Final de peoes"), text.index("Estudo 2"))

    def test_the_board_position_becomes_the_start_of_the_current_game(self):
        self.window.chess_panel.go_next()
        self.window.chess_panel.go_next()
        expected = self.window.chess_panel.board.fen()

        self.window._set_start_fen_from_board()

        segment = self.window.editor_panel.active_segment()
        self.assertEqual(expected, segment.headers["FEN"])
        self.assertEqual("1", segment.headers["SetUp"])

    def test_diagram_markers_become_comments(self):
        self.window.editor_panel.load_document_text("1.e4 e5 [D] 2.Nf3")

        self.window._normalize_diagrams()

        self.assertEqual("1.e4 e5 {[#]} 2.Nf3", self.window.editor_panel.document_text())

    def test_renumbering_repairs_every_game_of_the_book(self):
        self.window.editor_panel.load_document_text("1.e4 e5 7.Nf3\n\n=== 2 ===\n\n1.d4 d5 9.c4")

        self.window.editor_panel.action_renumber()

        self.assertEqual(
            "1. e4 e5 2. Nf3\n\n=== 2 ===\n\n1. d4 d5 2. c4",
            self.window.editor_panel.document_text(),
        )

    def test_a_single_game_document_keeps_the_list_out_of_the_way(self):
        self.window.editor_panel.load_document_text("1.e4 e5 2.Nf3 Nc6")

        self.assertEqual(1, self.window.games_panel.list_widget.count())
        self.assertFalse(self.window.games_panel.isVisibleTo(self.window.splitter))
        self.assertNotIn("partida", self.window.lbl_book.text())


class HeaderDialogTests(unittest.TestCase):
    def test_empty_fields_do_not_become_empty_headers(self):
        dialog = HeaderDialog({"Event": "Torneio", "White": "Smyslov"})
        self.addCleanup(dialog.deleteLater)

        self.assertEqual({"Event": "Torneio", "White": "Smyslov"}, dialog.values())

    def test_the_book_template_only_carries_what_repeats(self):
        dialog = HeaderDialog({
            "Event": "Torneio", "Site": "Moscou", "Date": "1945.??.??",
            "White": "Smyslov", "Black": "Rudakovsky",
        })
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(
            {"Event": "Torneio", "Site": "Moscou", "Date": "1945.??.??"},
            dialog.book_template_values(),
        )

    def test_applying_a_template_does_not_overwrite_this_game(self):
        dialog = HeaderDialog(
            {"White": "Smyslov", "Event": "Ja preenchido"},
            {"Event": "Torneio do livro", "Site": "Moscou"},
        )
        self.addCleanup(dialog.deleteLater)

        dialog.combo_templates.setCurrentIndex(0)
        dialog._apply_template()

        self.assertEqual("Ja preenchido", dialog.values()["Event"])
        self.assertEqual("Moscou", dialog.values()["Site"])


if __name__ == "__main__":
    unittest.main()
