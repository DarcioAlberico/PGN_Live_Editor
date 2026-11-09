"""Fase 5: o PDF do livro ao lado do editor (SPEC 5.6)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pdf_fixture import write_pdf  # noqa: E402
from PyQt6.QtGui import QTextCursor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.services.project_service import (  # noqa: E402
    Project,
    load_project,
    save_project,
)
from pgn_live_editor.ui.main_window import MainWindow  # noqa: E402
from pgn_live_editor.ui.pdf_panel import PdfPanel  # noqa: E402

PAGES = [
    ["PARTIDA N.o 13", "1.e4 c5 2.Cf3 e6 3.d4 cxd4"],
    ["4.Cxd4 Cf6 5.Cc3 d6"],
    ["6.Be2 Be7 7.O-O O-O"],
]

_app = QApplication.instance() or QApplication([])


class PdfPanelTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.pdf = write_pdf(Path(self._temp.name) / "livro.pdf", PAGES)

        self.panel = PdfPanel()
        self.addCleanup(self.panel.deleteLater)
        # O leitor de PDF segura o arquivo aberto no Windows: fechar antes de
        # apagar a pasta temporaria (addCleanup roda na ordem inversa).
        self.addCleanup(self.panel.close_pdf)

    def test_opening_a_pdf_reads_its_pages(self):
        problem = self.panel.open_pdf(self.pdf)

        self.assertEqual("", problem)
        self.assertEqual(3, self.panel.page_count)
        self.assertEqual(0, self.panel.current_page())
        self.assertIn("1.e4 c5", self.panel.txt_page.toPlainText())

    def test_a_file_that_is_not_a_pdf_reports_instead_of_crashing(self):
        broken = Path(self._temp.name) / "não-e-pdf.pdf"
        broken.write_text("isto não e um PDF", encoding="utf-8")

        problem = self.panel.open_pdf(broken)

        self.assertIn("Não foi possível abrir", problem)
        self.assertFalse(self.panel.has_document())

    def test_changing_the_page_changes_the_extracted_text(self):
        self.panel.open_pdf(self.pdf)

        self.panel.go_to_page(2)

        self.assertEqual(2, self.panel.current_page())
        self.assertIn("6.Be2", self.panel.txt_page.toPlainText())

    def test_paging_past_the_end_stops_at_the_last_page(self):
        self.panel.open_pdf(self.pdf)

        self.panel.go_to_page(99)

        self.assertEqual(2, self.panel.current_page())

    def test_with_no_selection_the_whole_page_is_sent(self):
        self.panel.open_pdf(self.pdf)
        sent: list[str] = []
        self.panel.text_requested_signal.connect(sent.append)

        self.panel.send_to_editor()

        self.assertEqual(1, len(sent))
        self.assertIn("1.e4 c5", sent[0])

    def test_only_the_selection_is_sent_when_there_is_one(self):
        self.panel.open_pdf(self.pdf)
        text = self.panel.txt_page.toPlainText()
        start = text.index("1.e4")

        cursor = self.panel.txt_page.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + len("1.e4 c5"), QTextCursor.MoveMode.KeepAnchor)
        self.panel.txt_page.setTextCursor(cursor)

        sent: list[str] = []
        self.panel.text_requested_signal.connect(sent.append)
        self.panel.send_to_editor()

        self.assertEqual(["1.e4 c5"], sent)

    def test_a_multi_line_selection_keeps_its_line_breaks(self):
        """O Qt devolve U+2029 no lugar da quebra; sem trocar, vinha tudo numa linha."""
        self.panel.open_pdf(self.pdf)
        self.panel.txt_page.selectAll()

        sent: list[str] = []
        self.panel.text_requested_signal.connect(sent.append)
        self.panel.send_to_editor()

        self.assertIn("\n", sent[0])
        self.assertNotIn(" ", sent[0])

    def test_next_page_sends_and_advances(self):
        self.panel.open_pdf(self.pdf)
        sent: list[str] = []
        self.panel.text_requested_signal.connect(sent.append)

        self.panel.append_and_advance()

        self.assertEqual(1, self.panel.current_page())
        self.assertIn("1.e4 c5", sent[0])

    def test_the_last_page_does_not_advance_past_the_end(self):
        self.panel.open_pdf(self.pdf, page=2)
        self.panel.append_and_advance()

        self.assertEqual(2, self.panel.current_page())

    def test_without_a_document_the_controls_are_off(self):
        self.assertFalse(self.panel.btn_send.isEnabled())
        self.assertFalse(self.panel.btn_next.isEnabled())
        self.assertEqual("", self.panel.page_text(0))


class PdfInTheWindowTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name)
        self.pdf = write_pdf(self.tmp / "livro.pdf", PAGES)

        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)
        self.addCleanup(self.window.pdf_panel.close_pdf)

    def test_text_from_the_pdf_lands_at_the_cursor(self):
        self.window.editor_panel.load_document_text("")
        self.window.pdf_panel.open_pdf(self.pdf)

        self.window.pdf_panel.send_to_editor()

        self.assertIn("1.e4 c5", self.window.editor_panel.document_text())

    def test_the_panel_appears_when_a_pdf_is_opened(self):
        self.assertFalse(self.window.pdf_panel.isVisible())

        self.window.pdf_panel.open_pdf(self.pdf)

        self.assertEqual(str(self.pdf), self.window.project.state.pdf_path)

    def test_the_page_is_remembered_in_the_project(self):
        self.window.pdf_panel.open_pdf(self.pdf)
        self.window.pdf_panel.go_to_page(2)

        path = save_project(self.window.project, self.tmp / "trabalho")
        reopened = load_project(path)

        self.assertEqual(str(self.pdf), reopened.state.pdf_path)
        self.assertEqual(2, reopened.state.pdf_page)

    def test_reopening_the_project_puts_the_pdf_back_on_the_same_page(self):
        self.window.pdf_panel.open_pdf(self.pdf)
        self.window.pdf_panel.go_to_page(1)
        path = save_project(self.window.project, self.tmp / "trabalho")

        self.window.pdf_panel.close_pdf()
        self.window._load_project_path(path)

        self.assertTrue(self.window.pdf_panel.has_document())
        self.assertEqual(1, self.window.pdf_panel.current_page())

    def test_a_project_whose_pdf_moved_away_opens_anyway(self):
        project = Project(raw_text="1.e4")
        project.state.pdf_path = str(self.tmp / "sumiu.pdf")
        path = save_project(project, self.tmp / "órfão")

        self.window._load_project_path(path)

        self.assertFalse(self.window.pdf_panel.has_document())
        self.assertEqual("1.e4", self.window.editor_panel.document_text())


if __name__ == "__main__":
    unittest.main()
