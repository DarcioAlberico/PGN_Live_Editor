"""Aquecimento opcional do OCR de diagramas (R-405).

A primeira leitura de diagramas pagava ~20 s so de importar torch/OpenCV/
PyMuPDF com o disco frio (O-11), e o usuario achava que o programa tinha
travado. Com o extra instalado e um PDF aberto, um processo filho de baixa
prioridade importa o servico uma vez por sessao -- a leitura de verdade
encontra o disco quente.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.services import ocr_warm_up
from pgn_live_editor.services.worker_commands import WORKER_MODULES, run_worker


class WarmUpTests(unittest.TestCase):
    def setUp(self):
        ocr_warm_up._started = False

    def test_without_the_extra_nothing_is_started(self):
        with (
            mock.patch.object(ocr_warm_up, "diagram_ocr_installed", return_value=False),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertFalse(ocr_warm_up.warm_up_diagram_ocr())
        popen.assert_not_called()

    def test_with_the_extra_it_starts_once_per_session(self):
        with (
            mock.patch.object(ocr_warm_up, "diagram_ocr_installed", return_value=True),
            mock.patch("subprocess.Popen") as popen,
        ):
            self.assertTrue(ocr_warm_up.warm_up_diagram_ocr())
            self.assertFalse(ocr_warm_up.warm_up_diagram_ocr())
        self.assertEqual(1, popen.call_count)
        command = popen.call_args.args[0]
        self.assertEqual(["--worker", "warm-up"], command[-2:])

    def test_a_failure_to_start_is_quiet(self):
        with (
            mock.patch.object(ocr_warm_up, "diagram_ocr_installed", return_value=True),
            mock.patch("subprocess.Popen", side_effect=OSError("sem processo")),
        ):
            self.assertFalse(ocr_warm_up.warm_up_diagram_ocr())

    def test_the_warm_up_subcommand_exists_and_never_fails_loudly(self):
        self.assertIn("warm-up", WORKER_MODULES)
        # No venv de desenvolvimento o extra nao esta instalado: sai com 0 mesmo assim.
        with mock.patch.object(sys, "stderr"):
            self.assertEqual(0, run_worker(["warm-up"]))

    def test_installed_check_does_not_import_anything(self):
        before = set(sys.modules)
        ocr_warm_up.diagram_ocr_installed()
        self.assertFalse({"torch", "cv2", "pymupdf"} & (set(sys.modules) - before))


class WindowTriggerTests(unittest.TestCase):
    def test_opening_a_pdf_schedules_the_warm_up(self):
        import tempfile
        from pathlib import Path

        from pdf_fixture import write_pdf
        from PyQt6.QtWidgets import QApplication, QMessageBox

        from pgn_live_editor.ui.main_window import MainWindow

        self.app = app = QApplication.instance() or QApplication([])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            window = MainWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.editor_panel.shutdown)
        self.addCleanup(window.pdf_panel.close_pdf)
        pdf = write_pdf(Path(tempfile.mkdtemp()) / "livro.pdf", [["1.e4 e5"]])

        with mock.patch.object(ocr_warm_up, "warm_up_diagram_ocr") as warm:
            window.pdf_panel.open_pdf(pdf)
            window._warm_up_timer.timeout.emit()
            app.processEvents()

        warm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
