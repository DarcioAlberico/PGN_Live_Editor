"""Uma excecao num slot nao pode matar o programa (S-03 / R-005).

Sem `sys.excepthook` proprio, o PyQt6 chama `qFatal` quando um slot levanta:
o processo morre com 0xC0000409, sem traceback (o executavel nao tem console)
e sem gravar nada. Roda num subprocesso porque o pytest instala o proprio hook.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import _isolation  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from pgn_live_editor.services import crash_safety

    log_dir = Path(sys.argv[1])
    marker = log_dir / "emergencia.txt"
    crash_safety.install(log_dir)
    crash_safety.add_emergency_action(lambda: marker.write_text("snapshot", encoding="utf-8"))

    app = QApplication([])

    def broken_slot():
        raise RuntimeError("defeito de proposito num slot")

    QTimer.singleShot(0, broken_slot)
    QTimer.singleShot(200, app.quit)
    sys.exit(app.exec())
    """
)


class CrashSafetyTests(unittest.TestCase):
    def run_script(self, log_dir: Path) -> subprocess.CompletedProcess:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        return subprocess.run(
            [sys.executable, "-c", SCRIPT, str(log_dir)],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_an_exception_in_a_slot_is_logged_and_the_program_survives(self):
        log_dir = Path(os.environ["PGN_LIVE_EDITOR_HOME"]) / "crash-test"

        result = self.run_script(log_dir)

        self.assertEqual(0, result.returncode, result.stderr[-2000:])
        log_text = (log_dir / "pgn-live-editor.log").read_text(encoding="utf-8")
        self.assertIn("defeito de proposito num slot", log_text)
        self.assertIn("Traceback", log_text)
        self.assertEqual("snapshot", (log_dir / "emergencia.txt").read_text(encoding="utf-8"))


class WindowEmergencyTests(unittest.TestCase):
    """O que a janela registra no `crash_safety` em `__main__`."""

    def setUp(self):
        from PyQt6.QtWidgets import QApplication

        from pgn_live_editor.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)

    def test_the_emergency_snapshot_keeps_the_text(self):
        from pgn_live_editor.services.project_service import clear_recovery_snapshot, read_recovery_snapshot

        self.addCleanup(clear_recovery_snapshot)
        self.window.editor_panel.load_document_text("1. e4 e5 2. Nf3 {texto nao salvo}")

        self.window.emergency_snapshot()

        self.assertIn("texto nao salvo", read_recovery_snapshot().raw_text)

    def test_the_error_notice_does_not_block_the_window(self):
        self.window.show_unexpected_error("RuntimeError: teste")
        self.window.show_unexpected_error("RuntimeError: segundo")

        box = self.window._unexpected_error_box
        self.assertFalse(box.isModal())
        self.assertIn("segundo", box.text())


if __name__ == "__main__":
    unittest.main()
