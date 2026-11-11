"""O painel de PDF nao mistura um documento com outro, nem a pagina com ela mesma (R-305).

U-11: `open_pdf` nao limpava o cache de OCR nem cancelava o OCR em andamento:
a pagina 2 do livro B mostrava -- e "Enviar" enviava -- o OCR do livro A.
U-12: uma troca de pagina emitia `page_changed` duas vezes (dois renders).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401
from pdf_fixture import write_pdf
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.ui.pdf_panel import PdfPanel

_app = QApplication.instance() or QApplication([])


class PdfSessionTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        tmp = Path(self._temp.name)
        self.book_a = write_pdf(tmp / "a.pdf", [["1.e4 e5"], ["2.Cf3 Cc6"], ["3.Bb5 a6"]])
        self.book_b = write_pdf(tmp / "b.pdf", [["1.d4 d5"], ["2.c4 e6"]])
        self.panel = PdfPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)
        self.addCleanup(self.panel.close_pdf)

    def test_opening_another_pdf_forgets_the_ocr_of_the_first(self):
        self.panel.open_pdf(self.book_a)
        self.panel._ocr_cache[1] = "texto lido do livro A"
        self.panel._ocr_pending_page = 2
        self.panel._page_ocr.submit((2, []))

        self.assertEqual("", self.panel.open_pdf(self.book_b))

        self.assertEqual({}, self.panel._ocr_cache)
        self.assertIsNone(self.panel._ocr_pending_page)
        self.assertFalse(self.panel._page_ocr.busy, "o OCR do livro A nao pode chegar no livro B")

    def test_changing_page_announces_it_once(self):
        self.panel.open_pdf(self.book_a)
        announced: list[int] = []
        self.panel.page_changed_signal.connect(announced.append)

        self.panel.go_to_page(1)
        self.panel.spn_page.setValue(3)

        self.assertEqual([1, 2], announced)


if __name__ == "__main__":
    unittest.main()
