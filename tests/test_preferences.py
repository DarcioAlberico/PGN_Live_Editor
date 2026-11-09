"""Fase 6: preferencias globais e layout persistido (SPEC 5.1)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.services import preferences_service  # noqa: E402
from pgn_live_editor.services.preferences_service import (  # noqa: E402
    THEME_DARK,
    THEME_LIGHT,
    Layout,
    Preferences,
    database_path,
    load_preferences,
    save_preferences,
)
from pgn_live_editor.ui.main_window import MainWindow  # noqa: E402
from pgn_live_editor.ui.preferences_dialog import PreferencesDialog  # noqa: E402
from pgn_live_editor.ui.theme import PALETTES, palette_of  # noqa: E402

_app = QApplication.instance() or QApplication([])


class PreferencesStorageTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch.object(preferences_service, "app_data_dir", return_value=Path(self._temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_come_back_when_nothing_was_ever_saved(self):
        preferences = load_preferences()

        self.assertEqual("pt", preferences.locale)
        self.assertEqual(THEME_DARK, preferences.theme)
        self.assertTrue(preferences.auto_close_delimiters)

    def test_what_is_saved_is_what_comes_back(self):
        save_preferences(
            Preferences(
                locale="de",
                theme=THEME_LIGHT,
                editor_font_family="Cascadia Mono",
                editor_font_size=14,
                engine_path="C:/motor/stockfish.exe",
                engine_threshold_cp=250,
                engine_include_variations=False,
                auto_close_delimiters=False,
            )
        )

        restored = load_preferences()

        self.assertEqual("de", restored.locale)
        self.assertEqual(THEME_LIGHT, restored.theme)
        self.assertEqual("Cascadia Mono", restored.editor_font_family)
        self.assertEqual(14, restored.editor_font_size)
        self.assertEqual("C:/motor/stockfish.exe", restored.engine_path)
        self.assertEqual(250, restored.engine_threshold_cp)
        self.assertFalse(restored.engine_include_variations)
        self.assertFalse(restored.auto_close_delimiters)

    def test_the_layout_survives_too(self):
        save_preferences(Preferences(layout=Layout(geometry="AAAA", issues_panel_visible=False)))

        layout = load_preferences().layout

        self.assertEqual("AAAA", layout.geometry)
        self.assertFalse(layout.issues_panel_visible)

    def test_a_value_out_of_range_falls_back_instead_of_breaking_the_ui(self):
        save_preferences(Preferences(locale="klingon", theme="neon", editor_font_size=999))

        restored = load_preferences()

        self.assertEqual("pt", restored.locale)
        self.assertEqual(THEME_DARK, restored.theme)
        self.assertEqual(11, restored.editor_font_size)

    def test_notation_and_dictionary_locales_are_independent(self):
        save_preferences(Preferences(locale="pt", notation_locale="de", dictionary_locale="fr"))
        restored = load_preferences()
        self.assertEqual("de", restored.notation_locale)
        self.assertEqual("fr", restored.dictionary_locale)

    def test_a_corrupt_database_does_not_take_the_program_down(self):
        database_path().write_bytes(b"isto nao e um banco")

        self.assertEqual(Preferences(), load_preferences())

    def test_a_row_written_by_a_future_version_is_ignored(self):
        save_preferences(Preferences())
        with closing(sqlite3.connect(database_path())) as connection, connection:
            connection.execute("INSERT INTO preferences (key, value) VALUES ('funcionalidade_do_futuro', '42')")

        self.assertEqual("pt", load_preferences().locale)


class ThemeTests(unittest.TestCase):
    def test_both_palettes_answer_the_same_questions(self):
        """Tema claro nao e trocar o fundo: e trocar o conjunto inteiro."""
        dark, light = PALETTES[THEME_DARK], PALETTES[THEME_LIGHT]

        self.assertEqual(set(dark), set(light))
        self.assertNotEqual(dark["move"], light["move"])

    def test_an_unknown_theme_falls_back_to_the_dark_one(self):
        self.assertEqual(PALETTES[THEME_DARK], palette_of("neon"))


class PreferencesInTheWindowTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch.object(preferences_service, "app_data_dir", return_value=Path(self._temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def test_choosing_a_theme_reaches_the_editor_without_reopening(self):
        self.window.preferences = Preferences(theme=THEME_LIGHT)

        self.window._apply_preferences()

        self.assertEqual(THEME_LIGHT, self.window.editor_panel.theme)

    def test_the_font_choice_reaches_the_editor(self):
        self.window.preferences = Preferences(editor_font_family="Courier New", editor_font_size=15)

        self.window._apply_preferences()

        self.assertEqual(15, self.window.editor_panel.text_edit.font().pointSize())

    def test_turning_off_auto_close_reaches_the_editor(self):
        self.window.preferences = Preferences(auto_close_delimiters=False)

        self.window._apply_preferences()

        self.assertFalse(self.window.editor_panel.text_edit.auto_close_enabled)

    def test_the_engine_settings_follow_the_preferences(self):
        self.window.preferences = Preferences(
            engine_threshold_cp=450, engine_movetime_ms=500, engine_include_variations=False
        )

        self.window._apply_preferences()

        self.assertEqual(450, self.window.engine_settings.threshold_cp)
        self.assertEqual(500, self.window.engine_settings.movetime_ms)
        self.assertFalse(self.window.engine_settings.include_variations)

    def test_the_dialog_gives_back_what_was_chosen(self):
        dialog = PreferencesDialog(Preferences(), self.window)
        self.addCleanup(dialog.deleteLater)

        dialog.cmb_locale.setCurrentIndex(1)  # en
        dialog.spn_font_size.setValue(13)
        dialog.chk_auto_close.setChecked(False)

        values = dialog.values()

        self.assertEqual("en", values.locale)
        self.assertEqual(13, values.editor_font_size)
        self.assertFalse(values.auto_close_delimiters)

    def test_the_layout_is_captured_and_restored(self):
        self.window.issues_panel.setVisible(False)

        captured = self.window._capture_layout()
        self.assertFalse(captured.issues_panel_visible)
        self.assertTrue(captured.geometry)

        self.window.issues_panel.setVisible(True)
        self.window.preferences.layout = captured
        self.window._restore_layout()

        self.assertFalse(self.window.issues_panel.isVisible())


if __name__ == "__main__":
    unittest.main()
