"""Recuperacao de sessao v2 (R-102 do docs/ROADMAP_V3.md).

Antes o snapshot guardava so o texto; recuperar e salvar gravava por cima do
`.plez` um projeto sem cabecalhos, dicionario, partidas concluidas e diagramas
(S-01). E havia um unico `recovery/session.json`: a segunda instancia aberta
sobrescrevia e, ao fechar, apagava a recuperacao da primeira (S-14).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import _isolation  # noqa: F401

from pgn_live_editor.core.substitutions import SCOPE_PREFIX, Dictionary, Substitution
from pgn_live_editor.services.project_service import Project, load_project, recovery_dir, save_project
from pgn_live_editor.services.recovery import RecoverySession, discard, orphan_snapshots, restore_project

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def rich_project(directory: Path) -> Project:
    project = Project(raw_text="1. e4 e5 2. Nf3 Nc6")
    project.meta.title = "Meu livro"
    project.header_template = {"Event": "Torneio", "White": "Smyslov"}
    project.dictionary = Dictionary([Substitution("tD", "N", scope=SCOPE_PREFIX)])
    project.state.completed_games = [0, 3]
    project.state.ignored_issue_tokens = ["Xq9"]
    project.state.diagram_records = [{"page": 4, "fen": "8/8/8/8/8/8/8/K6k w - - 0 1", "review": "accepted"}]
    save_project(project, directory / "livro.plez")
    return project


def run_instance(script: str, *args: str, wait: bool = False):
    """Outra instancia do programa: so o servico de recuperacao, num processo a parte."""
    code = textwrap.dedent(script)
    command = [sys.executable, "-c", code, *args]
    env = dict(os.environ)
    if wait:
        return subprocess.Popen(
            command, cwd=PROJECT_ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
    return subprocess.run(command, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=60)


WRITE_AND_CRASH = """
    import os, sys
    from pathlib import Path
    from pgn_live_editor.services.project_service import load_project
    from pgn_live_editor.services.recovery import RecoverySession
    project = load_project(Path(sys.argv[1]))
    RecoverySession().write(project, sys.argv[2], cursor_position=7)
    os._exit(1)   # sem limpar nada: e o crash
"""

UNTITLED_AND_CRASH = """
    import json, os, sys
    from pgn_live_editor.services.project_service import Project
    from pgn_live_editor.services.recovery import RecoverySession
    project = Project(raw_text="rascunho", header_template=json.loads(sys.argv[1]))
    RecoverySession().write(project, sys.argv[2])
    os._exit(1)
"""

WRITE_AND_STAY_ALIVE = """
    import sys
    from pgn_live_editor.services.project_service import Project
    from pgn_live_editor.services.recovery import RecoverySession
    session = RecoverySession()
    session.write(Project(raw_text=sys.argv[1]), sys.argv[1])
    print("pronto", flush=True)
    sys.stdin.readline()
