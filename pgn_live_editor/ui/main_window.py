import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QMainWindow, QMessageBox, QSplitter, QWidget

from ..core.move_tree import render_game_pgn
from .chess_panel import ChessPanel
from .editor_panel import EditorPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PGN Live Editor for Chess PDF Text")
        self.resize(1200, 800)
        self._build_menu()

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 3-Column Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # 1. Editor Panel (Left)
        self.editor_panel = EditorPanel()

        # 2. Chess Panel (Right)
        self.chess_panel = ChessPanel()

        self.splitter.addWidget(self.editor_panel)
        self.splitter.addWidget(self.chess_panel)

        # Set relative sizes
        self.splitter.setStretchFactor(0, 1) # Editor gets 50%
        self.splitter.setStretchFactor(1, 1) # Chess gets 50%

        # Connecting Real-time parsing triggers
        self.editor_panel.text_parsed_signal.connect(self.chess_panel.on_game_parsed)
        self.editor_panel.cursor_position_changed_signal.connect(self.chess_panel.on_editor_cursor_moved)
        self.chess_panel.current_move_span_signal.connect(self.editor_panel.set_current_move_span)
        self.chess_panel.move_focused_signal.connect(self.editor_panel.focus_span)
        self.chess_panel.move_insert_requested_signal.connect(self.editor_panel.insert_board_move)
        self.chess_panel.variant_builder.insert_requested_signal.connect(self.editor_panel.insert_text_at_cursor)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&Arquivo")

        save_pgn_action = QAction("Salvar PGN...", self)
        save_pgn_action.setShortcut(QKeySequence.StandardKey.Save)
        save_pgn_action.setStatusTip("Salva o PGN atual com comentarios e variantes")
        save_pgn_action.triggered.connect(self.save_pgn)
        file_menu.addAction(save_pgn_action)

    def save_pgn(self):
        ast = self.editor_panel.reparse_now()
        if not ast.moves and not ast.result:
            QMessageBox.information(self, "Salvar PGN", "Nao ha PGN parseado para salvar.")
            return

        suggested_path = Path.cwd() / "exported_game.pgn"
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Salvar PGN",
            str(suggested_path),
            "PGN Files (*.pgn);;Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return

        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".pgn")

        pgn_text = render_game_pgn(ast)
        try:
            output_path.write_text(pgn_text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Salvar PGN", f"Nao foi possivel salvar o arquivo.\n\n{exc}")
            return

        self.statusBar().showMessage(f"PGN salvo em {output_path}", 5000)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
