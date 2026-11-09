"""Fumaca dos painéis novos: rodape de simbolos, arvore e conversor.

Roda sem tela (`QT_QPA_PLATFORM=offscreen`), como o resto dos testes de
interface.
"""

from __future__ import annotations

import os
import unittest

import _isolation  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.core.book_import import BookImportOptions  # noqa: E402
from pgn_live_editor.core.nag_table import NAG_TABLE  # noqa: E402
from pgn_live_editor.ui.book_import_dialog import BookImportDialog  # noqa: E402
from pgn_live_editor.ui.main_window import MainWindow  # noqa: E402
from pgn_live_editor.ui.nag_bar import NagBar  # noqa: E402

_app = QApplication.instance() or QApplication([])

BOOK_PAGE = "\n".join(
    [
        "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.Qd2",
        "Position after: 7.Qd2",
        "7...h6",
        "A) 8.Be3 Ng4 9.0-0-0 Nxe3",
        "B) 8.Bh4? falls into an elementary trap: 8...Nxe4!µ",
    ]
)


class NagBarTests(unittest.TestCase):
    def setUp(self):
        self.bar = NagBar()
        self.addCleanup(self.bar.deleteLater)

    def test_every_nag_of_the_table_has_a_button(self):
        self.assertEqual(len(NAG_TABLE), len(self.bar._buttons))

    def test_clicking_emits_the_glyph_and_the_switch_emits_the_code(self):
        received: list[str] = []
        self.bar.symbol_requested_signal.connect(received.append)

        button, nag = next(item for item in self.bar._buttons if item[1].code == "$16")
        button.click()
        self.bar.chk_numeric.setChecked(True)
        button.click()

        self.assertEqual([nag.glyph, "$16"], received)

    def test_the_unsafe_glyphs_are_always_inserted_as_codes(self):
        """Um `N` solto no meio da notacao viraria cavalo, nao novidade."""
        for _button, nag in self.bar._buttons:
            if not nag.safe_glyph:
                self.assertEqual(nag.code, self.bar.text_for(nag))


class WindowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        # `deleteLater`, e nao `close()`: fechar a janela suja pede confirmacao
        # num QMessageBox modal, e sem tela ninguem responde.
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def test_the_footer_of_symbols_writes_at_the_cursor(self):
        self.window.editor_panel.load_document_text("1.e4 c5 2.Nf3")
        cursor = self.window.editor_panel.text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.window.editor_panel.text_edit.setTextCursor(cursor)

        button, nag = next(item for item in self.window.editor_panel.nag_bar._buttons if item[1].code == "$5")
        button.click()

        self.assertTrue(self.window.editor_panel.document_text().endswith(nag.glyph))

    def test_the_footer_can_be_hidden(self):
        self.assertTrue(self.window.editor_panel.nag_bar.isVisibleTo(self.window.editor_panel))
        self.window._toggle_nag_bar()
        self.assertFalse(self.window.editor_panel.nag_bar.isVisibleTo(self.window.editor_panel))

    def test_the_tree_lists_the_variations_and_navigates_to_them(self):
        self.window.editor_panel.load_document_text(
            "1.e4 c5 2.Nf3 d6 ( 2...Nc6 3.Bb5 ) 3.d4 cxd4 ( 3...Nf6 4.e5 ( 4.Nc3 ) )"
        )
        panel = self.window.chess_panel.outline_panel
        self.assertIn("variante", panel.lbl_summary.text())

        root = panel.tree.topLevelItem(0)
        self.assertEqual(2, root.childCount())

        first = root.child(0)
        panel._on_item_clicked(first, 0)
        start, end = first.data(0, Qt.ItemDataRole.UserRole)
        selected = self.window.editor_panel.text_edit.textCursor()
        self.assertEqual(start, selected.selectionStart())
        self.assertEqual(end, selected.selectionEnd())

    def test_the_tree_survives_an_empty_document(self):
        self.window.editor_panel.load_document_text("")
        self.assertEqual("Nenhuma variante.", self.window.chess_panel.outline_panel.lbl_summary.text())

    def test_converting_a_book_page_rewrites_the_document(self):
        self.window.editor_panel.load_document_text(BOOK_PAGE)
        message = self.window.editor_panel.action_convert_book_text(BookImportOptions())

        text = self.window.editor_panel.document_text()
        self.assertEqual("Texto de livro convertido.", message)
        self.assertNotIn("Position after", text)
        self.assertIn("( 8.Bh4? {falls into an elementary trap:} 8...Nxe4!∓ )", text)

    def test_converting_undoes_in_one_step(self):
        """A SPEC 3.5 exige isso de todo gesto: um Ctrl+Z desfaz o gesto inteiro."""
        self.window.editor_panel.load_document_text(BOOK_PAGE)
        self.window.editor_panel.action_convert_book_text(BookImportOptions())
        self.window.editor_panel.text_edit.undo()
        self.assertEqual(BOOK_PAGE, self.window.editor_panel.document_text())

    def test_converting_only_the_selection_leaves_the_rest_alone(self):
        self.window.editor_panel.load_document_text("1.e4 c5\n\nA) 2.Nf3 d6\nB) 2.Nc3 Nc6")
        cursor = self.window.editor_panel.text_edit.textCursor()
        cursor.setPosition(self.window.editor_panel.document_text().index("A)"))
        cursor.movePosition(cursor.MoveOperation.End, cursor.MoveMode.KeepAnchor)
        self.window.editor_panel.text_edit.setTextCursor(cursor)

        self.window.editor_panel.action_convert_book_text(BookImportOptions())
        text = self.window.editor_panel.document_text()
        self.assertTrue(text.startswith("1.e4 c5"))
        self.assertIn("2.Nf3 ( 2.Nc3 Nc6 ) d6", text)


class BookImportDialogTests(unittest.TestCase):
    def test_the_preview_reacts_to_the_options(self):
        dialog = BookImportDialog(BOOK_PAGE)
        self.addCleanup(dialog.deleteLater)

        self.assertIn("{falls into an elementary trap:}", dialog.converted_text())

        dialog.checkboxes["prose_to_comments"].setChecked(False)
        self.assertNotIn("{falls into an elementary trap:}", dialog.converted_text())

    def test_the_doubtful_decisions_are_shown_apart_from_the_counts(self):
        dialog = BookImportDialog("8.0-0-0\n8.f4!? b5")
        self.addCleanup(dialog.deleteLater)
        self.assertIn("repete o lance 8", dialog.lbl_notes.text())


if __name__ == "__main__":
    unittest.main()