"""


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        for path in recovery_dir().iterdir():
            path.unlink()
        self.tmp = Path(tempfile.mkdtemp(prefix="recovery-"))

    def test_recovering_after_a_crash_and_saving_keeps_the_whole_project(self):
        rich_project(self.tmp)
        origin = self.tmp / "livro.plez"
        result = run_instance(WRITE_AND_CRASH, str(origin), "1. e4 e5 2. Nf3 Nc6 3. Bb5 {editado}")
        self.assertEqual(1, result.returncode, result.stderr[-1500:])

        (snapshot,) = orphan_snapshots()
        restored = restore_project(snapshot)
        save_project(restored, restored.path)
        reopened = load_project(origin)

        self.assertEqual("1. e4 e5 2. Nf3 Nc6 3. Bb5 {editado}", reopened.raw_text)
        self.assertEqual("Meu livro", reopened.meta.title)
        self.assertEqual({"Event": "Torneio", "White": "Smyslov"}, reopened.header_template)
        self.assertEqual(["tD"], [entry.pattern for entry in reopened.dictionary.entries])
        self.assertEqual([0, 3], reopened.state.completed_games)
        self.assertEqual(["Xq9"], reopened.state.ignored_issue_tokens)
        self.assertEqual(4, reopened.state.diagram_records[0]["page"])
        self.assertEqual(7, snapshot.cursor_position)
        self.assertLess(snapshot.age_seconds, 60)

    def test_a_running_instance_does_not_see_or_erase_the_other_instance_snapshot(self):
        other = run_instance(WRITE_AND_STAY_ALIVE, "texto da outra janela", wait=True)
        self.addCleanup(other.wait, 30)
        self.addCleanup(other.stdin.close)
        self.assertEqual("pronto", other.stdout.readline().strip())

        mine = RecoverySession()
        mine.write(Project(raw_text="meu texto"), "meu texto")
        self.assertEqual([], orphan_snapshots(), "a outra instancia esta viva: nada e orfao")

        mine.clear()
        survivors = [
            json.loads(p.read_text(encoding="utf-8"))["project"]["raw_text"] for p in recovery_dir().glob("*.json")
        ]
        self.assertEqual(["texto da outra janela"], survivors)

    def test_a_snapshot_whose_instance_closed_cleanly_is_gone(self):
        session = RecoverySession()
        session.write(Project(raw_text="algo"), "algo")

        session.clear()

        self.assertEqual([], list(recovery_dir().glob("*.json")))

    def test_the_newest_orphan_comes_first(self):
        rich_project(self.tmp)
        origin = str(self.tmp / "livro.plez")
        run_instance(WRITE_AND_CRASH, origin, "primeiro")
        time.sleep(0.05)
        run_instance(WRITE_AND_CRASH, origin, "segundo")

        self.assertEqual(["segundo", "primeiro"], [s.raw_text for s in orphan_snapshots()])

    def test_discarding_removes_the_orphan(self):
        run_instance(WRITE_AND_CRASH, str(save_project(Project(raw_text="x"), self.tmp / "a.plez")), "texto")
        (snapshot,) = orphan_snapshots()

        discard(snapshot)

        self.assertEqual([], orphan_snapshots())

    def test_a_snapshot_without_origin_restores_as_untitled(self):
        run_instance(UNTITLED_AND_CRASH, json.dumps({"Event": "Treino"}), "rascunho editado")

        (snapshot,) = orphan_snapshots()
        restored = restore_project(snapshot)

        self.assertIsNone(restored.path)
        self.assertEqual("rascunho editado", restored.raw_text)
        self.assertEqual({"Event": "Treino"}, restored.header_template)

    def test_the_old_single_file_snapshot_still_restores_over_its_origin(self):
        rich_project(self.tmp)
        origin = self.tmp / "livro.plez"
        legacy = {
            "version": 3,
            "raw_text": "texto antigo recuperado",
            "origin_path": str(origin),
            "saved_at": time.time(),
        }
        (recovery_dir() / "session.json").write_text(json.dumps(legacy), encoding="utf-8")

        (snapshot,) = orphan_snapshots()
        restored = restore_project(snapshot)

        self.assertEqual("texto antigo recuperado", restored.raw_text)
        self.assertEqual({"Event": "Torneio", "White": "Smyslov"}, restored.header_template)
        self.assertEqual(origin, restored.path)

    def test_malformed_snapshots_are_skipped_not_raised(self):
        for index, payload in enumerate(
            ["[1, 2]", '{"project": 5}', '{"saved_at": "ontem"}', "nao e json", '{"project": {"raw_text": 7}}']
        ):
            (recovery_dir() / f"lixo{index}.json").write_text(payload, encoding="utf-8")

        self.assertEqual([], orphan_snapshots())

    def test_blank_text_is_not_worth_a_snapshot(self):
        run_instance(UNTITLED_AND_CRASH, "{}", "   \n  ")

        self.assertEqual([], orphan_snapshots())


class WindowRecoveryTests(unittest.TestCase):
    """O mesmo caminho pela janela: o que o usuario faz depois de um crash (S-01 / U-02)."""

    def setUp(self):
        from PyQt6.QtWidgets import QApplication

        for path in recovery_dir().iterdir():
            path.unlink()
        self.app = QApplication.instance() or QApplication([])
        self.tmp = Path(tempfile.mkdtemp(prefix="recovery-ui-"))

    def test_recover_then_save_keeps_headers_dictionary_and_progress(self):
        from unittest import mock

        from PyQt6.QtWidgets import QMessageBox

        from pgn_live_editor.ui.main_window import MainWindow

        rich_project(self.tmp)
        origin = self.tmp / "livro.plez"
        run_instance(WRITE_AND_CRASH, str(origin), "1. e4 e5 2. Nf3 Nc6 3. Bb5 {depois do crash}")

        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window = MainWindow()
            self.addCleanup(window.deleteLater)
            window._offer_session_recovery()

        self.assertTrue(window._is_dirty(), "o texto recuperado ainda nao esta no .plez")
        self.assertEqual([], orphan_snapshots(), "o orfao foi assumido por esta janela")
        self.assertTrue(window.recovery.snapshot_path.exists())

        self.assertTrue(window._write_project(origin))
        reopened = load_project(origin)
        self.assertIn("depois do crash", reopened.raw_text)
        self.assertEqual({"Event": "Torneio", "White": "Smyslov"}, reopened.header_template)
        self.assertEqual(["tD"], [entry.pattern for entry in reopened.dictionary.entries])
        self.assertEqual([0, 3], reopened.state.completed_games)
        self.assertEqual(4, reopened.state.diagram_records[0]["page"])
        self.assertFalse(window.recovery.snapshot_path.exists(), "salvo: o snapshot nao e mais preciso")


if __name__ == "__main__":
    unittest.main()
