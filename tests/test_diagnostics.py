"""Diagnostico: o que o suporte precisa saber, sem nunca levar o texto do livro."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.__main__ import main
from pgn_live_editor.services.diagnostics import build_diagnostic_report


class DiagnosticReportTests(unittest.TestCase):
    def test_the_optional_parts_are_reported(self):
        with (
            mock.patch("pgn_live_editor.services.engine_service.discover_engine_path", return_value="C:/motor/sf.exe"),
            mock.patch("pgn_live_editor.services.text_ocr.text_ocr_available", return_value=False),
        ):
            report = build_diagnostic_report()

        self.assertIn("Motor de xadrez: C:/motor/sf.exe", report)
        self.assertIn("OCR de texto: indisponível", report)

    def test_missing_engine_is_said_plainly(self):
        with mock.patch("pgn_live_editor.services.engine_service.discover_engine_path", return_value=""):
            self.assertIn("Motor de xadrez: nenhum encontrado", build_diagnostic_report())

    def test_a_broken_optional_part_does_not_break_the_report(self):
        with mock.patch("pgn_live_editor.services.text_ocr.text_ocr_available", side_effect=RuntimeError("winrt")):
            self.assertIn("OCR de texto: erro (winrt)", build_diagnostic_report())


class DiagnosticCommandLineTests(unittest.TestCase):
    def test_the_flag_writes_the_report_without_opening_a_window(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "diag.txt"
            with (
                mock.patch.object(sys, "argv", ["pgn-live-editor", "--diagnostico", str(target)]),
                mock.patch("PyQt6.QtWidgets.QApplication") as application,
                self.assertRaises(SystemExit) as raised,
            ):
                main()

            self.assertEqual(0, raised.exception.code)
            application.assert_not_called()
            self.assertIn("PGN Live Editor — diagnóstico", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
