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

from ..core.parser import iter_moves
from ..core.text_ops import promote_variation
from ..services.clipboard_service import COPY_FORMATS
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
from .cleanup_dialog import CleanupDialog
from .editor_panel import EditorPanel
from .issues_panel import IssuesPanel

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

        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self.vertical_splitter)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.vertical_splitter.addWidget(self.splitter)

        self.editor_panel = EditorPanel()
        self.chess_panel = ChessPanel()

        self.splitter.addWidget(self.editor_panel)
        self.splitter.addWidget(self.chess_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        self.issues_panel = IssuesPanel()
        self.vertical_splitter.addWidget(self.issues_panel)
        self.vertical_splitter.setStretchFactor(0, 4)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([640, 180])

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

        self.editor_panel.text_parsed_signal.connect(self._on_parsed)
        self.issues_panel.issue_selected_signal.connect(self.editor_panel.focus_span)
        self.issues_panel.accept_requested_signal.connect(self._accept_issue)
        self.issues_panel.ignore_requested_signal.connect(self._ignore_issue)
        self.issues_panel.learn_requested_signal.connect(self._learn_issue)
        self.issues_panel.comment_requested_signal.connect(self._comment_issue)

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

        structure_menu = self.menuBar().addMenu("&Estrutura")
        self._add_action(structure_menu, "Envolver em comentario", "Ctrl+K", self._wrap_comment)
        self._add_action(structure_menu, "Envolver em variante", "Ctrl+9", self._wrap_variation)
        self._add_action(structure_menu, "Desenvolver (tirar delimitador)", "Ctrl+Shift+K", self._unwrap)
        self._add_action(structure_menu, "Paragrafo vira comentario", "Ctrl+Shift+C", self._paragraph_to_comment)
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Renumerar documento", "Ctrl+R", self._renumber)
        self._add_action(structure_menu, "Promover variante", "Ctrl+Shift+Up", self._promote_variation)
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Limpar texto colado...", "Ctrl+Shift+V", self._clean_paste)
        self._add_action(structure_menu, "Conferir delimitadores", "Ctrl+Shift+B", self._check_balance)

        copy_menu = self.menuBar().addMenu("&Copiar")
        for copy_format in COPY_FORMATS:
            self._add_action(
                copy_menu,
                copy_format.label,
                None,
                lambda _checked=False, key=copy_format.key: self._copy_excerpt(key),
            )

        move_menu = self.menuBar().addMenu("&Lances")
        self._add_action(move_menu, "Lance anterior", "Ctrl+Left", self.chess_panel.go_prev)
        self._add_action(move_menu, "Proximo lance", "Ctrl+Right", self.chess_panel.go_next)
        move_menu.addSeparator()
        self._add_action(move_menu, "Inverter tabuleiro", "Ctrl+F", self.chess_panel.flip_board)

        review_menu = self.menuBar().addMenu("&Revisao")
        self._add_action(review_menu, "Proximo alerta", "F8", lambda: self.issues_panel.go_to_next_issue(1))
        self._add_action(review_menu, "Alerta anterior", "Shift+F8", lambda: self.issues_panel.go_to_next_issue(-1))
        self._add_action(review_menu, "Aceitar correcao", "F4", self.issues_panel.accept_selected)
        review_menu.addSeparator()
        self._add_action(review_menu, "Mostrar/ocultar alertas", "F9", self._toggle_issues_panel)

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
        self.editor_panel.locale = project.meta.locale
        self.editor_panel.normalizer.locale = project.meta.locale
        self.editor_panel.parser.set_locale(project.meta.locale)
        self.editor_panel.parser.set_dictionary(project.dictionary)
        self.editor_panel.parser.set_ignored_tokens(project.state.ignored_issue_tokens)
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
    # Revisao de alertas
    # ------------------------------------------------------------------

    def _on_parsed(self, ast):
        self.issues_panel.set_issues(ast.issues)
        self.chess_panel.start_fen = self.editor_panel.current_start_fen

    # ------------------------------------------------------------------
    # Gestos de edicao estrutural
    # ------------------------------------------------------------------

    def _report(self, message: str):
        if message:
            self.statusBar().showMessage(message, 5000)

    def _wrap_comment(self):
        self._report(self.editor_panel.action_wrap_comment())

    def _wrap_variation(self):
        self._report(self.editor_panel.action_wrap_variation())

    def _unwrap(self):
        self._report(self.editor_panel.action_unwrap())

    def _paragraph_to_comment(self):
        self._report(self.editor_panel.action_paragraph_to_comment())

    def _renumber(self):
        self._report(self.editor_panel.action_renumber())

    def _promote_variation(self):
        entry = self.chess_panel.current_entry()
        if entry is None or len(entry.path) < 2:
            self._report("Selecione um lance de variante para promove-la.")
            return

        owner, variation_index = self.chess_panel.variation_owner_of(entry)
        if owner is None:
            self._report("Este lance ja esta na linha principal.")
            return

        result = promote_variation(self.editor_panel.document_text(), self.editor_panel.current_ast, owner, variation_index)
        self._report(self.editor_panel.apply_operation(result) or result.message)

    def _clean_paste(self):
        text = self.editor_panel.document_text()
        if not text.strip():
            self._report("Nao ha texto para limpar.")
            return

        dialog = CleanupDialog(text, self)
        if dialog.exec() == CleanupDialog.DialogCode.Accepted:
            self._report(self.editor_panel.action_clean_paste(dialog.options()))

    def _check_balance(self):
        problems = self.editor_panel.balance_problems()
        if not problems:
            self._report("Delimitadores balanceados.")
            return

        self.editor_panel.focus_span(problems[0].position, problems[0].position + 1)
        detail = "\n".join(f"- posicao {problem.position}: {problem.message}" for problem in problems[:12])
        QMessageBox.warning(
            self,
            "Delimitadores",
            f"{len(problems)} problema(s) encontrado(s):\n\n{detail}",
        )

    def _copy_excerpt(self, key: str):
        text = self.chess_panel.copy_excerpt(key)
        if text:
            first_line = text.splitlines()[0]
            preview = first_line[:60] + ("..." if len(first_line) > 60 else "")
            self._report(f"Copiado: {preview}")

    def _toggle_issues_panel(self):
        self.issues_panel.setVisible(not self.issues_panel.isVisible())

    def _accept_issue(self, issue):
        """Grava a correcao no texto bruto -- o alerta some porque a causa sumiu."""
        candidate = issue.best_candidate
        if not candidate:
            return
        self.editor_panel.replace_span(issue.raw_start, issue.raw_end, candidate.san)
        self.statusBar().showMessage(f"'{issue.raw_text}' substituido por '{candidate.san}'.", 4000)

    def _comment_issue(self, issue):
        self.editor_panel.wrap_span_as_comment(issue.raw_start, issue.raw_end)
        self.statusBar().showMessage(f"'{issue.raw_text}' virou comentario.", 4000)

    def _ignore_issue(self, issue):
        token = issue.raw_text
        if not token or token in self.project.state.ignored_issue_tokens:
            return
        self.project.state.ignored_issue_tokens.append(token)
        self.editor_panel.set_ignored_tokens(self.project.state.ignored_issue_tokens)
        self.statusBar().showMessage(f"'{token}' passa a ser tratado como texto neste projeto.", 5000)

    def _learn_issue(self, issue):
        candidate = issue.best_candidate
        if not candidate or not issue.raw_text:
            return
        self.project.dictionary[issue.raw_text] = candidate.san
        self.editor_panel.set_dictionary(self.project.dictionary)
        self.statusBar().showMessage(
            f"Dicionario do projeto: '{issue.raw_text}' -> '{candidate.san}'. Salve o projeto para guardar.",
            6000,
        )

    # ------------------------------------------------------------------
    # Exportacao
    # ------------------------------------------------------------------

    def export_pgn(self):
        ast = self.editor_panel.reparse_now()
        if not ast.moves and not ast.result:
            QMessageBox.information(self, "Exportar PGN", "Nao ha PGN analisado para exportar.")
            return

        pgn_text, report = build_pgn(ast, self.editor_panel.current_start_fen)

        pending = [move for move in iter_moves(ast.moves) if move.needs_review]
        if pending:
            preview = ", ".join(f"{move.san} -> {move.display_san}" for move in pending[:5])
            choice = QMessageBox.warning(
                self,
                "Correcoes por conferir",
                f"{len(pending)} lance(s) foram corrigidos automaticamente sem certeza e ainda\n"
                f"nao foram conferidos:\n\n{preview}\n\n"
                "Exportar assim mesmo?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Save:
                return

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
