"""OCR de texto para PDF escaneado (`services/text_ocr.py` e o painel de PDF).

O fluxo do painel e testado com um OCR de mentira: o que se confere e que a
pagina sem texto dispara a leitura, que o resultado cai no lugar do texto
extraido e que "proxima pagina e anexar" espera a leitura terminar. Um teste a
parte usa o OCR real do Windows e pula onde ele nao existe.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pdf_fixture import write_pdf  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.services.text_ocr import (  # noqa: E402
    OCR_DPIS,
    Line,
    OcrPage,
    TextOcrUnavailable,
    Word,
    _missing_line_regions,
    merge_readings,
    recognize_bgra,
    recognize_renders,
    rgb_to_bgra,
    text_ocr_available,
    tidy_ocr_notation,
)
from pgn_live_editor.ui.pdf_panel import PdfPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])


def wait_for(condition, timeout: float = 10.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        _app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


class TidyOcrNotationTests(unittest.TestCase):
    def test_lowercase_victim_after_capture_is_raised(self):
        self.assertEqual("5. CxC, PxC; 8. PxC", tidy_ocr_notation("5. CXC, P xc; 8. Pxc"))

    def test_capital_x_between_pieces_is_a_capture(self):
        self.assertEqual("8. DxP, D3C 9. AxP +", tidy_ocr_notation("8. DXP, D3C 9. A X P +"))

    def test_san_and_prose_are_left_alone(self):
        for text in ("3. exd5 Nxe5", "La Xunta de Galicia", "Alex Xavier", "a x b"):
            self.assertEqual(text, tidy_ocr_notation(text))


class MergeReadingsTests(unittest.TestCase):
    """Juntar leituras da mesma pagina feitas em resolucoes diferentes."""

    @staticmethod
    def line(*words: tuple[str, float]) -> Line:
        return Line([Word(text, x, 100, 30, 10) for text, x in words])

    def test_a_word_missing_in_one_reading_comes_from_the_other(self):
        first = [self.line(("3.", 0), ("P4D", 40), ("4.", 120), ("CxP", 160))]
        second = [self.line(("3.", 1), ("P4D", 41), ("PxP", 80), ("4.", 121))]

        merged = merge_readings([first, second])

        self.assertEqual("3. P4D PxP 4. CxP", merged[0].text())

    def test_the_same_word_is_not_duplicated(self):
        first = [self.line(("P4R", 0), ("P4AD", 40))]
        second = [self.line(("P4R", 2), ("P4AD", 41))]

        self.assertEqual("P4R P4AD", merge_readings([first, second])[0].text())

    def test_a_whole_line_missing_is_inserted_in_reading_order(self):
        first = [Line([Word("Continuacion", 0, 50, 80, 10)]), Line([Word("7. P3TD", 0, 150, 60, 10)])]
        second = [Line([Word("PxP", 0, 100, 30, 10), Word("AxP", 40, 100, 30, 10)])]

        merged = merge_readings([first, second])

        self.assertEqual(["Continuacion", "PxP AxP", "7. P3TD"], [line.text() for line in merged])


class MissingLineRegionTests(unittest.TestCase):
    """Onde cabe uma linha inteira que o OCR nao devolveu."""

    @staticmethod
    def line(top: float, left: float = 0, right: float = 200) -> Line:
        return Line([Word("texto", left, top, right - left, 10)])

    def test_a_one_line_hole_in_a_column_is_found(self):
        lines = [self.line(0), self.line(12), self.line(36), self.line(48)]  # falta a de 24

        regions = _missing_line_regions(lines)

        self.assertEqual([1], [index for index, _probe in regions])

    def test_paragraph_and_diagram_gaps_are_ignored(self):
        lines = [self.line(0), self.line(12), self.line(24), self.line(200), self.line(212)]

        self.assertEqual([], _missing_line_regions(lines))

    def test_the_other_column_is_not_the_line_below(self):
        left = [self.line(0), self.line(12), self.line(24)]
        right = [self.line(40, 300, 500), self.line(52, 300, 500)]

        self.assertEqual([], _missing_line_regions(left + right))


class RgbConversionTests(unittest.TestCase):
    def test_rgb_becomes_opaque_bgra(self):
        self.assertEqual(b"\x03\x02\x01\xff\x06\x05\x04\xff", rgb_to_bgra(b"\x01\x02\x03\x04\x05\x06", 2, 1))

    def test_wrong_size_is_an_error(self):
        with self.assertRaises(ValueError):
            rgb_to_bgra(b"\x00" * 5, 2, 1)


class AvailabilityTests(unittest.TestCase):
    def test_without_the_windows_engine_nothing_breaks(self):
        with mock.patch("pgn_live_editor.services.text_ocr._engine", return_value=None):
            self.assertFalse(text_ocr_available())
            with self.assertRaises(TextOcrUnavailable):
                recognize_bgra(b"\xff" * 16, 2, 2)


class ScannedPagePanelTests(unittest.TestCase):
    """O painel com um OCR de mentira."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Pagina 1 escaneada (sem texto), pagina 2 com camada de texto.
        self.pdf = write_pdf(Path(self.tmp.name) / "livro.pdf", [[], ["1. P4R P4R 2. C3AR"]])
        self.fake_ocr = mock.Mock(return_value=OcrPage(text="1. P4R P4AD 2. C3AR P3R", lines=1))
        for target, value in (
            ("pgn_live_editor.ui.pdf_panel.text_ocr_available", mock.Mock(return_value=True)),
            ("pgn_live_editor.ui.pdf_panel.recognize_renders", self.fake_ocr),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.panel = PdfPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)

    def test_a_page_without_text_is_read_by_ocr(self):
        self.panel.open_pdf(self.pdf, 0)

        self.assertTrue(wait_for(lambda: self.panel._ocr_pending_page is None))
        self.assertEqual("1. P4R P4AD 2. C3AR P3R", self.panel.txt_page.toPlainText())
        self.assertTrue(self.panel.btn_text_ocr.isVisibleTo(self.panel))

    def test_a_page_with_a_text_layer_does_not_call_the_ocr(self):
        self.panel.open_pdf(self.pdf, 1)

        self.assertIn("P4R", self.panel.txt_page.toPlainText())
        self.fake_ocr.assert_not_called()

    def test_the_result_is_remembered_per_page(self):
        self.panel.open_pdf(self.pdf, 0)
        wait_for(lambda: self.panel._ocr_pending_page is None)
        self.panel.go_to_page(1)
        self.panel.go_to_page(0)

        self.assertEqual(1, self.fake_ocr.call_count)
        self.assertEqual("1. P4R P4AD 2. C3AR P3R", self.panel.txt_page.toPlainText())

    def test_next_page_waits_for_the_reading(self):
        sent: list[str] = []
        self.panel.text_requested_signal.connect(sent.append)
        self.panel.open_pdf(self.pdf, 0)

        self.panel.append_and_advance()

        self.assertTrue(wait_for(lambda: bool(sent)))
        self.assertEqual(["1. P4R P4AD 2. C3AR P3R"], sent)

    def test_ocr_failure_is_shown_not_raised(self):
        self.fake_ocr.side_effect = RuntimeError("sem idioma")
        self.panel.open_pdf(self.pdf, 0)

        self.assertTrue(wait_for(lambda: self.panel._ocr_pending_page is None))
        self.assertIn("sem idioma", self.panel.txt_page.placeholderText())


