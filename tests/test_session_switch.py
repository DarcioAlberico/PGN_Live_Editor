"""Um caminho so para trocar de projeto (R-104 / R-105 do docs/ROADMAP_V3.md).

Antes cada caminho -- novo, abrir, abrir recente, importar, recuperar -- trocava
pedacos diferentes da sessao:

- abrir um `.plez` reanalisava o texto **antigo** com a configuracao nova e
  reconciliava as decisoes do projeto novo contra ele: decisoes humanas
  voltavam a "pendente" (U-01);
- "Abrir recente" nao perguntava pelo trabalho nao salvo (U-03);
- importar herdava o dicionario e os tokens ignorados do projeto anterior: um
  `tDc6` era corrigido em silencio por uma regra que o projeto novo nao tem (U-08).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401
from PyQt6.QtWidgets import QApplication, QMessageBox

from pgn_live_editor.core.recognition_record import build_recognition_records
from pgn_live_editor.core.substitutions import SCOPE_PREFIX, Dictionary, Substitution
from pgn_live_editor.services.project_service import Project, save_project
from pgn_live_editor.ui.main_window import MainWindow

DECIDED_TEXT = "1. e4 e5 2. Xq9 Nc6 3. Bb5 Zz8 4. Ba4"


def decided_project(directory: Path) -> Path:
    project = Project(raw_text=DECIDED_TEXT)
    records = build_recognition_records(DECIDED_TEXT)
    for record in records:
        if record["raw_text"] in ("Xq9", "Zz8"):
            record["action"] = "rejected"
            record["decision"] = "rejeitado pelo revisor"
    project.state.recognition_records = records
    return save_project(project, directory / "decidido.plez")


def actions(window: MainWindow) -> dict[str, str]:
    return {
        record["raw_text"]: record["action"]
        for record in window.project.state.recognition_records
        if record["raw_text"] in ("Xq9", "Zz8")
    }


class SessionSwitchTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = Path(tempfile.mkdtemp(prefix="session-"))
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)

    def test_opening_a_project_keeps_its_human_decisions(self):
        path = decided_project(self.tmp)
        self.window.editor_panel.load_document_text("1. d4 d5 2. c4 e6 {outro texto qualquer}")
        self.window.project.raw_text = self.window.editor_panel.document_text()

        self.window._load_project_path(path)

        self.assertEqual({"Xq9": "rejected", "Zz8": "rejected"}, actions(self.window))

    def test_open_recent_asks_before_throwing_away_unsaved_work(self):
        path = decided_project(self.tmp)
        self.window.editor_panel.load_document_text("1. e4 {trabalho que nao foi salvo}")

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as asked:
            self.window.open_path(path)

        asked.assert_called_once()
        self.assertIn("trabalho que nao foi salvo", self.window.editor_panel.document_text())

    def test_every_recent_file_entry_goes_through_the_same_gate(self):
        path = decided_project(self.tmp)
        self.window._load_project_path(path)
        self.window.editor_panel.load_document_text("1. e4 {nao salvo}")
        action = next(a for a in self.window.recent_menu.actions() if a.statusTip() == str(path))

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as asked:
            action.trigger()

        asked.assert_called_once()
        self.assertIn("nao salvo", self.window.editor_panel.document_text())

    def test_importing_does_not_inherit_the_previous_dictionary_or_ignored_tokens(self):
        previous = Project(raw_text="1. e4 e5")
        previous.dictionary = Dictionary([Substitution("tD", "N", scope=SCOPE_PREFIX)])
        previous.state.ignored_issue_tokens = ["Qz9"]
        self.window._load_project_path(save_project(previous, self.tmp / "anterior.plez"))
        source = self.tmp / "capitulo.txt"
        source.write_text("1. e4 tDc6 2. Qz9", encoding="utf-8")

        self.window.import_path(source)

        self.assertEqual([], self.window.dictionary_service.project.entries)
        config = self.window.editor_panel.async_parser.config
        self.assertEqual((), tuple(config.ignored_tokens))
        flagged = {issue.raw_text for issue in self.window.editor_panel.current_ast.issues}
        self.assertIn("Qz9", flagged, "o token ignorado no projeto anterior volta a ser alerta")
        self.assertIn("tDc6", flagged, "sem o dicionario anterior, tDc6 nao e corrigido em silencio")

    def test_a_new_project_starts_without_the_previous_session(self):
        previous = Project(raw_text="1. e4 e5")
        previous.state.ignored_issue_tokens = ["Qz9"]
        previous.dictionary = Dictionary([Substitution("tD", "N", scope=SCOPE_PREFIX)])
        self.window._load_project_path(save_project(previous, self.tmp / "anterior.plez"))

        self.window.new_project()

        self.assertEqual([], self.window.dictionary_service.project.entries)
        self.assertEqual((), tuple(self.window.editor_panel.async_parser.config.ignored_tokens))
        self.assertFalse(self.window.pdf_panel.has_document())

    def test_a_late_analysis_of_the_old_text_does_not_touch_the_new_project(self):
        path = decided_project(self.tmp)
        self.window._load_project_path(path)
        stale = mock.Mock(raw_text="1. d4 {texto que ja saiu do editor}")

        self.window._on_document_analyzed(stale)

        self.assertEqual({"Xq9": "rejected", "Zz8": "rejected"}, actions(self.window))


if __name__ == "__main__":
    unittest.main()
