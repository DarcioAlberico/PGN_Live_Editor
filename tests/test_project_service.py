"""Fase 1: o trabalho nao pode ser perdido."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.core.substitutions import SCOPE_PREFIX, Dictionary, Substitution
from pgn_live_editor.services import project_service
from pgn_live_editor.services.project_service import (
    PROJECT_SUFFIX,
    Project,
    ProjectMeta,
    ProjectState,
    import_text_file,
    load_project,
    load_recent_files,
    remember_recent_file,
    save_project,
    validate_project_archive,
)

BOOK_TEXT = (
    '[Event "XIV Campeonato Soviético"]\n'
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
            dictionary=Dictionary([Substitution(pattern="tD", replacement="N", scope=SCOPE_PREFIX)]),
            state=ProjectState(cursor_position=42, ignored_issue_tokens=["[D]"]),
        )

        path = save_project(project, self.tmp / "trabalho")
        reopened = load_project(path)

        self.assertEqual(PROJECT_SUFFIX, path.suffix)
        self.assertEqual(BOOK_TEXT, reopened.raw_text)
        self.assertEqual("Smyslov", reopened.meta.title)
        self.assertEqual("pt", reopened.meta.locale)
        self.assertEqual({"Event": "Torneio", "Site": "Moscou"}, reopened.header_template)
        self.assertEqual({"tD": "N"}, reopened.dictionary.prefix_map("pt"))
        self.assertEqual(SCOPE_PREFIX, list(reopened.dictionary)[0].scope)
        self.assertEqual(42, reopened.state.cursor_position)
        self.assertEqual(["[D]"], reopened.state.ignored_issue_tokens)
        self.assertGreater(reopened.meta.saved_at, 0.0)

    def test_diagram_ocr_records_survive_round_trip(self):
        project = Project(
            raw_text='[FEN "8/8/8/8/8/8/4K3/4k3 b - - 0 1"]',
            state=ProjectState(
                diagram_records=[
                    {
                        "pdf_path": "livro.pdf",
                        "page": 12,
                        "diagram": 2,
                        "fen": "8/8/8/8/8/8/4K3/4k3 b - - 0 1",
                        "review": "pending",
                    }
                ]
            ),
        )

        reopened = load_project(save_project(project, self.tmp / "diagramas"))

        self.assertEqual(1, len(reopened.state.diagram_records))
        self.assertEqual(12, reopened.state.diagram_records[0]["page"])
        self.assertEqual("pending", reopened.state.diagram_records[0]["review"])

    def test_a_project_saved_before_the_scoped_dictionary_still_opens(self):
        """Formato antigo: `dictionary.json` era um mapa simples."""
        path = save_project(Project(raw_text="1.e4"), self.tmp / "antigo")
        with zipfile.ZipFile(path) as archive:
            kept = {name: archive.read(name) for name in archive.namelist()}
        kept["dictionary.json"] = b'{"Bh5": "Bb5"}'

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in kept.items():
                archive.writestr(name, payload)

        reopened = load_project(path)

        self.assertEqual({"Bh5": "Bb5"}, reopened.dictionary.token_map("pt"))

    def test_accented_text_survives_the_zip(self):
        text = "{Excelente desenvolvimento das pretas. Ameaça mate.} 1.e4 e5"
        path = save_project(Project(raw_text=text), self.tmp / "acentos")

        self.assertEqual(text, load_project(path).raw_text)

    def test_saving_over_an_existing_project_is_atomic(self):
        path = save_project(Project(raw_text="versão 1"), self.tmp / "trabalho")
        save_project(Project(raw_text="versão 2"), path)

        self.assertEqual("versão 2", load_project(path).raw_text)
        self.assertEqual([], list(self.tmp.glob("*.tmp")), "sobrou arquivo temporario")
        self.assertEqual(1, len(list(self.tmp.glob(f"*{PROJECT_SUFFIX}"))))

    def test_project_backups_rotate_without_replacing_the_current_file(self):
        path = save_project(Project(raw_text="versão 0"), self.tmp / "rotativo")
        for version in range(1, 5):
            save_project(Project(raw_text=f"versão {version}"), path)

        self.assertEqual("versão 4", load_project(path).raw_text)
        self.assertEqual("versão 3", load_project(path.with_suffix(".plez.bak")).raw_text)
        self.assertEqual("versão 2", load_project(path.with_suffix(".plez.bak.1")).raw_text)
        self.assertEqual("versão 1", load_project(path.with_suffix(".plez.bak.2")).raw_text)

    def test_a_save_that_fails_at_the_last_step_keeps_the_project_on_disk(self):
        # S-02: antes, o projeto ia para `.bak` antes da troca final; se ela
        # falhasse (antivirus, OneDrive), nao sobrava projeto no caminho.
        path = save_project(Project(raw_text="versão 1"), self.tmp / "frágil")
        save_project(Project(raw_text="versão 2"), path)
        real_replace = os.replace

        def failing_final_swap(source, target):
            # `Path.replace` tambem passa por aqui; so a troca que poe o
            # arquivo novo no lugar do projeto falha.
            if Path(target) == path:
                raise OSError("disco cheio")
            return real_replace(source, target)

        with mock.patch("os.replace", failing_final_swap), self.assertRaises(OSError):
            save_project(Project(raw_text="versão 3"), path)

        self.assertEqual("versão 2", load_project(path).raw_text)
        self.assertEqual("versão 1", load_project(path.with_suffix(".plez.bak")).raw_text)

    def test_opening_a_file_that_is_not_a_project_fails_clearly(self):
        stray = self.tmp / "qualquer.plez"
        stray.write_text("isto não e um zip", encoding="utf-8")

        with self.assertRaises(ValueError):
            load_project(stray)

    def test_opening_a_zip_without_raw_text_fails_clearly(self):
        broken = self.tmp / "quebrado.plez"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("meta.json", "{}")

        with self.assertRaises(ValueError):
            load_project(broken)

    def test_schema_validation_accepts_current_project_and_reports_missing_raw(self):
        path = save_project(Project(raw_text="1.e4"), self.tmp / "validado")
        self.assertEqual([], validate_project_archive(path))
        broken = self.tmp / "sem-raw.plez"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("meta.json", "{}")
        self.assertTrue(any("raw.txt" in item for item in validate_project_archive(broken)))


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
