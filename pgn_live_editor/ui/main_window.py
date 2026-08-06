import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
)

from ..services.export_service import build_pgn, write_pgn
from ..services.project_service import (
    PROJECT_SUFFIX,
    Project,
    clear_recovery_snapshot,
    import_text_file,
    load_project,
    load_recent_files,
    read_recovery_snapshot,
    remember_recent_file,
    save_project,
    write_recovery_snapshot,
)
from .chess_panel import ChessPanel
from .editor_panel import EditorPanel

AUTOSAVE_INTERVAL_MS = 30_000
AUTOSAVE_CHAR_THRESHOLD = 200


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PGN Live Editor for Chess PDF Text")
        self.resize(1280, 860)

        self.project = Project()
        self._chars_since_autosave = 0
        self._last_autosaved_text = ""

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        self.editor_panel = EditorPanel()
        self.chess_panel = ChessPanel()

        self.splitter.addWidget(self.editor_panel)
        self.splitter.addWidget(self.chess_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        self.lbl_autosave = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_autosave)

        self._build_menu()

        # Ligacoes de parsing em tempo real
        self.editor_panel.text_parsed_signal.connect(self.chess_panel.on_game_parsed)
        self.editor_panel.cursor_position_changed_signal.connect(self.chess_panel.on_editor_cursor_moved)
        self.chess_panel.current_move_span_signal.connect(self.editor_panel.set_current_move_span)
        self.chess_panel.move_focused_signal.connect(self.editor_panel.focus_span)
        self.chess_panel.move_insert_requested_signal.connect(self.editor_panel.insert_board_move)
        self.chess_panel.variant_builder.insert_requested_signal.connect(self.editor_panel.insert_text_at_cursor)

        self.editor_panel.text_edit.textChanged.connect(self._on_text_changed)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start()

        self._update_window_title()
        QTimer.singleShot(0, self._offer_session_recovery)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&Arquivo")

        self._add_action(file_menu, "Novo", QKeySequence.StandardKey.New, self.new_project)
        self._add_action(file_menu, "Abrir projeto...", QKeySequence.StandardKey.Open, self.open_project)

        self.recent_menu = file_menu.addMenu("Abrir recente")
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        self._add_action(file_menu, "Salvar projeto", QKeySequence.StandardKey.Save, self.save_project)
        self._add_action(file_menu, "Salvar projeto como...", QKeySequence.StandardKey.SaveAs, self.save_project_as)

        file_menu.addSeparator()
        self._add_action(file_menu, "Importar PGN ou texto...", "Ctrl+I", self.import_text)
        self._add_action(file_menu, "Exportar PGN...", "Ctrl+Shift+E", self.export_pgn)

        file_menu.addSeparator()
        self._add_action(file_menu, "Sair", QKeySequence.StandardKey.Quit, self.close)

        move_menu = self.menuBar().addMenu("&Lances")
        self._add_action(move_menu, "Lance anterior", "Ctrl+Left", self.chess_panel.go_prev)
        self._add_action(move_menu, "Proximo lance", "Ctrl+Right", self.chess_panel.go_next)
        move_menu.addSeparator()
        self._add_action(move_menu, "Inverter tabuleiro", "Ctrl+F", self.chess_panel.flip_board)

    def _add_action(self, menu, title: str, shortcut, slot) -> QAction:
        action = QAction(title, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut) if isinstance(shortcut, str) else shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _rebuild_recent_menu(self):
        self.recent_menu.clear()
        entries = load_recent_files()
        if not entries:
            empty = QAction("(nenhum)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return

        for path in entries:
            action = QAction(path.name, self)
            action.setStatusTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self._load_project_path(target))
            self.recent_menu.addAction(action)

    # ------------------------------------------------------------------
    # Ciclo de vida do projeto
    # ------------------------------------------------------------------

    def _update_window_title(self):
        name = self.project.display_name
        dirty = "*" if self._is_dirty() else ""
        self.setWindowTitle(f"{dirty}{name} - PGN Live Editor")

    def _is_dirty(self) -> bool:
        return self.editor_panel.document_text() != self.project.raw_text

    def _confirm_discard(self) -> bool:
        if not self._is_dirty():
            return True

        choice = QMessageBox.question(
            self,
            "Trabalho nao salvo",
            "Ha alteracoes que ainda nao foram salvas no projeto.\n\nDeseja salvar antes de continuar?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self.save_project()
        return True

    def new_project(self):
        if not self._confirm_discard():
            return
        self.project = Project()
        self.editor_panel.load_document_text("")
        clear_recovery_snapshot()
        self._last_autosaved_text = ""
        self._update_window_title()
        self.statusBar().showMessage("Projeto novo.", 4000)

    def open_project(self):
        if not self._confirm_discard():
            return

        file_path, _filter = QFileDialog.getOpenFileName(
            self,
            "Abrir projeto",
            str(Path.cwd()),
            f"Projetos do PGN Live Editor (*{PROJECT_SUFFIX});;Todos os arquivos (*)",
        )
        if file_path:
            self._load_project_path(Path(file_path))

    def _load_project_path(self, path: Path):
        try:
            project = load_project(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Abrir projeto", f"Nao foi possivel abrir o projeto.\n\n{exc}")
            return

        self.project = project
        self.editor_panel.load_document_text(project.raw_text, project.state.cursor_position)
        self._last_autosaved_text = project.raw_text
        self._chars_since_autosave = 0
        remember_recent_file(path)
        self._rebuild_recent_menu()
        self._update_window_title()
        self.statusBar().showMessage(f"Projeto aberto: {path.name}", 5000)

    def save_project(self) -> bool:
        if self.project.path is None:
            return self.save_project_as()
        return self._write_project(self.project.path)

    def save_project_as(self) -> bool:
        suggested = self.project.path or (Path.cwd() / f"projeto{PROJECT_SUFFIX}")
        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Salvar projeto",
            str(suggested),
            f"Projetos do PGN Live Editor (*{PROJECT_SUFFIX});;Todos os arquivos (*)",
        )
        if not file_path:
            return False

        path = Path(file_path)
        if not path.suffix:
            path = path.with_suffix(PROJECT_SUFFIX)
        return self._write_project(path)

    def _write_project(self, path: Path) -> bool:
        self.project.raw_text = self.editor_panel.document_text()
        self.project.state.cursor_position = self.editor_panel.cursor_position()

        try:
            saved_path = save_project(self.project, path)
        except OSError as exc:
            QMessageBox.critical(self, "Salvar projeto", f"Nao foi possivel salvar o projeto.\n\n{exc}")
            return False

        self._last_autosaved_text = self.project.raw_text
        self._chars_since_autosave = 0
        clear_recovery_snapshot()
        remember_recent_file(saved_path)
        self._rebuild_recent_menu()
        self._update_window_title()
        self.statusBar().showMessage(f"Projeto salvo em {saved_path}", 5000)
        return True

    def import_text(self):
        if not self._confirm_discard():
            return

        file_path, _filter = QFileDialog.getOpenFileName(
            self,
            "Importar PGN ou texto",
            str(Path.cwd()),
            "PGN e texto (*.pgn *.txt);;Todos os arquivos (*)",
        )
        if not file_path:
            return

        path = Path(file_path)
        try:
            text = import_text_file(path)
        except OSError as exc:
            QMessageBox.critical(self, "Importar", f"Nao foi possivel ler o arquivo.\n\n{exc}")
            return

        self.project = Project()
        self.project.meta.title = path.stem
        self.editor_panel.load_document_text(text)
        self._last_autosaved_text = ""
        self._update_window_title()
        self.statusBar().showMessage(f"Importado: {path.name} ({len(text)} caracteres)", 5000)

    # ------------------------------------------------------------------
    # Exportacao
    # ------------------------------------------------------------------

    def export_pgn(self):
        ast = self.editor_panel.reparse_now()
        if not ast.moves and not ast.result:
            QMessageBox.information(self, "Exportar PGN", "Nao ha PGN analisado para exportar.")
            return

        pgn_text, report = build_pgn(ast, self.editor_panel.current_start_fen)

        if not report.ok:
            choice = QMessageBox.warning(
                self,
                "Conferencia do PGN",
                "O PGN gerado nao confere com o que foi analisado:\n\n"
                f"{report.summary()}\n\n"
                "Salvar assim mesmo?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Save:
                return

        suggested_name = f"{self.project.display_name}.pgn"
        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Exportar PGN",
            str(Path.cwd() / suggested_name),
            "Arquivos PGN (*.pgn);;Arquivos de texto (*.txt);;Todos os arquivos (*)",
        )
        if not file_path:
            return

        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".pgn")

        try:
            write_pgn(output_path, pgn_text)
        except OSError as exc:
            QMessageBox.critical(self, "Exportar PGN", f"Nao foi possivel gravar o arquivo.\n\n{exc}")
            return

        self.statusBar().showMessage(f"{report.actual_nodes} lances exportados para {output_path}", 6000)

    # ------------------------------------------------------------------
    # Autosave e recuperacao
    # ------------------------------------------------------------------

    def _on_text_changed(self):
        self._chars_since_autosave += 1
        if self._chars_since_autosave >= AUTOSAVE_CHAR_THRESHOLD:
            self._autosave()
        self._update_window_title()

    def _autosave(self):
        text = self.editor_panel.document_text()
        self._chars_since_autosave = 0

        if not text.strip() or text == self._last_autosaved_text:
            return

        try:
            write_recovery_snapshot(text, self.project.path)
        except OSError:
            self.lbl_autosave.setText("autosave indisponivel")
            return

        self._last_autosaved_text = text
        self.lbl_autosave.setText("recuperacao gravada")
        QTimer.singleShot(4000, lambda: self.lbl_autosave.setText(""))

    def _offer_session_recovery(self):
        snapshot = read_recovery_snapshot()
        if snapshot is None:
            return

        minutes = int(snapshot.age_seconds // 60)
        when = f"ha {minutes} min" if minutes else "ha menos de 1 min"
        origin = snapshot.origin_path.name if snapshot.origin_path else "projeto sem nome"

        choice = QMessageBox.question(
            self,
            "Recuperar sessao",
            f"O editor foi fechado sem salvar.\n\n"
            f"Origem: {origin}\n"
            f"Gravado {when} ({len(snapshot.raw_text)} caracteres).\n\n"
            "Deseja recuperar esse texto?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.editor_panel.load_document_text(snapshot.raw_text)
            if snapshot.origin_path and snapshot.origin_path.is_file():
                self.project.path = snapshot.origin_path
            self._last_autosaved_text = snapshot.raw_text
            self._update_window_title()
            self.statusBar().showMessage("Sessao recuperada. Salve o projeto para consolidar.", 8000)
        else:
            clear_recovery_snapshot()

    def closeEvent(self, event):
        if self._is_dirty():
            choice = QMessageBox.question(
                self,
                "Sair",
                "Ha alteracoes que ainda nao foram salvas no projeto.\n\nDeseja salvar antes de sair?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save and not self.save_project():
                event.ignore()
                return

        clear_recovery_snapshot()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
