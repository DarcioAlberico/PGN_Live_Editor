"""Higiene do editor (R-307).

U-07: `rehighlightBlock` emite `textChanged` sem edicao nenhuma: uma analise a
mais e o contador do autosave andando sozinho.
U-10: `_delimiter_cache` sem limite (301 entradas depois de 300 teclas); e
`load_document_text` deixava o timer de analise armado -- abrir um projeto
fazia uma segunda analise completa, redundante.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

import _isolation  # noqa: F401
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.ui.editor_panel import DELIMITER_CACHE_LIMIT, EditorPanel

_app = QApplication.instance() or QApplication([])

BOOK = '[Event "A"]\n\n1. e4 e5 (1... c5 2. Nf3) 2. Nf3 {nota} Nc6 *\n\n[Event "B"]\n\n1. d4 d5 *\n'


def settle(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.01)


class EditorHygieneTests(unittest.TestCase):
    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)

    def test_opening_a_document_analyses_it_once(self):
        with mock.patch.object(self.panel.async_parser, "schedule") as schedule:
            self.panel.load_document_text(BOOK)
            settle(0.4)

        self.assertFalse(schedule.called, "a abertura ja analisou; o timer nao pode disparar outra")

    def test_recoloring_is_not_an_edit(self):
        self.panel.load_document_text(BOOK)
        edits: list[None] = []
        self.panel.edited_signal.connect(lambda: edits.append(None))

        self.panel.set_theme("claro")
        self.panel.set_theme("escuro")

        self.assertEqual([], edits)

    def test_typing_is_an_edit(self):
        self.panel.load_document_text(BOOK)
        edits: list[None] = []
        self.panel.edited_signal.connect(lambda: edits.append(None))

        self.panel.text_edit.textCursor().insertText("x")

        self.assertEqual(1, len(edits))

    def test_the_delimiter_cache_is_bounded(self):
        self.panel.load_document_text(BOOK)
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(BOOK.index("{nota}") + 1)
        for _ in range(DELIMITER_CACHE_LIMIT + 50):
            cursor.insertText("a")
            self.panel.reparse_now()

        self.assertLessEqual(len(self.panel._delimiter_cache), DELIMITER_CACHE_LIMIT)


class AutosaveCounterTests(unittest.TestCase):
    def test_recoloring_does_not_advance_the_autosave_counter(self):
        from PyQt6.QtWidgets import QMessageBox

        from pgn_live_editor.ui.main_window import MainWindow

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            window = MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.editor_panel.shutdown)
        window.editor_panel.load_document_text(BOOK)
        window._chars_since_autosave = 0

        window.editor_panel.set_theme("claro")
        window.editor_panel.set_theme("escuro")

        self.assertEqual(0, window._chars_since_autosave)


if __name__ == "__main__":
    unittest.main()