class RangeReadingTests(unittest.TestCase):
    """ "Ler intervalo (OCR)": varias paginas, texto e imagem misturados."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Paginas 1 e 3 escaneadas, 2 com texto.
        self.pdf = write_pdf(Path(self.tmp.name) / "livro.pdf", [[], ["2. C3AR C3AD"], []])
        self.fake_ocr = mock.Mock(side_effect=lambda renders: OcrPage(text=f"lido {len(renders)}", lines=1))
        for target, value in (
            ("pgn_live_editor.ui.pdf_panel.text_ocr_available", mock.Mock(return_value=True)),
            ("pgn_live_editor.ui.pdf_panel.recognize_renders", self.fake_ocr),
        ):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.panel = PdfPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)
        self.sent: list[str] = []
        self.panel.text_requested_signal.connect(self.sent.append)

    def test_the_whole_range_arrives_in_order_with_page_marks(self):
        self.panel.open_pdf(self.pdf, 1)  # pagina com texto: nenhum OCR automatico
        wait_for(lambda: self.panel._ocr_pending_page is None)

        self.panel.read_range_with_ocr(0, 2)

        self.assertTrue(wait_for(lambda: not self.panel.is_reading_range))
        self.assertEqual(1, len(self.sent))
        blocks = self.sent[0].split("\n\n")
        self.assertEqual(
            ["{ PDF página 1 }", "{ PDF página 2 }", "{ PDF página 3 }"],
            [block.splitlines()[0] for block in blocks],
        )
        self.assertIn("2. C3AR C3AD", blocks[1])
        self.assertIn("lido", blocks[2])

    def test_cancelling_sends_nothing(self):
        self.panel.open_pdf(self.pdf, 1)
        self.panel.read_range_with_ocr(0, 2)
        self.panel.toggle_range_reading()  # cancelar

        wait_for(lambda: False, timeout=0.3)
        self.assertEqual([], self.sent)
        self.assertEqual("Ler intervalo (OCR)", self.panel.btn_range_ocr.text())


class WithoutOcrPanelTests(unittest.TestCase):
    def test_scanned_page_says_why_it_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = write_pdf(Path(folder) / "livro.pdf", [[]])
            with mock.patch("pgn_live_editor.ui.pdf_panel.text_ocr_available", return_value=False):
                panel = PdfPanel()
            self.addCleanup(panel.deleteLater)
            panel.open_pdf(pdf, 0)

            self.assertFalse(panel.btn_text_ocr.isVisibleTo(panel))
            self.assertIn("escaneada", panel.txt_page.placeholderText())
            panel.close_pdf()


@unittest.skipUnless(text_ocr_available(), "sem OCR do Windows nesta máquina")
class WindowsOcrTests(unittest.TestCase):
    def test_descriptive_notation_is_read_from_a_rendered_page(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = write_pdf(Path(folder) / "livro.pdf", [["1. P4R P4AD 2. C3AR P3R 3. P4D PxP 4. CxP C3AR"]])
            panel = PdfPanel()
            self.addCleanup(panel.deleteLater)
            panel.open_pdf(pdf, 0)
            renders = [(*panel.render_page_bgra(0, dpi), dpi) for dpi in OCR_DPIS]
            panel.close_pdf()

        # Uma resolucao so perde `PxP`; juntas, nao.
        text = recognize_renders(renders).text

        for move in ("P4R", "P4AD", "C3AR", "P3R", "P4D", "PxP"):
            self.assertIn(move, text)


if __name__ == "__main__":
    unittest.main()
