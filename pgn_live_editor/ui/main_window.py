import sys
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt, QTimer
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

from ..core.issues import SUSPECT_BLUNDER
from ..core.parser import iter_moves
from ..core.substitutions import LAYER_PROJECT, SCOPE_TOKEN
from ..core.text_ops import (
    move_variation,
    promote_variation,
    reanchor_variation,
    set_move_annotations,
)
from ..services.clipboard_service import COPY_FORMATS
from ..services.dictionary_service import DictionaryService
from ..services.engine_service import (
    ENGINE_ENV_VAR,
    EngineScanner,
    EngineSettings,
    ScanFindings,
    discover_engine_path,
    load_engine_settings,
    save_engine_settings,
)
from ..services.export_service import (
    build_book_pgn,
    build_normalized_text,
    build_pgn,
    build_structural_json,
    game_file_name,
    write_pgn,
)
from ..services.parse_worker import SegmentAnalysis, build_games_for_export
from ..services.preferences_service import Layout, load_preferences, save_preferences
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
from .dictionary_dialog import DictionaryDialog
from .editor_panel import EditorPanel
from .games_panel import GamesPanel
from .header_dialog import HeaderDialog
from .issues_panel import IssuesPanel
from .pdf_panel import PdfPanel
from .preferences_dialog import PreferencesDialog
from .theme import apply_theme

