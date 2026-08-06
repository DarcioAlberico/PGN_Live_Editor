"""Fase 1: o trabalho nao pode ser perdido."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pgn_live_editor.services import project_service
from pgn_live_editor.services.project_service import (
    PROJECT_SUFFIX,
    Project,
    ProjectMeta,
    ProjectState,
    clear_recovery_snapshot,
    import_text_file,
    load_project,
    load_recent_files,
    read_recovery_snapshot,
    remember_recent_file,
    save_project,
    write_recovery_snapshot,
)

BOOK_TEXT = (
    '[Event "XIV Campeonato Sovietico"]\n'
    '[White "V. SMYSLOV"]\n\n'
    "{Defesa Siciliana} 1.e4 c5 2.Nf3 e6 3.d4 cxd4 (3... Nf6 4.e5) 4.Nxd4\n"
)


class ProjectRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_saved_project_reopens_with_everything_intact(self):
        project = Project(
            raw_text=BOOK_TEXT,
            meta=ProjectMeta(title="Smyslov", author="V. Smyslov", source="Livro X", locale="pt"),
            header_template={"Event": "Torneio", "Site": "Moscou"},
            dictionary={"tD": "N"},
            state=ProjectState(cursor_position=42, ignored_issue_tokens=["[D]"]),
        )

        path = save_project(project, self.tmp / "trabalho")
        reopened = load_project(path)

        self.assertEqual(PROJECT_SUFFIX, path.suffix)
        self.assertEqual(BOOK_TEXT, reopened.raw_text)
        self.assertEqual("Smyslov", reopened.meta.title)
        self.assertEqual("pt", reopened.meta.locale)
        self.assertEqual({"Event": "Torneio", "Site": "Moscou"}, reopened.header_template)
        self.assertEqual({"tD": "N"}, reopened.dictionary)
        self.assertEqual(42, reopened.state.cursor_position)
        self.assertEqual(["[D]"], reopened.state.ignored_issue_tokens)
        self.assertGreater(reopened.meta.saved_at, 0.0)

    def test_accented_text_survives_the_zip(self):
        text = "{Excelente desenvolvimento das pretas. Ameaça mate.} 1.e4 e5"
        path = save_project(Project(raw_text=text), self.tmp / "acentos")

        self.assertEqual(text, load_project(path).raw_text)

    def test_saving_over_an_existing_project_is_atomic(self):
        path = save_project(Project(raw_text="versao 1"), self.tmp / "trabalho")
        save_project(Project(raw_text="versao 2"), path)

        self.assertEqual("versao 2", load_project(path).raw_text)
        self.assertEqual([], list(self.tmp.glob("*.tmp")), "sobrou arquivo temporario")
        self.assertEqual(1, len(list(self.tmp.glob(f"*{PROJECT_SUFFIX}"))))

    def test_opening_a_file_that_is_not_a_project_fails_clearly(self):
        stray = self.tmp / "qualquer.plez"
        stray.write_text("isto nao e um zip", encoding="utf-8")

        with self.assertRaises(ValueError):
            load_project(stray)

    def test_opening_a_zip_without_raw_text_fails_clearly(self):
        broken = self.tmp / "quebrado.plez"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("meta.json", "{}")

        with self.assertRaises(ValueError):
            load_project(broken)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_import_reads_utf8_and_legacy_encodings(self):
        utf8_path = self.tmp / "utf8.pgn"
        utf8_path.write_text("{ameaça} 1.e4", encoding="utf-8")

        cp1252_path = self.tmp / "cp1252.pgn"
        cp1252_path.write_bytes("{ameaça} 1.e4".encode("cp1252"))

        self.assertEqual("{ameaça} 1.e4", import_text_file(utf8_path))
        self.assertEqual("{ameaça} 1.e4", import_text_file(cp1252_path))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

        patcher = mock.patch.object(project_service, "app_data_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_snapshot_survives_a_crash_and_can_be_read_back(self):
        write_recovery_snapshot(BOOK_TEXT, self.tmp / "livro.plez")

        snapshot = read_recovery_snapshot()

        self.assertIsNotNone(snapshot)
        self.assertEqual(BOOK_TEXT, snapshot.raw_text)
        self.assertEqual("livro.plez", snapshot.origin_path.name)
        self.assertLess(snapshot.age_seconds, 60)

    def test_no_snapshot_when_nothing_was_written(self):
        self.assertIsNone(read_recovery_snapshot())

    def test_clearing_removes_the_snapshot(self):
        write_recovery_snapshot(BOOK_TEXT, None)
        clear_recovery_snapshot()

        self.assertIsNone(read_recovery_snapshot())

    def test_empty_text_does_not_produce_a_recovery_offer(self):
        write_recovery_snapshot("   \n  ", None)

        self.assertIsNone(read_recovery_snapshot())


class RecentFilesTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

        patcher = mock.patch.object(project_service, "app_data_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recent_files_are_ordered_by_last_use_and_deduplicated(self):
        first = save_project(Project(raw_text="a"), self.tmp / "a")
        second = save_project(Project(raw_text="b"), self.tmp / "b")

        remember_recent_file(first)
        remember_recent_file(second)
        remember_recent_file(first)

        self.assertEqual([first, second], load_recent_files())

    def test_missing_files_are_dropped_from_the_recent_list(self):
        path = save_project(Project(raw_text="a"), self.tmp / "sumiu")
        remember_recent_file(path)
        path.unlink()

        self.assertEqual([], load_recent_files())


if __name__ == "__main__":
    unittest.main()
