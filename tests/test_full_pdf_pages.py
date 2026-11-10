"""PDF inteiro pagina a pagina: uma queda no meio nao perde o que ja foi lido (R-109).

Antes o processo de reconhecimento gravava o resultado so no fim; morrer na
pagina 300 perdia as 299 anteriores. E uma falha do OCR de texto aparecia como
"nao ha camada de texto" -- o usuario ia procurar o problema no PDF (S-15).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.services import full_pdf_recognition_process as process
from pgn_live_editor.services.full_pdf_recognition_worker import collect_pages


def page(number: int, text: str = "1. e4 e5") -> dict:
    return {"page": number, "text": text, "text_method": "text-layer", "diagrams": [], "ocr_error": ""}


class CollectPagesTests(unittest.TestCase):
    def setUp(self):
        self.output = Path(tempfile.mkdtemp(prefix="fullpdf-")) / "paginas.jsonl"

    def write_lines(self, *lines: str) -> None:
        self.output.write_text("".join(lines), encoding="utf-8")

    def test_a_complete_run_gives_every_page(self):
        self.write_lines(*(json.dumps(page(n)) + "\n" for n in (1, 2, 3)))

        pages = collect_pages(self.output, return_code=0, stderr_tail="")

        self.assertEqual([1, 2, 3], [p["page"] for p in pages])

    def test_a_crash_keeps_the_pages_already_read_and_says_where_it_stopped(self):
        self.write_lines(json.dumps(page(1)) + "\n", json.dumps(page(2)) + "\n", '{"page": 3, "te')

        pages = collect_pages(self.output, return_code=3, stderr_tail="MemoryError: sem memoria")

        self.assertEqual([1, 2, 3], [p["page"] for p in pages])
        self.assertEqual("interrupted", pages[-1]["text_method"])
        self.assertIn("MemoryError", pages[-1]["ocr_error"])

    def test_a_crash_before_any_page_is_an_error_with_the_reason(self):
        self.write_lines("")

        with self.assertRaises(ValueError) as raised:
            collect_pages(self.output, return_code=1, stderr_tail="ImportError: No module named 'torch'")

        self.assertIn("torch", str(raised.exception))


class TextOcrFailureTests(unittest.TestCase):
    def test_an_ocr_failure_is_not_reported_as_a_page_without_text(self):
        fake_page = mock.Mock()
        fake_page.get_pixmap.return_value = mock.Mock(samples=b"\x00" * 12, width=2, height=2)

        with mock.patch.object(process, "recognize_renders", side_effect=RuntimeError("OCR do Windows recusou")):
            text, method, error = process._ocr_page_text(fake_page)

        self.assertEqual(("", "ocr-failed"), (text, method))
        self.assertIn("recusou", error)


class DocumentRenderingTests(unittest.TestCase):
    def test_interruption_and_ocr_failure_are_spelled_out_in_the_text(self):
        from pgn_live_editor.ui.main_window import _full_pdf_document

        pages = [
            page(1),
            {"page": 2, "text": "", "text_method": "ocr-failed", "text_error": "OCR recusou", "diagrams": []},
            {"page": 3, "text": "", "text_method": "interrupted", "ocr_error": "MemoryError", "diagrams": []},
        ]

        text, _records, _summary = _full_pdf_document(pages)

        self.assertIn("OCR de texto falhou nesta página: OCR recusou", text)
        self.assertIn("Reconhecimento interrompido", text)
        self.assertNotIn("não há camada de texto", text)


if __name__ == "__main__":
    unittest.main()
