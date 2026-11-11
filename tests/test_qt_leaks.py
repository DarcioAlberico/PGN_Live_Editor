"""Objetos Qt que se acumulavam a cada gesto (R-308).

U-13: um `QMenu` novo, filho do painel, a cada clique direito -- e nunca
apagado; as `QAction` de "Abrir recente" filhas da janela, recriadas a cada
reconstrucao do menu. U-15: o painel de partidas fazia `clear()` dentro do
proprio `currentRowChanged` (a navegacao reanalisa e reconstroi a lista).
"""

from __future__ import annotations

import unittest
from unittest import mock

import _isolation  # noqa: F401
from PyQt6.QtCore import QEvent, QPoint
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

from pgn_live_editor.services.parse_worker import SegmentSummary

_app = QApplication.instance() or QApplication([])

BOOK = '[Event "A"]\n\n1. e4 e5 2. Nf3 *\n\n[Event "B"]\n\n1. d4 d5 *\n'


def flush() -> None:
    for _ in range(3):
        _app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)  # entrega os deleteLater
    _app.processEvents()


class LeakTests(unittest.TestCase):
    def setUp(self):
        from pgn_live_editor.ui.main_window import MainWindow

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)
        self.window.editor_panel.load_document_text(BOOK)
        self.window.editor_panel.reparse_now()

    def count_after(self, gesture, kind) -> tuple[int, int]:
        gesture()
        flush()
        before = len(self.window.findChildren(kind))
        for _ in range(20):
            gesture()
        flush()
        return before, len(self.window.findChildren(kind))

    def test_context_menus_do_not_pile_up(self):
        tree = self.window.editor_panel.pgn_tree
        with (
            mock.patch.object(QMenu, "exec", return_value=None),
            mock.patch.object(tree.tree, "itemAt", side_effect=lambda *_: tree.tree.topLevelItem(0)),
        ):
            for gesture in (
                lambda: self.window.chess_panel._show_copy_menu(QPoint(1, 1)),
                lambda: tree._open_context_menu(QPoint(1, 1)),
            ):
                with self.subTest(gesture=gesture):
                    before, after = self.count_after(gesture, QMenu)
                    self.assertEqual(before, after)

    def test_rebuilding_the_recent_menu_does_not_pile_up_actions(self):
        before, after = self.count_after(self.window._rebuild_recent_menu, QAction)

        self.assertEqual(before, after)


class GamesPanelReentrancyTests(unittest.TestCase):
    def test_the_list_is_not_cleared_inside_its_own_row_change(self):
        from pgn_live_editor.ui.games_panel import GamesPanel

        panel = GamesPanel()
        self.addCleanup(panel.deleteLater)
        summaries = [SegmentSummary(0, "Partida A"), SegmentSummary(1, "Partida B")]
        panel.set_summaries(summaries, 0)

        inside = {"now": False}
        cleared_inside: list[bool] = []
        real_clear = panel.list_widget.clear

        def watching_clear():
            cleared_inside.append(inside["now"])
            real_clear()

        def navigate(index: int) -> None:
            # O que a janela faz: navegar reanalisa e reconstroi a lista.
            inside["now"] = True
            panel.set_summaries(summaries, index)
            inside["now"] = False

        panel.game_selected_signal.connect(navigate)
        with mock.patch.object(panel.list_widget, "clear", watching_clear):
            panel.list_widget.setCurrentRow(1)
            flush()

        self.assertNotIn(True, cleared_inside)
        self.assertEqual(1, panel.active_index)
        self.assertEqual(1, panel.list_widget.currentRow())


if __name__ == "__main__":
    unittest.main()
