from PyQt6.QtWidgets import QApplication

from pgn_live_editor.core.annotations import Arrow, VisualAnnotations
from pgn_live_editor.ui.comment_editor_dialog import CommentEditorDialog


def test_rich_comment_editor_keeps_annotation_commands():
    _application = QApplication.instance() or QApplication([])
    dialog = CommentEditorDialog("Plano central", VisualAnnotations([Arrow(12, 28)], []))
    dialog._insert_symbol("!")
    assert "Plano central" in dialog.comment_text()
    assert "!" in dialog.comment_text()
    assert "[%cal" in dialog.comment_text()
    dialog.deleteLater()
