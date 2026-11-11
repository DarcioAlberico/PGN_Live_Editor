"""Subprocessos que funcionam no executavel congelado (R-304, SPEC_V3 §4.4).

S-11: os workers chamavam `[sys.executable, "-m", "pgn_live_editor.services..."]`.
No exe do PyInstaller `sys.executable` e o proprio programa: `-m` nao existe
la, o filho abria uma **segunda janela** do editor e o OCR nunca respondia.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.services.worker_commands import WORKER_MODULES, worker_command

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandTests(unittest.TestCase):
    def test_from_source_the_child_runs_the_package_entry_point(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            command = worker_command("diagram-ocr", "a.pdf", "3")

        self.assertEqual([sys.executable, "-m", "pgn_live_editor", "--worker", "diagram-ocr", "a.pdf", "3"], command)

    def test_frozen_the_child_is_the_executable_itself(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            command = worker_command("full-pdf", "a.pdf")

        self.assertEqual([sys.executable, "--worker", "full-pdf", "a.pdf"], command)

    def test_an_unknown_worker_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            worker_command("nao-existe")


class DispatchTests(unittest.TestCase):
    def run_entry_point(self, *args: str) -> subprocess.CompletedProcess[str]:
        probe = (
            "import sys, runpy\n"
            f"sys.argv = ['pgn_live_editor', *{list(args)!r}]\n"
            "try:\n"
            "    runpy.run_module('pgn_live_editor', run_name='__main__')\n"
            "except SystemExit as exc:\n"
            "    code = exc.code\n"
            "else:\n"
            "    code = 0\n"
            "print('QT=' + str('PyQt6.QtWidgets' in sys.modules))\n"
            "raise SystemExit(code)\n"
        )
        return subprocess.run(
            [sys.executable, "-c", probe],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_each_worker_answers_without_starting_the_interface(self):
        for name in WORKER_MODULES:
            with self.subTest(worker=name):
                completed = self.run_entry_point("--worker", name, "--help")

                self.assertEqual(0, completed.returncode, completed.stderr[-500:])
                if name != "warm-up":  # o aquecimento nao recebe argumentos
                    self.assertIn("--output", completed.stdout)
                self.assertIn("QT=False", completed.stdout)

    def test_an_unknown_worker_fails_without_a_window(self):
        completed = self.run_entry_point("--worker", "nao-existe")

        self.assertEqual(2, completed.returncode)
        self.assertIn("nao-existe", completed.stderr)
        self.assertIn("QT=False", completed.stdout)


if __name__ == "__main__":
    unittest.main()