AUTOSAVE_INTERVAL_MS = 30_000
AUTOSAVE_CHAR_THRESHOLD = 200


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PGN Live Editor for Chess PDF Text")
        self.resize(1280, 860)

        self.project = Project()
        self.preferences = load_preferences()
        self._chars_since_autosave = 0
        self._last_autosaved_text = ""
        # Motor de xadrez (fase 5): opcional, sob demanda, fora da thread de UI.
        self.engine_settings: EngineSettings = load_engine_settings()
        self.dictionary_service = DictionaryService(locale=self.preferences.locale)
        self.engine_scanner = EngineScanner(self)
        self._engine_findings: ScanFindings | None = None
        self._pending_engine_segment: tuple[str, int] = ("", 0)
        # Marcas de partida concluida e template do livro nao estao no texto
        # bruto -- sem isto elas sumiriam sem aviso ao fechar a janela.
        self._state_dirty = False

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self.vertical_splitter)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.vertical_splitter.addWidget(self.splitter)

        self.games_panel = GamesPanel()
        self.pdf_panel = PdfPanel()
        self.editor_panel = EditorPanel()
        self.chess_panel = ChessPanel()

        self.splitter.addWidget(self.games_panel)
        self.splitter.addWidget(self.pdf_panel)
        self.splitter.addWidget(self.editor_panel)
        self.splitter.addWidget(self.chess_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setStretchFactor(2, 3)
        self.splitter.setStretchFactor(3, 3)
        self.splitter.setSizes([210, 420, 560, 500])
        # Um livro de uma partida so nao precisa de lista de partidas, e quem
        # nao tem o PDF a mao nao precisa do painel dele ocupando a tela.
        self.games_panel.setVisible(False)
        self.pdf_panel.setVisible(False)

        self.issues_panel = IssuesPanel()
        self.vertical_splitter.addWidget(self.issues_panel)
        self.vertical_splitter.setStretchFactor(0, 4)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([640, 180])

        self.lbl_touched = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_touched)
        self.lbl_book = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_book)
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
        self.editor_panel.document_analyzed_signal.connect(self._on_document_analyzed)
        self.games_panel.game_selected_signal.connect(self.editor_panel.go_to_segment)
        self.games_panel.completion_toggled_signal.connect(self._on_completion_toggled)
        self.games_panel.analyze_book_requested_signal.connect(self._analyze_book)
        self.chess_panel.start_fen_requested_signal.connect(self._set_start_fen_from_board)
        self.chess_panel.annotations_changed_signal.connect(self._on_annotations_changed)
        self.issues_panel.issue_selected_signal.connect(self.editor_panel.focus_span)
        self.issues_panel.accept_requested_signal.connect(self._accept_issue)
        self.issues_panel.ignore_requested_signal.connect(self._ignore_issue)
        self.issues_panel.learn_requested_signal.connect(self._learn_issue)
        self.issues_panel.comment_requested_signal.connect(self._comment_issue)
        self.pdf_panel.text_requested_signal.connect(self._insert_from_pdf)
        self.pdf_panel.page_changed_signal.connect(self._on_pdf_page_changed)
        self.pdf_panel.document_opened_signal.connect(self._on_pdf_opened)
        self.engine_scanner.finished_signal.connect(self._on_engine_scan_finished)
        self.engine_scanner.failed_signal.connect(self._on_engine_scan_failed)

        self.editor_panel.text_edit.textChanged.connect(self._on_text_changed)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start()

        self._apply_preferences()
        self._restore_layout()
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
        self._add_action(file_menu, "Exportar esta partida...", "Ctrl+Shift+E", self.export_pgn)
        self._add_action(file_menu, "Exportar o livro num arquivo...", None, self.export_book)
        self._add_action(file_menu, "Exportar uma partida por arquivo...", None, self.export_games_separately)
        self._add_action(file_menu, "Exportar analise em .json...", None, self.export_structural_json)
        self._add_action(file_menu, "Exportar texto normalizado em .txt...", None, self.export_normalized_text)

        file_menu.addSeparator()
        file_menu.addSeparator()
        self._add_action(file_menu, "Preferencias...", "Ctrl+,", self.edit_preferences)
        file_menu.addSeparator()
        self._add_action(file_menu, "Sair", QKeySequence.StandardKey.Quit, self.close)

        book_menu = self.menuBar().addMenu("&Partidas")
        self._add_action(book_menu, "Proxima partida", "Ctrl+Shift+P", lambda: self._step_game(1))
        self._add_action(book_menu, "Partida anterior", "Ctrl+Shift+O", lambda: self._step_game(-1))
        book_menu.addSeparator()
        self._add_action(book_menu, "Cabecalhos da partida...", "Ctrl+H", self.edit_headers)
        self._add_action(book_menu, "Marcar como concluida", "Ctrl+Shift+D", self.games_panel.toggle_current_completion)
        self._add_action(book_menu, "Analisar o livro inteiro", None, self._analyze_book)
        book_menu.addSeparator()
        self._add_action(book_menu, "Usar a posicao do tabuleiro como inicial", None, self._set_start_fen_from_board)
        self._add_action(book_menu, "Converter marcadores de diagrama", None, self._normalize_diagrams)
        book_menu.addSeparator()
        self._add_action(book_menu, "Mostrar/ocultar lista de partidas", "F10", self._toggle_games_panel)
        book_menu.addSeparator()
        self._add_action(book_menu, "Abrir o PDF do livro...", None, self.pdf_panel.choose_pdf)
        self._add_action(book_menu, "Mostrar/ocultar o PDF", "F11", self._toggle_pdf_panel)

        structure_menu = self.menuBar().addMenu("&Estrutura")
        self._add_action(structure_menu, "Envolver em comentario", "Ctrl+K", self._wrap_comment)
        self._add_action(structure_menu, "Envolver em variante", "Ctrl+9", self._wrap_variation)
        self._add_action(structure_menu, "Desenvolver (tirar delimitador)", "Ctrl+Shift+K", self._unwrap)
        self._add_action(structure_menu, "Paragrafo vira comentario", "Ctrl+Shift+C", self._paragraph_to_comment)
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Renumerar documento", "Ctrl+R", self._renumber)
        self._add_action(structure_menu, "Promover variante", "Ctrl+Shift+Up", self._promote_variation)
        self._add_action(
            structure_menu, "Rebaixar a linha principal", "Ctrl+Shift+Down", self._demote_mainline
        )
        self._add_action(structure_menu, "Subir a variante", "Alt+Up", lambda: self._move_variation(-1))
        self._add_action(structure_menu, "Descer a variante", "Alt+Down", lambda: self._move_variation(1))
        self._add_action(structure_menu, "Reancorar a variante aqui", "Ctrl+Shift+A", self._reanchor_variation)
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
        move_menu.addSeparator()
        self._add_action(move_menu, "Primeiro lance", "Ctrl+Home", self.chess_panel.go_first)
        self._add_action(move_menu, "Ultimo lance", "Ctrl+End", self.chess_panel.go_last)
        self._add_action(move_menu, "Sugerir lance legal", "Ctrl+Space", self._suggest_legal_move)
        self._add_action(move_menu, "Paleta de simbolos", "Ctrl+D", self.editor_panel.open_symbol_palette)

        review_menu = self.menuBar().addMenu("&Revisao")
        self._add_action(review_menu, "Proximo alerta", "F8", lambda: self.issues_panel.go_to_next_issue(1))
        self._add_action(review_menu, "Alerta anterior", "Shift+F8", lambda: self.issues_panel.go_to_next_issue(-1))
        self._add_action(review_menu, "Aceitar correcao", "F4", self.issues_panel.accept_selected)
        review_menu.addSeparator()
        self._add_action(review_menu, "Conferir a partida com o motor", "Ctrl+E", self._scan_with_engine)
        self._add_action(review_menu, "Motor de xadrez...", None, self._configure_engine)
        review_menu.addSeparator()
        self._add_action(review_menu, "Dicionario de OCR...", "Ctrl+Shift+I", self.edit_dictionary)
        review_menu.addSeparator()
        self._add_action(review_menu, "Mostrar/ocultar alertas", "F9", self._toggle_issues_panel)
        self.action_review_mode = self._add_action(
            review_menu, "Dupla visao: so o que foi mexido", "F12", self._toggle_review_mode
        )
        self.action_review_mode.setCheckable(True)

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
        return self._state_dirty or self.editor_panel.document_text() != self.project.raw_text

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
        self.dictionary_service.set_project(self.project.dictionary)
        self.editor_panel.set_dictionary(self.dictionary_service.merged())
        self.editor_panel.load_document_text("")
        clear_recovery_snapshot()
        self._last_autosaved_text = ""
        self._state_dirty = False
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
        self.dictionary_service.set_locale(project.meta.locale)
        self.dictionary_service.set_project(project.dictionary)
        self.editor_panel.configure_recognition(
            locale=project.meta.locale,
            dictionary=self.dictionary_service.merged(),
            ignored_tokens=project.state.ignored_issue_tokens,
        )
        self.games_panel.set_completed(project.state.completed_games)
        self._restore_pdf(project)
        self.editor_panel.load_document_text(project.raw_text, project.state.cursor_position)
        self._last_autosaved_text = project.raw_text
        self._chars_since_autosave = 0
        self._state_dirty = False
        remember_recent_file(path)
        self._rebuild_recent_menu()
        self._update_window_title()
        self.statusBar().showMessage(f"Projeto aberto: {path.name}", 5000)

    def _restore_pdf(self, project):
        """O PDF que acompanha o trabalho, na pagina em que se parou."""
        target = project.state.pdf_path
        if not target or not Path(target).is_file():
            self.pdf_panel.close_pdf()
            self.pdf_panel.setVisible(False)
            return

        problem = self.pdf_panel.open_pdf(Path(target), project.state.pdf_page)
        if problem:
            self._report(problem)
            return
        self.pdf_panel.setVisible(True)

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
        self._state_dirty = False
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
        self.games_panel.set_completed([])
        self.editor_panel.load_document_text(text)
        self._last_autosaved_text = ""
        self._state_dirty = False
        self._update_window_title()
        self.statusBar().showMessage(f"Importado: {path.name} ({len(text)} caracteres)", 5000)

    # ------------------------------------------------------------------
    # Revisao de alertas
    # ------------------------------------------------------------------

    def _on_parsed(self, ast):
        self.issues_panel.set_issues(ast.issues + self._live_engine_issues())
        self.chess_panel.start_fen = self.editor_panel.current_start_fen

    # ------------------------------------------------------------------
    # Escala de livro (fase 4)
    # ------------------------------------------------------------------

    def _on_document_analyzed(self, analysis):
        self.games_panel.set_completed(self.project.state.completed_games)
        self.games_panel.set_summaries(analysis.summaries(), analysis.active_index)
        # A lista so aparece quando ha livro: uma partida sozinha nao precisa dela.
        self.games_panel.setVisible(analysis.game_count > 1)
        self._update_book_status(analysis)
        self._update_touch_status()

    def _update_book_status(self, analysis):
        moves = len(list(iter_moves(analysis.ast.moves)))
        if analysis.game_count > 1:
            done = len([index for index in self.project.state.completed_games if index < analysis.game_count])
            self.lbl_book.setText(
                f"partida {analysis.active_index + 1}/{analysis.game_count} | {moves} lances | {done} concluida(s)"
            )
        else:
            self.lbl_book.setText(f"{moves} lances")

    def _on_completion_toggled(self, index: int, completed: bool):
        marked = set(self.project.state.completed_games)
        if completed:
            marked.add(index)
        else:
            marked.discard(index)

        self.project.state.completed_games = sorted(marked)
        self._state_dirty = True
        self._update_book_status(self.editor_panel.analysis)
        self._update_window_title()

    def _step_game(self, delta: int):
        analysis = self.editor_panel.analysis
        if analysis.game_count <= 1:
            self._report("Este documento tem uma partida so.")
            return

        target = (analysis.active_index + delta) % analysis.game_count
        self.editor_panel.go_to_segment(target)

    def _analyze_book(self):
        analyses = self.editor_panel.analyze_whole_book()
        moves = sum(analysis.move_count for analysis in analyses)
        errors = sum(analysis.issue_counts["error"] for analysis in analyses)
        suffix = f", {errors} erro(s)" if errors else ""
        self._report(f"{len(analyses)} partida(s) analisada(s): {moves} lances{suffix}.")

    def _toggle_games_panel(self):
        self.games_panel.setVisible(not self.games_panel.isVisible())

    def _toggle_pdf_panel(self):
        self.pdf_panel.setVisible(not self.pdf_panel.isVisible())

    # ------------------------------------------------------------------
    # PDF ao lado (fase 5)
    # ------------------------------------------------------------------

    def _insert_from_pdf(self, text: str):
        """O trecho escolhido no PDF entra no cursor, como uma colagem normal."""
        self.editor_panel.insert_text_at_cursor(text)
        self.editor_panel.text_edit.setFocus()
        self._report(f"{len(text)} caractere(s) vindos do PDF.")

    def _on_pdf_page_changed(self, page: int):
        """A pagina fica guardada no projeto: reabrir volta onde parou."""
        if self.project.state.pdf_page != page:
            self.project.state.pdf_page = page
            self._state_dirty = True

    def _on_pdf_opened(self, path: str):
        self.project.state.pdf_path = path
        self._state_dirty = True
        self.pdf_panel.setVisible(True)
        self._update_window_title()

    def _set_start_fen_from_board(self):
        self._report(self.editor_panel.action_set_start_fen(self.chess_panel.board.fen()))

    def _normalize_diagrams(self):
        self._report(self.editor_panel.action_normalize_diagrams())

    def edit_headers(self):
        segment = self.editor_panel.active_segment()
        headers = dict(segment.headers) if segment else {}

        dialog = HeaderDialog(headers, self.project.header_template, self)
        if dialog.exec() != HeaderDialog.DialogCode.Accepted:
            return

        if dialog.should_save_template:
            self.project.header_template = dialog.book_template_values()
            self._state_dirty = True

        values = dialog.values()
        # A posicao inicial nao passa pelo dialogo; nao pode sumir por causa dele.
        for tag in ("FEN", "SetUp"):
            if tag in headers:
                values[tag] = headers[tag]

        self._report(self.editor_panel.action_apply_headers(values))

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

    def _on_annotations_changed(self, node_id: str, annotations):
        """A seta desenhada no tabuleiro vira `[%cal]` no texto bruto.

        Passa pelo mesmo caminho de qualquer outro gesto: uma edicao no texto,
        um Ctrl+Z para desfazer, reanalise em seguida. Nao existe anotacao que
        viva fora do texto.
        """
        entry = self.chess_panel.entry_by_node_id.get(node_id)
        if entry is None:
            return

        result = set_move_annotations(self.editor_panel.document_text(), entry.node, annotations)
        if result.ok:
            self._report(self.editor_panel.apply_operation(result) or result.message)

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

    def _variation_target(self):
        """`(dono, indice)` da variante sob o cursor, ou `(None, None)`."""
        entry = self.chess_panel.current_entry()
        if entry is None or len(entry.path) < 2:
            return (None, None)
        return self.chess_panel.variation_owner_of(entry)

    def _demote_mainline(self):
        """O inverso de promover: a linha principal daqui vira variante.

        E a mesma troca, vista do outro lado -- por isso o lance sob o cursor
        precisa ter uma variante para trocar de lugar com ele.
        """
        entry = self.chess_panel.current_entry()
        if entry is None or not entry.node.variations:
            self._report("Ponha o cursor num lance que tenha variante para rebaixar a linha principal.")
            return

        result = promote_variation(
            self.editor_panel.document_text(), self.editor_panel.current_ast, entry.node, 0
        )
        self._report(self.editor_panel.apply_operation(result) or result.message)

    def _move_variation(self, delta: int):
        owner, variation_index = self._variation_target()
        if owner is None:
            self._report("Selecione um lance de variante para move-la entre as irmas.")
            return

        result = move_variation(self.editor_panel.document_text(), owner, variation_index, delta)
        self._report(self.editor_panel.apply_operation(result) or result.message)

    def _reanchor_variation(self):
        """Pendura a variante selecionada no lance onde o cursor esta.

        A ancoragem por legalidade acerta quase sempre; quando erra, este e o
        caminho de volta -- sem recortar e colar a mao.
        """
        owner, variation_index = self._variation_target()
        if owner is None:
            self._report("Selecione um lance da variante que precisa mudar de lugar.")
            return

        destination = self.editor_panel.move_at_cursor()
        if destination is None:
            self._report("Ponha o cursor no lance que deve passar a segurar a variante.")
            return

        result = reanchor_variation(self.editor_panel.document_text(), owner, variation_index, destination)
        self._report(self.editor_panel.apply_operation(result) or result.message)

    def _suggest_legal_move(self):
        """Ctrl+Espaco: o que e legal aqui, quando o livro esta ilegivel."""
        suggestions = self.editor_panel.legal_move_suggestions()
        if not suggestions:
            self._report("Nao da para saber a posicao neste ponto do texto.")
            return
        self._report("Legais aqui: " + ", ".join(suggestions))

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

    # ------------------------------------------------------------------
    # Preferencias e layout (fase 6)
    # ------------------------------------------------------------------

    def edit_preferences(self):
        dialog = PreferencesDialog(self.preferences, self)
        if dialog.exec() != PreferencesDialog.DialogCode.Accepted:
            return

        self.preferences = dialog.values()
        save_preferences(self.preferences)
        self._apply_preferences()
        self._report("Preferencias gravadas.")

    def _apply_preferences(self):
        """Poe as preferencias em uso agora, sem precisar reabrir o programa."""
        application = QApplication.instance()
        if application is not None:
            apply_theme(application, self.preferences.theme)

        self.editor_panel.set_theme(self.preferences.theme)
        self.editor_panel.set_editor_font(
            self.preferences.editor_font_family, self.preferences.editor_font_size
        )
        self.editor_panel.text_edit.auto_close_enabled = self.preferences.auto_close_delimiters
        self.editor_panel.set_locale(self.preferences.locale)
        self.dictionary_service.set_locale(self.preferences.locale)

        self.engine_settings = replace(
            self.engine_settings,
            path=self.preferences.engine_path,
            movetime_ms=self.preferences.engine_movetime_ms,
            threshold_cp=self.preferences.engine_threshold_cp,
        )
        save_engine_settings(self.engine_settings)

    def _restore_layout(self):
        """Devolve a janela ao tamanho e a divisao em que foi fechada."""
        layout = self.preferences.layout
        if layout.geometry:
            self.restoreGeometry(QByteArray.fromBase64(layout.geometry.encode("ascii")))
        if layout.window_state:
            self.restoreState(QByteArray.fromBase64(layout.window_state.encode("ascii")))
        if layout.horizontal_splitter:
            self.splitter.restoreState(QByteArray.fromBase64(layout.horizontal_splitter.encode("ascii")))
        if layout.vertical_splitter:
            self.vertical_splitter.restoreState(QByteArray.fromBase64(layout.vertical_splitter.encode("ascii")))

        self.issues_panel.setVisible(layout.issues_panel_visible)
        # A lista de partidas e o PDF continuam mandados pelo conteudo: aparecem
        # quando ha livro e quando ha PDF, nao porque estavam abertos ontem.

    def _capture_layout(self) -> Layout:
        return Layout(
            geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"),
            window_state=bytes(self.saveState().toBase64()).decode("ascii"),
            horizontal_splitter=bytes(self.splitter.saveState().toBase64()).decode("ascii"),
            vertical_splitter=bytes(self.vertical_splitter.saveState().toBase64()).decode("ascii"),
            games_panel_visible=self.games_panel.isVisible(),
            pdf_panel_visible=self.pdf_panel.isVisible(),
            issues_panel_visible=self.issues_panel.isVisible(),
        )

    def _toggle_issues_panel(self):
        self.issues_panel.setVisible(not self.issues_panel.isVisible())

    def _toggle_review_mode(self):
        """Dupla visao: apaga o confirmado para sobrar o que o programa mexeu."""
        enabled = self.action_review_mode.isChecked()
        self.editor_panel.set_review_mode(enabled)
        self._update_touch_status()

        if not enabled:
            self._report("Realce normal.")
            return

        counts = self.editor_panel.touch_counts()
        touched = counts["corrigido"] + counts["por_conferir"] + counts["nao_entendido"]
        if touched:
            self._report(f"Dupla visao: {touched} lance(s) que o programa mexeu, de {sum(counts.values())}.")
        else:
            self._report("Dupla visao: o programa nao mexeu em nenhum lance deste livro.")

    def _update_touch_status(self):
        """A conta que o modo dupla visao existe para mostrar."""
        if not self.editor_panel.review_mode:
            self.lbl_touched.setText("")
            return

        counts = self.editor_panel.touch_counts()
        self.lbl_touched.setText(
            f"confirmados {counts['confirmado']} | "
            f"corrigidos {counts['corrigido']} | "
            f"por conferir {counts['por_conferir']} | "
            f"nao entendidos {counts['nao_entendido']}"
        )

    def _accept_issue(self, issue):
        """Grava a correcao no texto bruto -- o alerta some porque a causa sumiu."""
        candidate = issue.best_candidate
        if not candidate:
            return
        self.editor_panel.replace_span(issue.raw_start, issue.raw_end, candidate.san)
        self.statusBar().showMessage(f"'{issue.raw_text}' substituido por '{candidate.san}'.", 4000)
        self._count_acceptance(issue, candidate)

    def _comment_issue(self, issue):
        self.editor_panel.wrap_span_as_comment(issue.raw_start, issue.raw_end)
        self.statusBar().showMessage(f"'{issue.raw_text}' virou comentario.", 4000)

    def _ignore_issue(self, issue):
        if issue.code == SUSPECT_BLUNDER:
            # Suspeita do motor nao e token de OCR: silenciar aqui e so tirar da
            # lista. Mandar o parser tratar o lance como texto destruiria a partida.
            self._dismiss_engine_issue(issue)
            self.statusBar().showMessage(f"Suspeita do motor sobre '{issue.raw_text}' dispensada.", 4000)
            return

        token = issue.raw_text
        if not token or token in self.project.state.ignored_issue_tokens:
            return
        self.project.state.ignored_issue_tokens.append(token)
        self.editor_panel.set_ignored_tokens(self.project.state.ignored_issue_tokens)
        self.statusBar().showMessage(f"'{token}' passa a ser tratado como texto neste projeto.", 5000)

    # ------------------------------------------------------------------
    # Motor de xadrez (fase 5)
    # ------------------------------------------------------------------

    def _live_engine_issues(self) -> list:
        """Alertas do motor que ainda valem para a partida sob o cursor.

        A varredura custa segundos e a reanalise acontece a cada tecla: sem esta
        conferencia, os alertas do motor ou sumiriam no primeiro caractere ou
        ficariam apontando para o lugar errado.
        """
        findings = self._engine_findings
        segment = self.editor_panel.active_segment()
        if findings is None:
            return []

        body = segment.body if segment else self.editor_panel.document_text()
        if not findings.matches(body):
            # O texto desta partida mudou: a varredura falava de outro texto.
            self._engine_findings = None
            return []

        return findings.shifted_to(segment.raw_offset if segment else 0)

    def _refresh_issues(self):
        self.issues_panel.set_issues(self.editor_panel.current_ast.issues + self._live_engine_issues())

    def _scan_with_engine(self):
        if self.engine_scanner.is_running:
            self._report("O motor ja esta conferindo uma partida.")
            return

        if not discover_engine_path(self.engine_settings.path):
            self._offer_engine_setup()
            return

        ast = self.editor_panel.reparse_now()
        if not ast.moves:
            self._report("Nao ha linha principal para o motor conferir.")
            return

        segment = self.editor_panel.active_segment()
        self._pending_engine_segment = (
            segment.body if segment else self.editor_panel.document_text(),
            segment.raw_offset if segment else 0,
        )
        self.statusBar().showMessage("Motor conferindo a linha principal...")
        self.engine_scanner.scan(ast, self.editor_panel.current_start_fen, self.engine_settings)

    def _on_engine_scan_finished(self, report):
        body, offset = self._pending_engine_segment
        self._engine_findings = ScanFindings(segment_body=body, raw_offset=offset, issues=list(report.issues))

        live = self._live_engine_issues()
        self._refresh_issues()

        if report.issues and not live:
            self._report("O texto mudou durante a conferencia; rode o motor de novo.")
            return
        self._report(report.summary())

    def _on_engine_scan_failed(self, message: str):
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Motor de xadrez", message)

    def _dismiss_engine_issue(self, issue):
        """Tira uma suspeita do motor da lista, sem tocar no texto bruto."""
        findings = self._engine_findings
        if findings is None:
            return

        segment = self.editor_panel.active_segment()
        delta = (segment.raw_offset if segment else 0) - findings.raw_offset
        original_start = issue.raw_start - delta
        findings.issues = [
            other
            for other in findings.issues
            if not (other.code == issue.code and other.raw_start == original_start)
        ]
        self._refresh_issues()

    def _configure_engine(self):
        current = discover_engine_path(self.engine_settings.path)
        file_path, _filter = QFileDialog.getOpenFileName(
            self,
            "Executavel do motor UCI",
            current or str(Path.cwd()),
            "Executaveis (*.exe);;Todos os arquivos (*)",
        )
        if not file_path:
            return

        self.engine_settings = replace(self.engine_settings, path=file_path)
        save_engine_settings(self.engine_settings)
        self._report(f"Motor de xadrez: {Path(file_path).name}")

    def _offer_engine_setup(self):
        choice = QMessageBox.question(
            self,
            "Motor de xadrez",
            "Nenhum motor UCI foi encontrado.\n\n"
            "O motor e opcional. Ele serve para desconfiar de um lance de linha\n"
            "principal que derruba a avaliacao sem o livro marcar com '?' -- que\n"
            "quase sempre e erro de leitura, nao erro do mestre.\n\n"
            f"Instale o Stockfish no PATH, aponte o caminho em {ENGINE_ENV_VAR},\n"
            "ou escolha o executavel agora.",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Open,
        )
        if choice == QMessageBox.StandardButton.Open:
            self._configure_engine()

    # ------------------------------------------------------------------

    def _learn_issue(self, issue):
        candidate = issue.best_candidate
        if not candidate or not issue.raw_text:
            return
        self._learn_substitution(issue.raw_text, candidate.san)
        self.statusBar().showMessage(
            f"Dicionario do projeto: '{issue.raw_text}' -> '{candidate.san}'. Salve o projeto para guardar.",
            6000,
        )

    def _learn_substitution(self, pattern: str, replacement: str, layer: str = LAYER_PROJECT):
        self.dictionary_service.learn(pattern, replacement, SCOPE_TOKEN, layer)
        self.project.dictionary = self.dictionary_service.project
        self.editor_panel.set_dictionary(self.dictionary_service.merged())
        self._state_dirty = True
        self._update_window_title()

    def edit_dictionary(self):
        """Editor das tres camadas, com o alcance de cada regra no texto atual."""
        dialog = DictionaryDialog(self.dictionary_service, self.editor_panel.document_text(), self)
        dialog.exec()

        self.project.dictionary = self.dictionary_service.project
        self.editor_panel.set_dictionary(self.dictionary_service.merged())
        self._state_dirty = True
        self._update_window_title()

    def _count_acceptance(self, issue, candidate):
        """Tres aceitacoes da mesma correcao e o scanner, nao acaso (SPEC 5.2)."""
        if not issue.raw_text or not candidate:
            return

        times = DictionaryService.register_acceptance(
            self.project.state.accepted_corrections, issue.raw_text, candidate.san
        )
        self._state_dirty = True
        if not DictionaryService.should_offer_to_learn(times):
            return

        choice = QMessageBox.question(
            self,
            "Aprender esta correcao",
            f"Voce ja aceitou '{issue.raw_text}' -> '{candidate.san}' {times} vezes.\n\n"
            "Gravar no dicionario para o programa passar a fazer isso sozinho?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self._learn_substitution(issue.raw_text, candidate.san)

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
    # Exportacao em lote (fase 4)
    # ------------------------------------------------------------------

    def _book_analyses(self) -> list[SegmentAnalysis]:
        """Todas as partidas do documento, analisadas e com conteudo."""
        return [
            analysis
            for analysis in self.editor_panel.analyze_whole_book()
            if analysis.ast.moves or analysis.ast.result or analysis.segment.headers
        ]

    def _confirm_book_report(self, report) -> bool:
        if report.ok:
            return True

        choice = QMessageBox.warning(
            self,
            "Conferencia do livro",
            f"{report.summary()}\n\nSalvar assim mesmo?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Save

    def _export_other_format(self, title: str, suffix: str, builder, file_filter: str):
        """Caminho comum das saidas que nao sao PGN (SPEC 5.5)."""
        analyses = self.editor_panel.analyze_whole_book()
        games = build_games_for_export(analyses)
        if not games:
            QMessageBox.information(self, title, "Nao ha partida analisada para exportar.")
            return

        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            title,
            str(Path.cwd() / f"{self.project.display_name}{suffix}"),
            file_filter,
        )
        if not file_path:
            return

        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(suffix)

        try:
            output_path.write_text(builder(games), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, title, f"Nao foi possivel gravar o arquivo.\n\n{exc}")
            return

        self._report(f"{len(games)} partida(s) em {output_path.name}.")

    def export_structural_json(self):
        self._export_other_format(
            "Exportar analise em .json",
            ".json",
            build_structural_json,
            "Arquivos JSON (*.json);;Todos os arquivos (*)",
        )

    def export_normalized_text(self):
        self._export_other_format(
            "Exportar texto normalizado",
            ".txt",
            build_normalized_text,
            "Arquivos de texto (*.txt);;Todos os arquivos (*)",
        )

    def export_book(self):
        analyses = self._book_analyses()
        if not analyses:
            QMessageBox.information(self, "Exportar livro", "Nao ha partida analisada para exportar.")
            return

        pgn_text, report = build_book_pgn(build_games_for_export(analyses))
        if not self._confirm_book_report(report):
            return

        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Exportar livro",
            str(Path.cwd() / f"{self.project.display_name}.pgn"),
            "Arquivos PGN (*.pgn);;Todos os arquivos (*)",
        )
        if not file_path:
            return

        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".pgn")

        try:
            write_pgn(output_path, pgn_text)
        except OSError as exc:
            QMessageBox.critical(self, "Exportar livro", f"Nao foi possivel gravar o arquivo.\n\n{exc}")
            return

        self.statusBar().showMessage(f"{report.summary()} Arquivo: {output_path}", 8000)

    def export_games_separately(self):
        analyses = self._book_analyses()
        if not analyses:
            QMessageBox.information(self, "Exportar partidas", "Nao ha partida analisada para exportar.")
            return

        _book_text, report = build_book_pgn(build_games_for_export(analyses))
        if not self._confirm_book_report(report):
            return

        directory = QFileDialog.getExistingDirectory(self, "Pasta de destino", str(Path.cwd()))
        if not directory:
            return

        target_dir = Path(directory)
        written: list[Path] = []
        try:
            for position, analysis in enumerate(analyses):
                pgn_text, _game_report = build_pgn(analysis.ast, analysis.start_fen)
                name = game_file_name(position, analysis.segment.headers, self.project.display_name)
                write_pgn(target_dir / name, pgn_text)
                written.append(target_dir / name)
        except OSError as exc:
            QMessageBox.critical(self, "Exportar partidas", f"Nao foi possivel gravar os arquivos.\n\n{exc}")
            return

        self.statusBar().showMessage(f"{len(written)} arquivo(s) gravado(s) em {target_dir}", 8000)

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

        self.preferences.layout = self._capture_layout()
        save_preferences(self.preferences)
        self.editor_panel.shutdown()
        self.engine_scanner.shutdown()
        clear_recovery_snapshot()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
