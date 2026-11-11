"""Abrir a janela nao carrega o OCR (R-405).

O-11: importar o servico de OCR de diagramas custa 1,7 s com o disco quente e
20,7 s frio. A janela tem de abrir no mesmo tempo com e sem o extra
instalado: nada de torch, OpenCV, numpy ou PyMuPDF ate alguem pedir uma
leitura -- e a leitura roda num processo filho, que paga o custo la.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import _isolation  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAVY = ("torch", "cv2", "numpy", "fitz", "pymupdf")

PROBE = """
import sys
# Anota toda TENTATIVA de importar um pesado -- mesmo sem o extra instalado
# (o venv de desenvolvimento nao tem), o que importa e o codigo nao tentar.
tried = set()
class Watch:
    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root in {heavy!r}:
            tried.add(root)
        return None
sys.meta_path.insert(0, Watch())
sys.path.insert(0, "tests")
import _isolation
from unittest import mock
from PyQt6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
from pgn_live_editor.ui.main_window import MainWindow
with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
    window = MainWindow()
window.show()
app.processEvents()
print("PESADOS=" + ",".join(sorted(tried)))
window.editor_panel.shutdown()
"""


class StartupImportTests(unittest.TestCase):
    def test_opening_the_window_imports_no_ocr_dependency(self):
        completed = subprocess.run(
            [sys.executable, "-c", PROBE.format(heavy=HEAVY)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"},
        )

        self.assertEqual(0, completed.returncode, completed.stderr[-800:])
        self.assertIn("PESADOS=\n", completed.stdout + "\n")


class PyMuPdfNameTests(unittest.TestCase):
    def test_no_module_imports_the_deprecated_fitz_name(self):
        # `import fitz` imprime aviso de depreciacao no PyMuPDF atual; o nome e `pymupdf`.
        offenders = [
            str(path.relative_to(PROJECT_ROOT))
            for path in (PROJECT_ROOT / "pgn_live_editor").rglob("*.py")
            if "import fitz" in path.read_text(encoding="utf-8")
        ]

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
