"""Surrogate solitario: tratado onde entra, nunca no meio do salvamento (S-13 / R-107).

O Qt deixa passar um surrogate UTF-16 sem par (colagem de PDF, OCR, area de
transferencia); ele ia parar no texto do editor e so explodia ao salvar, com
UnicodeEncodeError, que o salvar nao capturava -- e o processo morria.
"""

from __future__ import annotations

import unittest
from unittest import mock

import _isolation  # noqa: F401
from PyQt6.QtCore import QMimeData
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.core.issues import INVALID_CHARACTER
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.text_hygiene import REPLACEMENT_CHARACTER, replace_lone_surrogates
from pgn_live_editor.core.tokenizer import PGNTokenizer

LONE_HIGH = "\ud800"
LONE_LOW = "\udc00"


class ReplaceLoneSurrogatesTests(unittest.TestCase):
    def test_lone_surrogates_become_the_replacement_character(self):
        cleaned, count = replace_lone_surrogates(f"1. e4{LONE_HIGH} e5 {LONE_LOW}")

        self.assertEqual(f"1. e4{REPLACEMENT_CHARACTER} e5 {REPLACEMENT_CHARACTER}", cleaned)
        self.assertEqual(2, count)
        cleaned.encode("utf-8")  # nao levanta

    def test_real_characters_outside_the_bmp_are_kept(self):
        text = "Nf3 \U0001f600 ½ ♘"

        self.assertEqual((text, 0), replace_lone_surrogates(text))

    def test_the_replacement_keeps_every_offset(self):
        text = f"ab{LONE_HIGH}cd"

        cleaned, _count = replace_lone_surrogates(text)

        self.assertEqual(len(text), len(cleaned))

    def test_the_parser_flags_the_replaced_character(self):
        text = f"1. e4 e5 2. Nf3 {{bom{REPLACEMENT_CHARACTER}}} Nc6"
        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)

        ast = TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))

        flagged = [issue for issue in ast.issues if issue.code == INVALID_CHARACTER]
        self.assertEqual(1, len(flagged))
        self.assertIn(REPLACEMENT_CHARACTER, text[flagged[0].raw_start : flagged[0].raw_end])
        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.san for move in ast.moves])


class EditorEntryTests(unittest.TestCase):
    def setUp(self):
        from pgn_live_editor.ui.editor_panel import EditorPanel

        self.app = QApplication.instance() or QApplication([])
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)

    def assert_clean(self):
        text = self.panel.document_text()
        text.encode("utf-8")
        self.assertIn(REPLACEMENT_CHARACTER, text)

    def test_pasting_cleans_the_text(self):
        source = QMimeData()
        source.setText(f"1. e4 e5{LONE_HIGH}")

        self.panel.text_edit.insertFromMimeData(source)

        self.assert_clean()

    def test_loading_a_document_cleans_the_text(self):
        self.panel.load_document_text(f"1. e4 {LONE_LOW}e5")

        self.assert_clean()

    def test_inserting_at_the_cursor_cleans_the_text(self):
        self.panel.load_document_text("1. e4 ")
        self.panel.insert_text_at_cursor(f"e5{LONE_HIGH}")

        self.assert_clean()

    def test_replacing_a_span_cleans_the_replacement(self):
        self.panel.load_document_text("1. e4 e5")
        self.panel.replace_span(6, 8, f"c5{LONE_HIGH}")

        self.assert_clean()

    def test_the_editor_says_how_many_characters_were_replaced(self):
        reported = []
        self.panel.invalid_characters_replaced.connect(reported.append)

        self.panel.load_document_text(f"{LONE_HIGH}{LONE_LOW}x{LONE_HIGH}")

        self.assertEqual([3], reported)


class SaveDefenseTests(unittest.TestCase):
    """Segunda linha: se ainda assim o salvar falhar, o programa avisa e continua."""

    def test_an_unexpected_error_while_saving_keeps_the_window_and_the_text(self):
        import tempfile
        from pathlib import Path

        from PyQt6.QtWidgets import QMessageBox

        from pgn_live_editor.ui import main_window as module
        from pgn_live_editor.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.editor_panel.load_document_text("1. e4 e5 {meu trabalho}")
        target = Path(tempfile.mkdtemp()) / "x.plez"

        with (
            mock.patch.object(module, "save_project", side_effect=UnicodeEncodeError("utf-8", "x", 0, 1, "teste")),
            mock.patch.object(QMessageBox, "critical") as critical,
        ):
            saved = window._write_project(target)

        self.assertFalse(saved)
        critical.assert_called_once()
        self.assertEqual("1. e4 e5 {meu trabalho}", window.editor_panel.document_text())


if __name__ == "__main__":
    unittest.main()
