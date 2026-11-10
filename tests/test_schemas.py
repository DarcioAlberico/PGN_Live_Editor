"""Arquivo malformado nunca derruba o programa nem apaga os outros campos (R-103).

Antes: `state.json` com `completed_games: null` levantava TypeError ao abrir o
projeto (S-05); uma preferencia com tipo errado fazia voltar **todas** ao padrao,
e o fechamento gravava os padroes por cima (S-06); `engine.json` com
`movetime_ms: "fast"` impedia o programa de abrir (S-07).
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from dataclasses import asdict, fields
from pathlib import Path

import _isolation  # noqa: F401

from pgn_live_editor.services import engine_service, preferences_service
from pgn_live_editor.services.engine_service import EngineSettings, load_engine_settings
from pgn_live_editor.services.preferences_service import Preferences, load_preferences, write_all
from pgn_live_editor.services.project_service import (
    Project,
    ProjectMeta,
    ProjectState,
    load_project,
    save_project,
)
from pgn_live_editor.services.schemas import ProjectFormatError, from_payload

BAD_VALUES = [None, [], "x", 5, {}, 1.5, True]


def rewrite_member(path: Path, member: str, payload: object) -> None:
    with zipfile.ZipFile(path) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    contents[member] = json.dumps(payload).encode()
    with zipfile.ZipFile(path, "w") as target:
        for name, data in contents.items():
            target.writestr(name, data)


class FromPayloadTests(unittest.TestCase):
    def test_a_numeric_string_is_accepted_for_an_int(self):
        prefs, warnings = from_payload(Preferences, {"editor_font_size": "14"}, source="prefs")

        self.assertEqual(14, prefs.editor_font_size)
        self.assertEqual([], warnings)

    def test_a_bad_field_falls_back_alone_and_says_so(self):
        prefs, warnings = from_payload(
            Preferences, {"editor_font_size": "grande", "engine_path": "C:/sf.exe"}, source="prefs"
        )

        self.assertEqual(Preferences().editor_font_size, prefs.editor_font_size)
        self.assertEqual("C:/sf.exe", prefs.engine_path)
        self.assertEqual(["editor_font_size"], [w.field for w in warnings])

    def test_a_bool_is_not_taken_for_a_number(self):
        state, warnings = from_payload(ProjectState, {"cursor_position": True}, source="state")

        self.assertEqual(0, state.cursor_position)
        self.assertEqual(["cursor_position"], [w.field for w in warnings])

    def test_bad_items_leave_a_list_and_the_good_ones_stay(self):
        state, warnings = from_payload(ProjectState, {"completed_games": [1, "x", 3, None]}, source="state")

        self.assertEqual([1, 3], state.completed_games)
        self.assertEqual(["completed_games"], [w.field for w in warnings])

    def test_a_root_that_is_not_an_object_gives_the_defaults_and_a_warning(self):
        state, warnings = from_payload(ProjectState, [1, 2], source="state")

        self.assertEqual(ProjectState(), state)
        self.assertEqual(1, len(warnings))

    def test_unknown_keys_are_ignored(self):
        meta, warnings = from_payload(ProjectMeta, {"title": "Livro", "campo_do_futuro": 1}, source="meta")

        self.assertEqual("Livro", meta.title)
        self.assertEqual([], warnings)


class ProjectFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="schema-"))
        project = Project(raw_text="1. e4 e5")
        project.meta.title = "Meu livro"
        project.state.completed_games = [0, 2]
        project.state.pdf_path = "livro.pdf"
        self.path = save_project(project, self.tmp / "livro.plez")
        with zipfile.ZipFile(self.path) as archive:
            self.state = json.loads(archive.read("state.json"))
            self.meta = json.loads(archive.read("meta.json"))

    def test_every_state_field_survives_every_bad_value(self):
        for name in [f.name for f in fields(ProjectState)]:
            for bad in BAD_VALUES:
                with self.subTest(field=name, value=bad):
                    rewrite_member(self.path, "state.json", {**self.state, name: bad})

                    project = load_project(self.path)

                    self.assertEqual("1. e4 e5", project.raw_text)
                    if name != "pdf_path":
                        self.assertEqual("livro.pdf", project.state.pdf_path)
                    if name != "completed_games":
                        self.assertEqual([0, 2], project.state.completed_games)

    def test_every_meta_field_survives_every_bad_value(self):
        for name in [f.name for f in fields(ProjectMeta) if f.name != "version"]:
            for bad in BAD_VALUES:
                with self.subTest(field=name, value=bad):
                    rewrite_member(self.path, "meta.json", {**self.meta, name: bad})

                    project = load_project(self.path)

                    if name != "title":
                        self.assertEqual("Meu livro", project.meta.title)

    def test_a_malformed_field_is_reported_on_the_project(self):
        rewrite_member(self.path, "state.json", {**self.state, "cursor_position": None})

        project = load_project(self.path)

        self.assertIn("cursor_position", [w.field for w in project.load_warnings])

    def test_a_newer_format_is_a_format_error(self):
        rewrite_member(self.path, "meta.json", {**self.meta, "version": 99})

        with self.assertRaises(ProjectFormatError):
            load_project(self.path)

    def test_a_state_that_is_not_an_object_opens_with_defaults(self):
        rewrite_member(self.path, "state.json", [1, 2, 3])

        project = load_project(self.path)

        self.assertEqual("1. e4 e5", project.raw_text)
        self.assertEqual(ProjectState(), project.state)


class PreferencesFileTests(unittest.TestCase):
    def setUp(self):
        preferences_service.database_path().unlink(missing_ok=True)

    def test_one_bad_preference_does_not_reset_the_others(self):
        write_all({**asdict(Preferences(engine_path="C:/sf.exe", theme="claro")), "editor_font_size": "enorme"})

        prefs = load_preferences()

        self.assertEqual("C:/sf.exe", prefs.engine_path)
        self.assertEqual("claro", prefs.theme)
        self.assertEqual(Preferences().editor_font_size, prefs.editor_font_size)

    def test_every_preference_survives_every_bad_value(self):
        for name in [f.name for f in fields(Preferences) if f.name not in ("layout", "engine_path")]:
            for bad in BAD_VALUES:
                with self.subTest(field=name, value=bad):
                    write_all({**asdict(Preferences(engine_path="C:/sf.exe")), name: bad})

                    self.assertEqual("C:/sf.exe", load_preferences().engine_path)

    def test_a_bad_layout_field_keeps_the_rest_of_the_layout(self):
        layout = asdict(Preferences().layout) | {"geometry": "abc", "games_panel_visible": True}
        write_all({"layout": {**layout, "pdf_panel_visible": "talvez"}})

        prefs = load_preferences()

        self.assertEqual("abc", prefs.layout.geometry)
        self.assertTrue(prefs.layout.games_panel_visible)


class EngineSettingsFileTests(unittest.TestCase):
    def test_a_bad_engine_field_does_not_stop_the_program(self):
        for bad in BAD_VALUES:
            with self.subTest(value=bad):
                engine_service._settings_file().write_text(
                    json.dumps({"path": "C:/sf.exe", "movetime_ms": bad}), encoding="utf-8"
                )

                settings = load_engine_settings()

                self.assertEqual("C:/sf.exe", settings.path)
                self.assertGreater(settings.movetime_ms, 0)

    def test_a_root_that_is_not_an_object_gives_the_defaults(self):
        engine_service._settings_file().write_text("[1, 2]", encoding="utf-8")

        self.assertEqual(EngineSettings(), load_engine_settings())


class WindowLoadWarningTests(unittest.TestCase):
    def test_opening_a_damaged_project_shows_the_text_and_a_non_blocking_warning(self):
        from PyQt6.QtWidgets import QApplication

        from pgn_live_editor.ui.main_window import MainWindow

        # A referencia precisa ficar viva: uma QApplication coletada antes da
        # janela nascer derruba o processo.
        self.app = QApplication.instance() or QApplication([])
        tmp = Path(tempfile.mkdtemp(prefix="schema-ui-"))
        path = save_project(Project(raw_text="1. e4 e5 2. Nf3"), tmp / "estragado.plez")
        rewrite_member(path, "state.json", {"completed_games": None, "cursor_position": "x"})
        window = MainWindow()
        self.addCleanup(window.deleteLater)

        window._load_project_path(path)

        self.assertEqual("1. e4 e5 2. Nf3", window.editor_panel.document_text())
        box = window._load_warnings_box
        self.assertFalse(box.isModal())
        self.assertIn("completed_games", box.detailedText())


if __name__ == "__main__":
    unittest.main()
