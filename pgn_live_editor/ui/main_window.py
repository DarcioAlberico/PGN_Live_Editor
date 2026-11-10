import hashlib
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

import chess
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
    QTabWidget,
    QWidget,
)

from ..core.diagram_review import upsert_record
from ..core.issues import SUSPECT_BLUNDER
from ..core.parser import iter_moves
from ..core.project_diff import compare_projects, format_project_diff
from ..core.quality_report import format_quality_report, validate_book
from ..core.recognition_record import build_recognition_records, reconcile_records
from ..core.review_queue import accept_identical, apply_batch_action, reject_rule
from ..core.substitutions import LAYER_PROJECT, SCOPE_TOKEN
from ..core.text_ops import (
    OperationResult,
    TextEdit,
    insert_comment,
    move_variation,
    promote_variation,
    reanchor_variation,
    set_move_annotations,
)
from ..services.clipboard_service import COPY_FORMATS
from ..services.diagnostics import build_diagnostic_report
from ..services.diagram_generator import render_diagram_svg, write_png, write_svg
from ..services.diagram_ocr_worker import AsyncDiagramOcr
from ..services.dictionary_service import DictionaryService
from ..services.durable_io import write_durable_text
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
    build_html,
    build_normalized_text,
    build_pgn,
    build_structural_json,
    export_audit_text,
    game_file_name,
    validate_pgn_headers,
    write_pgn,
)
from ..services.full_pdf_recognition_worker import AsyncFullPdfRecognition
from ..services.parse_worker import SegmentAnalysis, build_games_for_export
from ..services.preferences_service import LOCALE_NAME, Layout, load_preferences, save_preferences
from ..services.project_service import (
    PROJECT_SUFFIX,
    Project,
    import_text_file,
    load_project,
    load_recent_files,
    remember_recent_file,
    save_project,
)
from ..services.recovery import RecoverySession, discard, orphan_snapshots, restore_project
from .book_import_dialog import BookImportDialog
from .chess_panel import ChessPanel
from .cleanup_dialog import CleanupDialog
from .comment_editor_dialog import CommentEditorDialog
from .diagram_queue_dialog import DiagramQueueDialog
from .diagram_review_dialog import DiagramReviewDialog
from .dictionary_dialog import DictionaryDialog
from .editor_panel import EditorPanel
from .games_panel import GamesPanel
from .header_dialog import HeaderDialog
from .issues_panel import IssuesPanel
from .legal_move_dialog import LegalMoveDialog
from .pdf_panel import PdfPanel
from .preferences_dialog import PreferencesDialog
from .qt_helpers import single_shot
from .review_panel import ReviewPanel
from .search_dialog import SearchDialog
from .theme import apply_theme

logger = logging.getLogger(__name__)

AUTOSAVE_INTERVAL_MS = 30_000
AUTOSAVE_CHAR_THRESHOLD = 200


def _point_in_bbox(x: float, y: float, bbox: object, padding: float = 8.0) -> bool:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    left, top, right, bottom = (float(value) for value in bbox)
    return left - padding <= x <= right + padding and top - padding <= y <= bottom + padding


def _same_pdf(saved: object, current: Path) -> bool:
    try:
        saved_path = Path(str(saved)).resolve()
        return saved_path == current.resolve() or saved_path.name.lower() == current.name.lower()
    except (OSError, ValueError):
        return False


def _diagram_header_block(record: dict[str, object]) -> str:
    """Formata uma posição de diagrama como partida PGN completa."""
    pdf_name = Path(str(record.get("pdf_path", ""))).name or "PDF"
    page = int(record.get("page", 0))
    diagram = record.get("diagram", "?")
    fen = str(record.get("fen", ""))
    confidence = float(record.get("min_confidence", 0.0))
    review = "1" if str(record.get("review", "pending")) == "pending" else "0"
    return "\n".join(
        [
            '[Event "PDF Diagram OCR"]',
            f'[Site "{pdf_name} - página {page}"]',
            '[Date "????.??.??"]',
            '[Round "?"]',
            '[White "?"]',
            '[Black "?"]',
            '[Result "*"]',
            '[SetUp "1"]',
            f'[FEN "{fen}"]',
            f'[DiagramOCR "{diagram}"]',
            f'[DiagramOCRConfidence "{confidence:.4f}"]',
            f'[DiagramOCRReview "{review}"]',
        ]
    )


def _full_pdf_document(pages: list[dict[str, object]]) -> tuple[str, list[dict[str, object]], str]:
    """O texto, os registros de diagrama e o resumo do reconhecimento do PDF inteiro."""
    blocks: list[str] = []
    records: list[dict[str, object]] = []
    pending = 0
    for page in pages:
        page_number = int(page.get("page", 0))
        blocks.append(f"{{ PDF página {page_number} }}")
        text = str(page.get("text", "")).strip()
        if text:
            blocks.append(text)
        elif str(page.get("text_method", "")) == "no-text-layer":
            blocks.append("{ OCR de texto necessário nesta página: não há camada de texto no PDF. }")
        elif str(page.get("text_method", "")) == "ocr-failed":
            blocks.append(f"{{ OCR de texto falhou nesta página: {page.get('text_error', '')} }}")
        elif str(page.get("text_method", "")) == "interrupted":
            blocks.append(f"{{ Reconhecimento interrompido aqui: {page.get('ocr_error', '')} }}")
            continue
        ocr_error = str(page.get("ocr_error", "")).strip()
        if ocr_error:
            blocks.append(f"{{ OCR de diagramas indisponível nesta página: {ocr_error} }}")
        for record in page.get("diagrams", []):
            if not isinstance(record, dict):
                continue
            records.append(record)
            needs_review = str(record.get("review", "pending")) == "pending"
            pending += int(needs_review)
            review = " revisão necessária" if needs_review else ""
            blocks.append(
                "{ Diagrama OCR "
                f"{record.get('diagram', '')}: confiança {float(record.get('min_confidence', 0)):.1%}"
                f"{review} | origem: página {page_number} }}"
            )
            blocks.append(_diagram_header_block(record))
        blocks.append("")
    failed_pages = sum(bool(str(page.get("ocr_error", "")).strip()) for page in pages)
    suffix = f", {pending} pendente(s) de revisão" if pending else ""
    failure_suffix = f", {failed_pages} página(s) com falha de OCR" if failed_pages else ""
    summary = f"PDF inteiro carregado: {len(pages)} página(s), {len(records)} diagrama(s){suffix}{failure_suffix}."
    return "\n".join(blocks).strip(), records, summary


class MainWindow(QMainWindow):
    # O inventário é auxiliar para auditoria e cria um dicionário por token.
    # Fazer isso automaticamente em um livro de milhões de caracteres trava a
    # interface e ainda consome centenas de MB; a análise PGN não depende dele.
    MAX_AUTO_RECOGNITION_CHARS = 500_000

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
        self.dictionary_service = DictionaryService(locale=self.preferences.effective_dictionary_locale)
        self.engine_scanner = EngineScanner(self)
        self.diagram_ocr = AsyncDiagramOcr(self)
        self.full_pdf_recognition = AsyncFullPdfRecognition(self)
        self._engine_findings: ScanFindings | None = None
        self._pending_engine_segment: tuple[str, int] = ("", 0)
        self._pending_diagram_click: tuple[int, float, float] | None = None
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
        self.review_panel = ReviewPanel()
        self.review_tabs = QTabWidget()
        self.review_tabs.addTab(self.issues_panel, "Alertas")
        self.review_tabs.addTab(self.review_panel, "Fila de reconhecimento")
        self.vertical_splitter.addWidget(self.review_tabs)
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
        self.editor_panel.status_message_signal.connect(self._report)
        self.editor_panel.notation_locale_changed_signal.connect(self._on_notation_locale_changed)
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
        self.review_panel.record_selected_signal.connect(self._focus_review_record)
        self.review_panel.accept_requested_signal.connect(self._accept_review_records)
        self.review_panel.reject_rule_requested_signal.connect(self._reject_review_rule)
        self.review_panel.comment_requested_signal.connect(self._comment_review_records)
        self.chess_panel.outline_panel.line_selected_signal.connect(self.editor_panel.focus_span)
        self.pdf_panel.text_requested_signal.connect(self._insert_from_pdf)
        self.pdf_panel.range_status_signal.connect(self._report)
        self.pdf_panel.book_text_requested_signal.connect(self._convert_from_pdf)
        self.pdf_panel.page_changed_signal.connect(self._on_pdf_page_changed)
        self.pdf_panel.document_opened_signal.connect(self._on_pdf_opened)
        self.pdf_panel.diagram_ocr_requested_signal.connect(self._read_diagrams_from_pdf)
        self.pdf_panel.full_pdf_recognition_requested_signal.connect(self._recognize_full_pdf)
        self.pdf_panel.full_pdf_recognition_cancel_requested_signal.connect(self._cancel_full_pdf_recognition)
        self.pdf_panel.diagram_ocr_cancel_requested_signal.connect(self._cancel_diagram_ocr)
        self.pdf_panel.diagram_review_queue_requested_signal.connect(self._review_diagram_queue)
        self.pdf_panel.diagram_clicked_signal.connect(self._on_diagram_clicked)
        self.diagram_ocr.finished_signal.connect(self._on_diagrams_read)
        self.full_pdf_recognition.finished_signal.connect(self._on_full_pdf_recognized)
        self.full_pdf_recognition.failed_signal.connect(self._on_full_pdf_recognition_failed)
        self.full_pdf_recognition.progress_signal.connect(self._on_full_pdf_progress)
        self.diagram_ocr.failed_signal.connect(self._on_diagram_ocr_failed)
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
        # O snapshot desta janela (R-102): um arquivo e uma trava so dela.
        self.recovery = RecoverySession()
        # Com dono: uma janela ja destruida nao pode abrir dialogo de
        # recuperacao. Ver `qt_helpers.single_shot`.
        single_shot(self, 0, self._offer_session_recovery)

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
        self._add_action(file_menu, "Exportar análise em .json...", None, self.export_structural_json)
        self._add_action(file_menu, "Exportar texto normalizado em .txt...", None, self.export_normalized_text)
        self._add_action(file_menu, "Exportar HTML navegável...", None, self.export_html)
        self._add_action(file_menu, "Exportar diagrama da posição atual...", "Ctrl+Alt+G", self._export_current_diagram)
        self._add_action(file_menu, "Comparar com outro projeto...", None, self._compare_with_project)
        self._add_action(file_menu, "Salvar comparação de projetos...", None, self._save_project_comparison)

        file_menu.addSeparator()
        file_menu.addSeparator()
        self._add_action(file_menu, "Preferencias...", "Ctrl+,", self.edit_preferences)
        file_menu.addSeparator()
        self._add_action(file_menu, "Sair", QKeySequence.StandardKey.Quit, self.close)

        book_menu = self.menuBar().addMenu("&Partidas")
        self._add_action(book_menu, "Próxima partida", "Ctrl+Shift+P", lambda: self._step_game(1))
        self._add_action(book_menu, "Partida anterior", "Ctrl+Shift+O", lambda: self._step_game(-1))
        book_menu.addSeparator()
        self._add_action(book_menu, "Cabeçalhos da partida...", "Ctrl+H", self.edit_headers)
        self._add_action(book_menu, "Marcar como concluida", "Ctrl+Shift+D", self.games_panel.toggle_current_completion)
        self._add_action(book_menu, "Analisar o livro inteiro", None, self._analyze_book)
        self._add_action(book_menu, "Relatório de qualidade", None, self._show_quality_report)
        self._add_action(book_menu, "Salvar relatório de qualidade...", None, self._save_quality_report)
        book_menu.addSeparator()
        self._add_action(book_menu, "Usar a posição do tabuleiro como inicial", None, self._set_start_fen_from_board)
        self._add_action(book_menu, "Converter marcadores de diagrama", None, self._normalize_diagrams)
        book_menu.addSeparator()
        self._add_action(book_menu, "Mostrar/ocultar lista de partidas", "F10", self._toggle_games_panel)
        book_menu.addSeparator()
        self._add_action(book_menu, "Abrir o PDF do livro...", None, self.pdf_panel.choose_pdf)
        self._add_action(book_menu, "Ler todos os diagramas da página", "Ctrl+Alt+O", self._read_diagrams_from_pdf)
        self._add_action(book_menu, "Reconhecer PDF inteiro", "Ctrl+Alt+Shift+O", self._recognize_full_pdf)
        self._add_action(book_menu, "Revisar pendências de diagramas", None, self._review_diagram_queue)
        self._add_action(book_menu, "Mostrar/ocultar o PDF", "F11", self._toggle_pdf_panel)

        structure_menu = self.menuBar().addMenu("&Estrutura")
        self._add_action(structure_menu, "Envolver em comentário", "Ctrl+K", self._wrap_comment)
        self._add_action(structure_menu, "Envolver em variante", "Ctrl+9", self._wrap_variation)
        self._add_action(structure_menu, "Desenvolver (tirar delimitador)", "Ctrl+Shift+K", self._unwrap)
        self._add_action(structure_menu, "Parágrafo vira comentário", "Ctrl+Shift+C", self._paragraph_to_comment)
        self._add_action(structure_menu, "Editar comentário do lance atual", "Ctrl+Alt+E", self._edit_current_comment)
        structure_menu.addSeparator()
        self._add_action(
            structure_menu,
            "Colar comentário antes do lance",
            "Ctrl+Alt+A",
            lambda: self._paste_comment(before=True),
        )
        self._add_action(
            structure_menu,
            "Colar comentário depois do lance",
            "Ctrl+Alt+D",
            lambda: self._paste_comment(before=False),
        )
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Dispor em árvore de variantes", "Ctrl+T", self._indent_variations)
        self._add_action(structure_menu, "Voltar a texto corrido", "Ctrl+Shift+T", self._flatten_variations)
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Renumerar documento", "Ctrl+R", self._renumber)
        self._add_action(structure_menu, "Promover variante", "Ctrl+Shift+Up", self._promote_variation)
        self._add_action(structure_menu, "Rebaixar a linha principal", "Ctrl+Shift+Down", self._demote_mainline)
        self._add_action(structure_menu, "Subir a variante", "Alt+Up", lambda: self._move_variation(-1))
        self._add_action(structure_menu, "Descer a variante", "Alt+Down", lambda: self._move_variation(1))
        self._add_action(structure_menu, "Reancorar a variante aqui", "Ctrl+Shift+A", self._reanchor_variation)
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Converter texto de livro...", "Ctrl+Shift+L", self._convert_book_text)
        self._add_action(structure_menu, "Limpar texto colado...", "Ctrl+Shift+V", self._clean_paste)
        self._add_action(structure_menu, "Conferir delimitadores", "Ctrl+Shift+B", self._check_balance)
        self._add_action(structure_menu, "Buscar no documento...", "Ctrl+Shift+F", self._search_document)
        structure_menu.addSeparator()
        self._add_action(structure_menu, "Árvore do PGN no editor", "F5", self._toggle_pgn_tree)
        self._add_action(structure_menu, "Árvore de variantes (ao lado do tabuleiro)", "F7", self._show_outline)
        self._add_action(structure_menu, "Copiar a árvore indentada", None, self._copy_outline)
        self._add_action(structure_menu, "Mostrar/ocultar o rodapé de símbolos", "F6", self._toggle_nag_bar)

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
        self._add_action(move_menu, "Próximo lance", "Ctrl+Right", self.chess_panel.go_next)
        move_menu.addSeparator()
        self._add_action(move_menu, "Inverter tabuleiro", "Ctrl+F", self.chess_panel.flip_board)
        move_menu.addSeparator()
        self._add_action(move_menu, "Primeiro lance", "Ctrl+Home", self.chess_panel.go_first)
        self._add_action(move_menu, "Último lance", "Ctrl+End", self.chess_panel.go_last)
        self._add_action(move_menu, "Sugerir lance legal", "Ctrl+Space", self._suggest_legal_move)
        self._add_action(move_menu, "Paleta de símbolos", "Ctrl+D", self.editor_panel.open_symbol_palette)

        review_menu = self.menuBar().addMenu("&Revisão")
        self._add_action(review_menu, "Próximo alerta", "F8", lambda: self.issues_panel.go_to_next_issue(1))
        self._add_action(review_menu, "Alerta anterior", "Shift+F8", lambda: self.issues_panel.go_to_next_issue(-1))
        self._add_action(review_menu, "Aceitar correção", "F4", self.issues_panel.accept_selected)
        review_menu.addSeparator()
        self._add_action(review_menu, "Conferir a partida com o motor", "Ctrl+E", self._scan_with_engine)
        self._add_action(review_menu, "Motor de xadrez...", None, self._configure_engine)
        help_menu = self.menuBar().addMenu("&Ajuda")
        self._add_action(help_menu, "Copiar diagnóstico", None, self._copy_diagnostic)
        self._add_action(help_menu, "Salvar diagnóstico...", None, self._save_diagnostic)
        self._add_action(review_menu, "Fila de revisão de diagramas...", "Ctrl+Alt+R", self._review_diagram_queue)
        review_menu.addSeparator()
        self._add_action(review_menu, "Dicionário de OCR...", "Ctrl+Shift+I", self.edit_dictionary)
        review_menu.addSeparator()
        self._add_action(review_menu, "Mostrar/ocultar alertas", "F9", self._toggle_issues_panel)
        self._add_action(review_menu, "Fila de reconhecimento", "F10", self._show_review_queue)
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
            if path.is_file():
                action = QAction(path.name, self)
                action.setStatusTip(str(path))
                action.triggered.connect(lambda _checked=False, target=path: self.open_path(target))
            else:
                # Cinza e com o motivo, mas na lista (R-108): o arquivo pode
                # voltar quando a pasta de rede ou o OneDrive voltarem.
                action = QAction(f"{path.name} (não encontrado)", self)
                action.setStatusTip(f"{path} -- não encontrado agora; pasta de rede ou sincronização?")
                action.setEnabled(False)
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
            "Trabalho não salvo",
            "Ha alterações que ainda não foram salvas no projeto.\n\nDeseja salvar antes de continuar?",
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
        self._switch_to(self._blank_project())
        self.recovery.clear()
        self.statusBar().showMessage("Projeto novo.", 4000)

    def _blank_project(self, title: str = "") -> Project:
        """Projeto vazio. So os idiomas continuam: quem abre um projeto novo no meio
        de um livro espanhol esta, quase sempre, no mesmo livro."""
        project = Project()
        project.meta.title = title
        project.meta.notation_locale = self.editor_panel.locale
        project.meta.dictionary_locale = self.dictionary_service.locale
        return project

    def open_path(self, path: Path) -> None:
        """Abrir um projeto por caminho (recentes, linha de comando): pergunta antes (R-105)."""
        if self._confirm_discard():
            self._load_project_path(path)

    def import_path(self, path: Path) -> None:
        """Importar um PGN/texto por caminho: pergunta antes (R-105)."""
        if self._confirm_discard():
            self._import_path(path)

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
            QMessageBox.critical(self, "Abrir projeto", f"Não foi possível abrir o projeto.\n\n{exc}")
            return

        self._switch_to(project, cursor_position=project.state.cursor_position)
        remember_recent_file(path)
        self._rebuild_recent_menu()
        self._update_window_title()
        self.statusBar().showMessage(f"Projeto aberto: {path.name}", 5000)
        if project.load_warnings:
            self._show_load_warnings(path, project.load_warnings)

    def _show_load_warnings(self, path: Path, warnings) -> None:
        """Parte do projeto veio estragada e voltou ao padrao: o usuario precisa saber (R-103).

        Nao modal: o texto ja esta aberto e e o que importa; o aviso so conta o
        que nao veio junto.
        """
        for warning in warnings:
            logger.warning("%s: %s", path.name, warning)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Projeto aberto com avisos")
        box.setText(
            f"{path.name} abriu com o texto completo, mas {len(warnings)} configuração(ões) "
            "estavam ilegíveis e voltaram ao padrão. Salvar grava o projeto já corrigido."
        )
        box.setDetailedText("\n".join(str(warning) for warning in warnings))
        box.setModal(False)
        box.show()
        self._load_warnings_box = box

    def _switch_to(self, project: Project, *, text: str | None = None, cursor_position: int = 0) -> None:
        """O unico caminho para trocar de projeto (R-104, SPEC v3 §10.1).

        Novo, abrir, recentes, importar e recuperar passam por aqui -- e o
        portao de descarte (`_confirm_discard`) fica com quem chama. Nada da
        sessao anterior sobrevive: trabalho em segundo plano, achados do motor,
        dicionario e tokens ignorados, PDF e o cache de OCR dele. O texto novo
        entra **depois** da configuracao nova e com uma analise so; antes, a
        reconfiguracao reanalisava o texto antigo e reconciliava as decisoes do
        projeto novo contra ele (U-01).

        `text` e o que vai para o editor quando nao e `project.raw_text` -- o
        texto importado, que ainda nao esta salvo em projeto nenhum.
        """
        self._cancel_background_work()
        self._engine_findings = None
        self.project = project
        self.dictionary_service.set_locale(project.meta.dictionary_locale)
        self.dictionary_service.set_project(project.dictionary)
        self.editor_panel.configure_recognition(
            locale=project.meta.notation_locale,
            dictionary=self.dictionary_service.merged(),
            ignored_tokens=project.state.ignored_issue_tokens,
            reparse=False,
        )
        self.games_panel.set_completed(project.state.completed_games)
        self._restore_pdf(project)
        self.editor_panel.load_document_text(project.raw_text if text is None else text, cursor_position)
        self._last_autosaved_text = project.raw_text
        self._chars_since_autosave = 0
        self._state_dirty = False
        self._update_window_title()

    def _cancel_background_work(self) -> None:
        """O que estava rodando para o projeto anterior nao tem onde cair no novo."""
        self.diagram_ocr.cancel()
        self.full_pdf_recognition.cancel()

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
        if project.state.pdf_sha256:
            try:
                current_hash = hashlib.sha256(Path(target).read_bytes()).hexdigest()
            except OSError:
                current_hash = ""
            if current_hash and current_hash != project.state.pdf_sha256:
                self._report("Aviso: o PDF associado foi alterado desde o último salvamento.")
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
        # O idioma gravado e o que esta analisando o texto agora, e nao o padrao
        # com que o projeto nasceu -- senao reabrir o `.plez` trocava o idioma.
        self.project.meta.notation_locale = self.editor_panel.locale
        self.project.state.cursor_position = self.editor_panel.cursor_position()

        try:
            saved_path = save_project(self.project, path)
        except Exception as exc:  # noqa: BLE001 - segunda linha de defesa (R-107)
            # OSError e o esperado (disco, permissao). Qualquer outra coisa e um
            # defeito -- mas o texto continua no editor e o programa, aberto: o
            # usuario pode tentar "Salvar como" noutro lugar.
            logger.exception("Falha ao salvar %s", path)
            QMessageBox.critical(self, "Salvar projeto", f"Não foi possível salvar o projeto.\n\n{exc}")
            return False

        self._last_autosaved_text = self.project.raw_text
        self._chars_since_autosave = 0
        self._state_dirty = False
        self.editor_panel.text_edit.document().setModified(False)
        self.recovery.clear()
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
        if file_path:
            self._import_path(Path(file_path))

    def _import_path(self, path: Path) -> None:
        try:
            text = import_text_file(path)
        except OSError as exc:
            QMessageBox.critical(self, "Importar", f"Não foi possível ler o arquivo.\n\n{exc}")
            return
        # O texto importado ainda nao esta em projeto nenhum: `raw_text` vazio
        # deixa a janela suja ate o primeiro salvar.
        self._switch_to(self._blank_project(title=path.stem), text=text)
        self.statusBar().showMessage(f"Importado: {path.name} ({len(text)} caracteres)", 5000)

    # ------------------------------------------------------------------
    # Revisao de alertas
    # ------------------------------------------------------------------

    def _set_recognition_action(self, issue, action: str, decision: str = "") -> None:
        """Persist a human decision for the exact token, when it is known."""
        start, end = int(issue.raw_start), int(issue.raw_end)
        changed = False
        for record in self.project.state.recognition_records:
            source = record.get("source", {})
            if not isinstance(source, dict):
                continue
            if source.get("start") == start and source.get("end") == end and record.get("raw_text") == issue.raw_text:
                record["action"] = action
                record["decided_at"] = time.time()
                if decision:
                    record["decision"] = decision
                changed = True
                break
        if changed:
            self._state_dirty = True
            self.review_panel.set_records(self.project.state.recognition_records)

    def _show_review_queue(self):
        self.review_tabs.setCurrentWidget(self.review_panel)
        self.review_tabs.setVisible(True)
        self.review_panel.table.setFocus()

    def _focus_review_record(self, record: dict):
        source = record.get("source", {})
        if not isinstance(source, dict):
            return
        start = int(source.get("start", -1))
        end = int(source.get("end", start))
        if start >= 0 and end > start:
            self.editor_panel.focus_span(start, end)
        page = source.get("page")
        pdf_path = source.get("pdf_path") or self.project.state.pdf_path
        if page is not None:
            if not self.pdf_panel.has_document() and pdf_path:
                problem = self.pdf_panel.open_pdf(Path(str(pdf_path)), max(0, int(page) - 1))
                if problem:
                    self._report(problem)
            elif self.pdf_panel.has_document():
                self.pdf_panel.go_to_page(max(0, int(page) - 1))
            if self.pdf_panel.has_document():
                self.pdf_panel.setVisible(True)

    @staticmethod
    def _review_ids(records: list[dict]) -> set[str]:
        return {str(record.get("source_id", record.get("id", ""))) for record in records}

    def _accept_review_records(self, records: list[dict]):
        ids = self._review_ids(records)
        if not ids or accept_identical(self.project.state.recognition_records, ids) == 0:
            self._report("Aceitação em lote cancelada: as propostas não são idênticas.")
            return
        edits = []
        for record in records:
            source = record.get("source", {})
            replacement = str(record.get("decision") or record.get("normalized_text", ""))
            if isinstance(source, dict) and int(source.get("end", 0)) > int(source.get("start", 0)):
                edits.append(TextEdit(int(source["start"]), int(source["end"]), replacement))
        self.editor_panel.apply_operation(OperationResult(edits, "Correções idênticas aceitas."))
        self._state_dirty = True
        self.review_panel.set_records(self.project.state.recognition_records)

    def _reject_review_rule(self, rule_id: str):
        changed = reject_rule(self.project.state.recognition_records, rule_id)
        if changed:
            self._state_dirty = True
            self.review_panel.set_records(self.project.state.recognition_records)
            self._report(f"{changed} proposta(s) da regra '{rule_id}' rejeitada(s).")

    def _comment_review_records(self, records: list[dict]):
        ids = self._review_ids(records)
        changed = apply_batch_action(self.project.state.recognition_records, ids, "commented")
        edits = []
        for record in records:
            source = record.get("source", {})
            if isinstance(source, dict):
                start, end = int(source.get("start", -1)), int(source.get("end", -1))
                if 0 <= start < end:
                    text = self.editor_panel.document_text()[start:end]
                    edits.append(TextEdit(start, end, "{" + text + "}"))
        if edits:
            self.editor_panel.apply_operation(OperationResult(edits, "Itens transformados em comentário."))
        if changed:
            self._state_dirty = True
            self.review_panel.set_records(self.project.state.recognition_records)

    def _on_parsed(self, ast):
        self.issues_panel.set_issues(ast.issues + self._live_engine_issues())
        self.chess_panel.start_fen = self.editor_panel.current_start_fen

    # ------------------------------------------------------------------
    # Escala de livro (fase 4)
    # ------------------------------------------------------------------

    def _on_document_analyzed(self, analysis):
        if analysis.raw_text != self.editor_panel.document_text():
            # Analise de um texto que ja saiu do editor (troca de projeto no meio
            # dela): reconciliar contra ele rebaixava decisoes do projeto novo
            # (U-01). A analise do texto atual vem logo atras.
            return
        if len(analysis.raw_text) > self.MAX_AUTO_RECOGNITION_CHARS:
            # Registros antigos não podem ser associados silenciosamente ao
            # texto novo. Para livros grandes, a fonte de verdade continua
            # sendo o PGN e o inventário fica vazio até existir uma ação
            # explícita para gerá-lo.
            self.project.state.recognition_records = []
        else:
            fresh_records = build_recognition_records(analysis.raw_text)
            self.project.state.recognition_records = reconcile_records(
                fresh_records, self.project.state.recognition_records
            )
            self._enrich_recognition_records(self.project.state.recognition_records, analysis)
        self.review_panel.set_records(self.project.state.recognition_records)
        self.games_panel.set_completed(self.project.state.completed_games)
        self.games_panel.set_summaries(analysis.summaries(), analysis.active_index)
        # A lista so aparece quando ha livro: uma partida sozinha nao precisa dela.
        self.games_panel.setVisible(analysis.game_count > 1)
        self._update_book_status(analysis)
        self._update_touch_status()

    def _enrich_recognition_records(self, records: list[dict], analysis) -> None:
        """Attach stable book/PDF provenance without putting it in token IDs."""
        for record in records:
            source = record.get("source", {})
            if not isinstance(source, dict):
                continue
            start = int(source.get("start", -1))
            segment = analysis.segment_at(start) if start >= 0 else None
            if segment is not None:
                record["game"] = segment.index
            if self.project.state.pdf_path:
                source["page"] = self.project.state.pdf_page + 1
                source["pdf_path"] = self.project.state.pdf_path

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
        try:
            self.project.state.pdf_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            self.project.state.pdf_sha256 = ""
        self._state_dirty = True
        self.pdf_panel.setVisible(True)
        self._update_window_title()

    def _read_diagrams_from_pdf(self):
        """Lê os diagramas da página atual e insere posições FEN no documento.

        O reconhecimento é deliberadamente explícito nesta primeira integração:
        não altera o texto sem a solicitação do usuário. O adapter mantém o
        OCR pesado opcional; quando indisponível, a mensagem explica como ativá-lo.
        """
        pdf_path = self.pdf_panel.path
        if pdf_path is None:
            self._report("Abra um PDF antes de ler os diagramas.")
            return

        self.pdf_panel.btn_ocr.setEnabled(False)
        self.pdf_panel.btn_cancel_ocr.setVisible(True)
        self._report("Lendo diagramas da página em segundo plano…")
        self.diagram_ocr.request(pdf_path, self.pdf_panel.current_page())

    def _recognize_full_pdf(self):
        pdf_path = self.pdf_panel.path
        if pdf_path is None:
            self._report("Abra um PDF antes de reconhecer o documento inteiro.")
            return
        self.pdf_panel.btn_full_recognition.setEnabled(False)
        self.pdf_panel.btn_ocr.setEnabled(False)
        self.pdf_panel.btn_cancel_full_recognition.setVisible(True)
        self.pdf_panel.set_full_progress(0, 1, 0)
        self._report("Reconhecendo texto e diagramas do PDF inteiro em segundo plano…")
        start_page, end_page = self.pdf_panel.recognition_range()
        self.full_pdf_recognition.request(pdf_path, start_page=start_page, end_page=end_page)

    def _cancel_full_pdf_recognition(self):
        self.full_pdf_recognition.cancel()
        self.pdf_panel.btn_full_recognition.setEnabled(self.pdf_panel.has_document())
        self.pdf_panel.btn_ocr.setEnabled(self.pdf_panel.has_document())
        self.pdf_panel.btn_cancel_full_recognition.setVisible(False)
        self.pdf_panel.clear_full_progress()
        self._report("Reconhecimento integral cancelado. O resultado parcial não foi aplicado.")

    def _on_full_pdf_recognized(self, pages: list[dict[str, object]]):
        """Resultado do PDF inteiro: pergunta o destino antes de mexer no texto (R-106).

        Antes trocava o documento por `setPlainText`, sem perguntar e apagando o
        Ctrl+Z, mesmo com o usuario editando enquanto o reconhecimento corria
        (U-04). Os registros de diagrama vao para o projeto que receber o texto.
        """
        try:
            text, records, summary = _full_pdf_document(pages)
            pdf_path = self.pdf_panel.path
            choice = "replace"
            if self.editor_panel.document_text().strip():
                choice = self._ask_full_pdf_destination(len(pages), len(records))
            if choice == "cancel":
                self._report("Resultado do PDF inteiro descartado; o texto não foi alterado.")
                return
            if choice == "new":
                if not self._confirm_discard():
                    return
                project = self._blank_project(title=pdf_path.stem if pdf_path else "")
                self._switch_to(project, text=text)
                if pdf_path is not None:
                    self.pdf_panel.open_pdf(pdf_path)
            else:
                self.editor_panel.replace_all_text(text)
            for record in records:
                upsert_record(self.project.state.diagram_records, record)
            if pdf_path is not None:
                self.project.state.pdf_path = str(pdf_path)
            self._state_dirty = True
            self._update_window_title()
            self._report(summary)
        finally:
            self.pdf_panel.btn_full_recognition.setEnabled(self.pdf_panel.has_document())
            self.pdf_panel.btn_ocr.setEnabled(self.pdf_panel.has_document())
            self.pdf_panel.btn_cancel_full_recognition.setVisible(False)
            self.pdf_panel.clear_full_progress()

    def _ask_full_pdf_destination(self, page_count: int, diagram_count: int) -> str:
        """'replace' (desfazivel), 'new' (projeto novo) ou 'cancel'."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Reconhecimento do PDF concluído")
        box.setText(
            f"O reconhecimento terminou: {page_count} página(s), {diagram_count} diagrama(s).\n\n"
            "O editor já tem texto. Onde colocar o resultado?"
        )
        replace = box.addButton("Substituir o texto (Ctrl+Z desfaz)", QMessageBox.ButtonRole.AcceptRole)
        new = box.addButton("Abrir num projeto novo", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Descartar o resultado", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(new)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace:
            return "replace"
        if clicked is new:
            return "new"
        return "cancel"

    def _on_full_pdf_progress(self, page: int, total: int, diagrams: int):
        self.pdf_panel.set_full_progress(page, total, diagrams)
        self.statusBar().showMessage(
            f"Reconhecendo PDF: página {page}/{total} ({page * 100 // max(1, total)}%) — "
            f"{diagrams} diagrama(s) encontrados até agora…"
        )

    def _on_full_pdf_recognition_failed(self, message: str):
        self.pdf_panel.btn_full_recognition.setEnabled(self.pdf_panel.has_document())
        self.pdf_panel.btn_ocr.setEnabled(self.pdf_panel.has_document())
        self.pdf_panel.btn_cancel_full_recognition.setVisible(False)
        self.pdf_panel.clear_full_progress()
        QMessageBox.warning(self, "Reconhecimento integral indisponível", message)

    def _on_diagram_clicked(self, page: int, x: float, y: float):
        """Abre no tabuleiro o diagrama sob o duplo clique."""
        path = self.pdf_panel.path
        if path is None:
            return
        page_records = [
            record
            for record in self.project.state.diagram_records
            if _same_pdf(record.get("pdf_path", ""), path) and int(record.get("page", 0)) == page + 1
        ]
        matching = [record for record in page_records if _point_in_bbox(x, y, record.get("bbox_pdf"))]
        if matching:
            self._open_diagram_record(matching[0])
            return
        # Em páginas com um único diagrama, o reconhecimento já persistido é
        # inequívoco. Isso também cobre pequenos deslocamentos do viewport Qt.
        if len(page_records) == 1:
            self._open_diagram_record(page_records[0])
            return
        self._pending_diagram_click = (page, x, y)
        self._read_diagrams_from_pdf()

    def _open_diagram_record(self, record: dict[str, object]):
        fen = str(record.get("fen", ""))
        if not fen:
            return
        if str(record.get("review", "pending")) == "pending" or not bool(record.get("is_legal", True)):
            dialog = DiagramReviewDialog(
                fen,
                uncertain_squares=tuple(record.get("uncertain_squares", [])),
                title=f"Revisar diagrama {record.get('diagram', '')}",
                parent=self,
            )
            if dialog.exec() != DiagramReviewDialog.DialogCode.Accepted:
                return
            placement, fen, legal, problems = dialog.result_data()
            record.update(
                placement=placement,
                fen=fen,
                side_to_move=fen.split()[1],
                is_legal=legal,
                problems=list(problems),
                uncertain_squares=[],
                review="accepted" if legal else "pending",
            )
            self._state_dirty = True
        # Grava a posição no cabeçalho PGN da partida sob o foco. Assim o
        # parser não perde a posição na próxima análise ou edição do texto.
        self._report(self.editor_panel.action_set_start_fen(fen))
        self.chess_panel.show_fen_position(fen)
        self._report(f"Diagrama {record.get('diagram', '')} aberto no tabuleiro.")

    def _on_diagrams_read(self, payload):
        page_index, results = payload
        try:
            if not results:
                self._pending_diagram_click = None
                self._report(f"Nenhum diagrama reconhecido na página {page_index + 1}.")
                return

            if self._pending_diagram_click is not None:
                pending_page, x, y = self._pending_diagram_click
                self._pending_diagram_click = None
                if pending_page == page_index:
                    clicked = next((item for item in results if _point_in_bbox(x, y, item.bbox_pdf)), None)
                    if clicked is not None:
                        record = clicked.to_record(pdf_path=str(self.pdf_panel.path), page=page_index + 1)
                        upsert_record(self.project.state.diagram_records, record)
                        self._open_diagram_record(record)
                        return
                self._pending_diagram_click = None
                self._report("Nenhum diagrama foi detectado exatamente no ponto clicado.")
                return

            blocks = []
            for result in results:
                if result.needs_review:
                    dialog = DiagramReviewDialog(
                        result.fen,
                        uncertain_squares=result.uncertain_squares,
                        title=f"Revisar diagrama {result.index}",
                        parent=self,
                    )
                    if dialog.exec() == DiagramReviewDialog.DialogCode.Accepted:
                        placement, fen, is_legal, problems = dialog.result_data()
                        result = replace(
                            result,
                            placement=placement,
                            fen=fen,
                            side_to_move=fen.split()[1],
                            is_legal=is_legal,
                            problems=problems,
                            uncertain_squares=(),
                            orientation_ambiguous=False,
                        )
                upsert_record(
                    self.project.state.diagram_records,
                    result.to_record(pdf_path=str(self.pdf_panel.path), page=page_index + 1),
                )
                review = " revisão necessária" if result.needs_review else ""
                blocks.append(
                    "{ Diagrama OCR "
                    f"{result.index}: confiança {result.min_confidence:.1%}{review} "
                    f"| problemas: {', '.join(result.problems) or 'nenhum'} "
                    f"| origem: página {page_index + 1} }}\n"
                    + _diagram_header_block(result.to_record(pdf_path=str(self.pdf_panel.path), page=page_index + 1))
                    + "\n"
                )
            self.editor_panel.insert_text_at_cursor("\n".join(blocks))
            self._state_dirty = True
            pendentes = sum(result.needs_review for result in results)
            suffix = f", {pendentes} pendente(s) de revisão" if pendentes else ""
            self._report(f"{len(results)} diagrama(s) convertido(s) em FEN{suffix}.")
        finally:
            self.pdf_panel.btn_ocr.setEnabled(self.pdf_panel.has_document())
            self.pdf_panel.btn_cancel_ocr.setVisible(False)

    def _cancel_diagram_ocr(self):
        self.diagram_ocr.cancel()
        self.pdf_panel.btn_ocr.setEnabled(self.pdf_panel.has_document())
        self.pdf_panel.btn_cancel_ocr.setVisible(False)
        self._report("Leitura de diagramas cancelada.")

    def _on_diagram_ocr_failed(self, message: str):
        self.pdf_panel.btn_ocr.setEnabled(self.pdf_panel.has_document())
        self.pdf_panel.btn_cancel_ocr.setVisible(False)
        QMessageBox.warning(self, "OCR de diagramas indisponível", message)

    def _review_diagram_queue(self):
        dialog = DiagramQueueDialog(self.project.state.diagram_records, self)
        dialog.page_requested_signal.connect(self.pdf_panel.go_to_page)
        dialog.fen_requested_signal.connect(self._apply_diagram_fen)
        dialog.exec()
        self._state_dirty = True
        self._report("Fila de revisão atualizada.")

    def _apply_diagram_fen(self, fen: str):
        """Aplica uma FEN da fila à partida sob o cursor, sem alterar a origem."""
        self._report(self.editor_panel.action_set_start_fen(fen))

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

    def _show_quality_report(self):
        analyses = self._book_analyses()
        findings = validate_book((index, analysis.ast) for index, analysis in enumerate(analyses))
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Qualidade do livro")
        dialog.setText(format_quality_report(findings))
        dialog.setDetailedText("\n".join(f"{item.code}: {item.message}" for item in findings))
        dialog.exec()

    def _save_quality_report(self):
        analyses = self._book_analyses()
        findings = validate_book((index, analysis.ast) for index, analysis in enumerate(analyses))
        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Salvar relatório de qualidade",
            str(Path.cwd() / f"{self.project.display_name}_qualidade.txt"),
            "Relatório de texto (*.txt);;Todos os arquivos (*)",
        )
        if not file_path:
            return
        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".txt")
        try:
            write_durable_text(output_path, format_quality_report(findings) + "\n")
        except OSError as exc:
            QMessageBox.critical(self, "Relatório de qualidade", f"Não foi possível gravar o arquivo.\n\n{exc}")
            return
        self.statusBar().showMessage(f"Relatório de qualidade gravado em {output_path}", 8000)

    def _build_project_comparison(self):
        file_path, _filter = QFileDialog.getOpenFileName(
            self,
            "Comparar com projeto",
            str(Path.cwd()),
            f"Projetos do PGN Live Editor (*{PROJECT_SUFFIX});;Todos os arquivos (*)",
        )
        if not file_path:
            return
        try:
            other = load_project(Path(file_path))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Comparar projetos", f"Não foi possível abrir o projeto.\n\n{exc}")
            return None
        current = Project(
            raw_text=self.editor_panel.document_text(),
            meta=self.project.meta,
            header_template=self.project.header_template,
            dictionary=self.project.dictionary,
            state=self.project.state,
        )
        return compare_projects(other, current)

    def _compare_with_project(self):
        diff = self._build_project_comparison()
        if diff is not None:
            QMessageBox.information(self, "Comparar projetos", format_project_diff(diff))

    def _save_project_comparison(self):
        diff = self._build_project_comparison()
        if diff is None:
            return
        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Salvar comparação de projetos",
            str(Path.cwd() / f"{self.project.display_name}_comparacao.txt"),
            "Relatório de texto (*.txt);;Todos os arquivos (*)",
        )
        if not file_path:
            return
        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".txt")
        try:
            write_durable_text(output_path, format_project_diff(diff) + "\n")
        except OSError as exc:
            QMessageBox.critical(self, "Comparar projetos", f"Não foi possível gravar o arquivo.\n\n{exc}")
            return
        self.statusBar().showMessage(f"Comparação gravada em {output_path}", 8000)

    def _diagnostic_text(self) -> str:
        return build_diagnostic_report(project_path=self.project.path, pdf_path=self.pdf_panel.path)

    def _copy_diagnostic(self):
        QApplication.clipboard().setText(self._diagnostic_text())
        self._report("Diagnóstico copiado sem incluir o texto do documento.")

    def _save_diagnostic(self):
        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Salvar diagnóstico",
            str(Path.cwd() / "pgn-live-editor-diagnostic.txt"),
            "Arquivo de texto (*.txt);;Todos os arquivos (*)",
        )
        if not file_path:
            return
        try:
            write_durable_text(Path(file_path), self._diagnostic_text())
        except OSError as exc:
            QMessageBox.critical(self, "Diagnóstico", f"Não foi possível salvar o diagnóstico.\n\n{exc}")
            return
        self._report(f"Diagnóstico salvo em {Path(file_path).name}.")

    def _wrap_comment(self):
        self._report(self.editor_panel.action_wrap_comment())

    def _wrap_variation(self):
        self._report(self.editor_panel.action_wrap_variation())

    def _unwrap(self):
        self._report(self.editor_panel.action_unwrap())

    def _paragraph_to_comment(self):
        self._report(self.editor_panel.action_paragraph_to_comment())

    def _paste_comment(self, before: bool):
        """A prosa da area de transferencia vira comentario do lance escolhido.

        O lance e o da arvore do PGN, quando ha um selecionado ali, e o do
        cursor quando nao ha -- os dois andam juntos, porque a arvore segue o
        cursor e clicar nela move o cursor.
        """
        self._report(self.editor_panel.action_paste_comment(before, self.editor_panel.pgn_tree.selected_node_id()))

    def _edit_current_comment(self):
        """Edita o comentário do lance selecionado, preservando NAGs e setas."""
        entry = self.chess_panel.current_entry()
        if entry is None:
            self._report("Selecione um lance no tabuleiro ou na árvore primeiro.")
            return
        from ..core.annotations import strip_annotations

        current = " ".join(entry.node.comments_after)
        dialog = CommentEditorDialog(strip_annotations(current), self.chess_panel.current_annotations(), self)
        if dialog.exec() != CommentEditorDialog.DialogCode.Accepted:
            return
        result = insert_comment(self.editor_panel.document_text(), entry.node, dialog.comment_text(), before=False)
        self._report(self.editor_panel.apply_operation(result) or result.message)

    def _export_current_diagram(self):
        board = self.chess_panel.board.copy(stack=False)
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Exportar diagrama",
            str(Path.cwd() / "diagrama.svg"),
            "SVG (*.svg);;PNG (*.png)",
        )
        if not file_path:
            return
        target = Path(file_path)
        if not target.suffix:
            target = target.with_suffix(".png" if "PNG" in selected_filter else ".svg")
        svg = render_diagram_svg(
            board,
            arrows=self.chess_panel.current_annotations().arrows,
            squares=self.chess_panel.current_annotations().squares,
            orientation=chess.BLACK if self.chess_panel.board_flipped else chess.WHITE,
            lastmove=self.chess_panel.current_last_move,
        )
        try:
            if target.suffix.lower() == ".png":
                write_png(target, svg)
            else:
                write_svg(target, svg)
        except (OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "Exportar diagrama", f"Não foi possível exportar o diagrama.\n\n{exc}")
            return
        self._report(f"Diagrama exportado para {target.name}.")

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
            self._report("Este lance já esta na linha principal.")
            return

        result = promote_variation(
            self.editor_panel.document_text(), self.editor_panel.current_ast, owner, variation_index
        )
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

        result = promote_variation(self.editor_panel.document_text(), self.editor_panel.current_ast, entry.node, 0)
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
        """Ctrl+Espaço: sugere e pode inserir um lance legal no cursor."""
        suggestions = self.editor_panel.legal_move_suggestions()
        if not suggestions:
            self._report("Não da para saber a posição neste ponto do texto.")
            return
        dialog = LegalMoveDialog(suggestions, self)
        if dialog.exec() != LegalMoveDialog.DialogCode.Accepted:
            return
        san = dialog.selected_move()
        board = self.editor_panel.board_at_cursor()
        if not san or board is None:
            return
        self.editor_panel.insert_board_move(san, board.turn, board.fullmove_number)
        self._report(f"Lance {san} inserido no ponto do cursor.")

    def _indent_variations(self):
        self._report(self.editor_panel.action_indent_variations())

    def _flatten_variations(self):
        self._report(self.editor_panel.action_flatten_variations())

    def _toggle_pgn_tree(self):
        visible = self.editor_panel.toggle_pgn_tree()
        self._report("Árvore do PGN visível." if visible else "Árvore do PGN oculta.")

    def _show_outline(self):
        self.chess_panel.show_outline()

    def _copy_outline(self):
        text = self.chess_panel.outline_panel.copy_outline()
        self._report(f"Árvore copiada ({len(text.splitlines())} linha(s))." if text else "Não ha árvore para copiar.")

    def _toggle_nag_bar(self):
        visible = self.editor_panel.toggle_nag_bar()
        self._report("Rodapé de símbolos visível." if visible else "Rodapé de símbolos oculto.")

    def _convert_book_text(self):
        """Ctrl+Shift+L -- converte a selecao, ou o documento inteiro."""
        cursor = self.editor_panel.text_edit.textCursor()
        source = (
            cursor.selectedText().replace(" ", "\n") if cursor.hasSelection() else self.editor_panel.document_text()
        )
        if not source.strip():
            self._report("Não ha texto para converter.")
            return

        dialog = BookImportDialog(source, self)
        if dialog.exec() != BookImportDialog.DialogCode.Accepted:
            return

        self._report(self.editor_panel.action_convert_book_text(dialog.options(), dialog.converted_text()))

    def _convert_from_pdf(self, text: str):
        """A pagina do PDF ja convertida, com a previa antes de entrar."""
        dialog = BookImportDialog(text, self)
        if dialog.exec() != BookImportDialog.DialogCode.Accepted:
            return

        converted = dialog.converted_text()
        if converted:
            self._insert_from_pdf(converted)

    def _clean_paste(self):
        text = self.editor_panel.document_text()
        if not text.strip():
            self._report("Não ha texto para limpar.")
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
        detail = "\n".join(f"- posição {problem.position}: {problem.message}" for problem in problems[:12])
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

        previous_locale = self.preferences.effective_notation_locale
        self.preferences = dialog.values()
        save_preferences(self.preferences)
        # Mudar o tema nao pode trocar o idioma do livro aberto: a preferencia de
        # idioma so vale para o documento quando foi ela que mudou.
        self._apply_preferences(apply_notation_locale=self.preferences.effective_notation_locale != previous_locale)
        self._report("Preferencias gravadas.")

    def _apply_preferences(self, apply_notation_locale: bool = True):
        """Poe as preferencias em uso agora, sem precisar reabrir o programa."""
        application = QApplication.instance()
        if application is not None:
            apply_theme(application, self.preferences.theme)

        self.editor_panel.set_theme(self.preferences.theme)
        self.chess_panel.outline_panel.set_theme(self.preferences.theme)
        self.editor_panel.set_editor_font(self.preferences.editor_font_family, self.preferences.editor_font_size)
        self.editor_panel.text_edit.auto_close_enabled = self.preferences.auto_close_delimiters
        if apply_notation_locale:
            self.editor_panel.set_locale(self.preferences.effective_notation_locale)
            self.project.meta.notation_locale = self.editor_panel.locale
        self.dictionary_service.set_locale(self.preferences.effective_dictionary_locale)

        self.engine_settings = replace(
            self.engine_settings,
            path=self.preferences.engine_path,
            movetime_ms=self.preferences.engine_movetime_ms,
            threshold_cp=self.preferences.engine_threshold_cp,
            include_variations=self.preferences.engine_include_variations,
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
            self._report("Dupla visao: o programa não mexeu em nenhum lance deste livro.")

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
            f"não entendidos {counts['nao_entendido']}"
        )

    def _accept_issue(self, issue):
        """Grava a correcao no texto bruto -- o alerta some porque a causa sumiu."""
        candidate = issue.best_candidate
        if not candidate:
            return
        self._set_recognition_action(issue, "accepted", candidate.san)
        text = candidate.replacement or candidate.san
        self.editor_panel.replace_span(issue.raw_start, issue.raw_end, text)
        self.statusBar().showMessage(f"'{issue.raw_text}' substituido por '{text}'.", 4000)
        if candidate.replacement is None:
            # Inserir um lance que faltava nao e correcao de OCR: nada a aprender.
            self._count_acceptance(issue, candidate)

    def _comment_issue(self, issue):
        self._set_recognition_action(issue, "commented")
        self.editor_panel.wrap_span_as_comment(issue.raw_start, issue.raw_end)
        self.statusBar().showMessage(f"'{issue.raw_text}' virou comentário.", 4000)

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
        self._set_recognition_action(issue, "ignored")
        self.project.state.ignored_issue_tokens.append(token)
        self.editor_panel.set_ignored_tokens(self.project.state.ignored_issue_tokens)
        self.statusBar().showMessage(f"'{token}' passa a ser tratado como texto neste projeto.", 5000)

    # ------------------------------------------------------------------
    # Motor de xadrez (fase 5)
    # ------------------------------------------------------------------

    def _on_notation_locale_changed(self, locale: str) -> None:
        """A caixa do editor trocou o idioma: vale para o documento e vai para o projeto."""
        if self.project.meta.notation_locale != locale:
            self.project.meta.notation_locale = locale
            self._state_dirty = True
            self._update_window_title()
        # Os alertas do motor falavam da leitura antiga dos lances.
        self._engine_findings = None
        self._refresh_issues()
        self.statusBar().showMessage(f"Idioma do texto: {LOCALE_NAME.get(locale, locale)}. Texto reanalisado.", 5000)

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
            self._report("O motor já esta conferindo uma partida.")
            return

        if not discover_engine_path(self.engine_settings.path):
            self._offer_engine_setup()
            return

        ast = self.editor_panel.reparse_now()
        if not ast.moves:
            self._report("Não ha linha principal para o motor conferir.")
            return

        segment = self.editor_panel.active_segment()
        self._pending_engine_segment = (
            segment.body if segment else self.editor_panel.document_text(),
            segment.raw_offset if segment else 0,
        )
        self.statusBar().showMessage(
            "Motor conferindo a linha principal e as variantes..."
            if self.engine_settings.include_variations
            else "Motor conferindo a linha principal..."
        )
        self.engine_scanner.scan(ast, self.editor_panel.current_start_fen, self.engine_settings)

    def _on_engine_scan_finished(self, report):
        body, offset = self._pending_engine_segment
        self._engine_findings = ScanFindings(segment_body=body, raw_offset=offset, issues=list(report.issues))

        live = self._live_engine_issues()
        self._refresh_issues()

        if report.issues and not live:
            self._report("O texto mudou durante a conferência; rode o motor de novo.")
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
            other for other in findings.issues if not (other.code == issue.code and other.raw_start == original_start)
        ]
        self._refresh_issues()

    def _configure_engine(self):
        current = discover_engine_path(self.engine_settings.path)
        file_path, _filter = QFileDialog.getOpenFileName(
            self,
            "Executável do motor UCI",
            current or str(Path.cwd()),
            "Executáveis (*.exe);;Todos os arquivos (*)",
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
            "principal que derruba a avaliação sem o livro marcar com '?' -- que\n"
            "quase sempre e erro de leitura, não erro do mestre.\n\n"
            f"Instale o Stockfish no PATH, aponte o caminho em {ENGINE_ENV_VAR},\n"
            "ou escolha o executável agora.",
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
            f"Dicionário do projeto: '{issue.raw_text}' -> '{candidate.san}'. Salve o projeto para guardar.",
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
            "Aprender esta correção",
            f"Você já aceitou '{issue.raw_text}' -> '{candidate.san}' {times} vezes.\n\n"
            "Gravar no dicionário para o programa passar a fazer isso sozinho?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self._learn_substitution(issue.raw_text, candidate.san)

    # ------------------------------------------------------------------
    # Exportacao
    # ------------------------------------------------------------------

    def _confirm_diagram_export(self) -> bool:
        pending = [
            record for record in self.project.state.diagram_records if str(record.get("review", "pending")) == "pending"
        ]
        if not pending:
            return True
        preview = ", ".join(f"pág. {record.get('page', '?')}/d.{record.get('diagram', '?')}" for record in pending[:8])
        choice = QMessageBox.warning(
            self,
            "Diagramas por conferir",
            f"Há {len(pending)} posição(ões) de diagrama ainda pendente(s) de revisão.\n\n"
            f"{preview}\n\nExportar mesmo assim?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Save

    def export_pgn(self):
        ast = self.editor_panel.reparse_now()
        if not ast.moves and not ast.result:
            QMessageBox.information(self, "Exportar PGN", "Não ha PGN analisado para exportar.")
            return

        pgn_text, report = build_pgn(ast, self.editor_panel.current_start_fen)

        header_problems = validate_pgn_headers(dict(ast.headers), self.editor_panel.current_start_fen)
        if header_problems:
            QMessageBox.warning(
                self,
                "Headers PGN incoerentes",
                "\n".join(f"- {problem}" for problem in header_problems) + "\n\nAjuste os headers antes de exportar.",
            )
            return

        if not self._confirm_diagram_export():
            return

        pending = [move for move in iter_moves(ast.moves) if move.needs_review]
        if pending:
            preview = ", ".join(f"{move.san} -> {move.display_san}" for move in pending[:5])
            choice = QMessageBox.warning(
                self,
                "Correções por conferir",
                f"{len(pending)} lance(s) foram corrigidos automaticamente sem certeza e ainda\n"
                f"não foram conferidos:\n\n{preview}\n\n"
                "Exportar assim mesmo?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Save:
                return

        if not report.ok:
            choice = QMessageBox.warning(
                self,
                "Conferência do PGN",
                f"O PGN gerado não confere com o que foi analisado:\n\n{report.summary()}\n\nSalvar assim mesmo?",
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
            pending_diagrams = sum(
                str(record.get("review", "pending")) == "pending" for record in self.project.state.diagram_records
            )
            write_durable_text(
                output_path.with_suffix(output_path.suffix + ".report.txt"),
                export_audit_text(
                    report,
                    pending_diagrams=pending_diagrams,
                    policy="exportado após confirmação" if pending_diagrams else "sem pendências",
                ),
            )
        except OSError as exc:
            QMessageBox.critical(self, "Exportar PGN", f"Não foi possível gravar o arquivo.\n\n{exc}")
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
        if not self._confirm_diagram_export():
            return False
        if report.ok:
            return True

        choice = QMessageBox.warning(
            self,
            "Conferência do livro",
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
            QMessageBox.information(self, title, "Não ha partida analisada para exportar.")
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
            write_durable_text(output_path, builder(games))
        except OSError as exc:
            QMessageBox.critical(self, title, f"Não foi possível gravar o arquivo.\n\n{exc}")
            return

        self._report(f"{len(games)} partida(s) em {output_path.name}.")

    def export_structural_json(self):
        self._export_other_format(
            "Exportar análise em .json",
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

    def export_html(self):
        analyses = self._book_analyses()
        if not analyses:
            QMessageBox.information(self, "Exportar HTML", "Não há partida analisada para exportar.")
            return
        file_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Exportar HTML navegável",
            str(Path.cwd() / f"{self.project.display_name}.html"),
            "Página HTML (*.html);;Todos os arquivos (*)",
        )
        if not file_path:
            return
        output_path = Path(file_path)
        if not output_path.suffix:
            output_path = output_path.with_suffix(".html")
        try:
            write_durable_text(output_path, build_html(build_games_for_export(analyses)))
        except OSError as exc:
            QMessageBox.critical(self, "Exportar HTML", f"Não foi possível gravar o arquivo.\n\n{exc}")
            return
        self.statusBar().showMessage(f"HTML navegável gravado em {output_path}", 8000)

    def _search_document(self):
        dialog = SearchDialog(self.editor_panel.document_text(), self.editor_panel.analyze_whole_book(), self)
        dialog.hit_selected_signal.connect(self.editor_panel.focus_span)
        dialog.exec()

    def export_book(self):
        analyses = self._book_analyses()
        if not analyses:
            QMessageBox.information(self, "Exportar livro", "Não ha partida analisada para exportar.")
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
            QMessageBox.critical(self, "Exportar livro", f"Não foi possível gravar o arquivo.\n\n{exc}")
            return

        self.statusBar().showMessage(f"{report.summary()} Arquivo: {output_path}", 8000)

    def export_games_separately(self):
        analyses = self._book_analyses()
        if not analyses:
            QMessageBox.information(self, "Exportar partidas", "Não ha partida analisada para exportar.")
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
            QMessageBox.critical(self, "Exportar partidas", f"Não foi possível gravar os arquivos.\n\n{exc}")
            return

        self.statusBar().showMessage(f"{len(written)} arquivo(s) gravado(s) em {target_dir}", 8000)

    # ------------------------------------------------------------------
    # Autosave e recuperacao
    # ------------------------------------------------------------------

    def _on_text_changed(self):
        # `toPlainText()` copia o livro inteiro; usar o estado O(1) do Qt para
        # que atualizar o título não custe 7 MB a cada tecla.
        dirty = self._state_dirty or self.editor_panel.text_edit.document().isModified()
        name = self.project.display_name
        self.setWindowTitle(f"{'*' if dirty else ''}{name} - PGN Live Editor")
        self._chars_since_autosave += 1
        if self._chars_since_autosave >= AUTOSAVE_CHAR_THRESHOLD:
            self._autosave()

    def _autosave(self):
        text = self.editor_panel.document_text()
        self._chars_since_autosave = 0

        if not text.strip() or text == self._last_autosaved_text:
            return

        try:
            self._write_snapshot(text)
        except OSError:
            logger.warning("Autosave falhou", exc_info=True)
            self.lbl_autosave.setText("autosave indisponivel")
            return

        self._last_autosaved_text = text
        self.lbl_autosave.setText("recuperação gravada")
        single_shot(self, 4000, lambda: self.lbl_autosave.setText(""))

    def emergency_snapshot(self) -> None:
        """Grava a recuperacao ja, sem olhar contador: chamado por `crash_safety`."""
        text = self.editor_panel.document_text()
        if text.strip():
            self._write_snapshot(text)
            self._last_autosaved_text = text

    def _write_snapshot(self, text: str) -> None:
        """O projeto inteiro com o texto do editor -- sem mexer em `self.project`.

        `_is_dirty` compara o editor com `project.raw_text`; sincronizar o
        projeto aqui faria o programa esquecer que ha trabalho nao salvo.
        """
        meta = replace(self.project.meta, notation_locale=self.editor_panel.locale)
        self.recovery.write(replace(self.project, meta=meta), text, cursor_position=self.editor_panel.cursor_position())

    def show_unexpected_error(self, summary: str) -> None:
        """Aviso nao modal: o programa continua aberto e o trabalho foi guardado."""
        from ..services.crash_safety import log_path

        location = log_path()
        text = (
            "Ocorreu um erro inesperado. O programa continua aberto e o texto foi "
            "guardado para recuperação.\n\n"
            f"{summary}" + (f"\n\nDetalhes no log:\n{location}" if location else "")
        )
        box = getattr(self, "_unexpected_error_box", None)
        if box is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Erro inesperado")
            box.setModal(False)
            self._unexpected_error_box = box
        box.setText(text)
        box.show()

    def _offer_session_recovery(self):
        """Oferece o snapshot mais novo de uma instancia que morreu (R-102)."""
        orphans = orphan_snapshots()
        if not orphans:
            return
        snapshot = orphans[0]

        minutes = int(snapshot.age_seconds // 60)
        when = f"ha {minutes} min" if minutes else "ha menos de 1 min"
        origin = snapshot.origin_path.name if snapshot.origin_path else "projeto sem nome"
        others = f"\n(Há mais {len(orphans) - 1} sessão(ões) para recuperar depois.)" if len(orphans) > 1 else ""

        choice = QMessageBox.question(
            self,
            "Recuperar sessão",
            f"O editor foi fechado sem salvar.\n\n"
            f"Origem: {origin}\n"
            f"Gravado {when} ({len(snapshot.raw_text)} caracteres).{others}\n\n"
            "Deseja recuperar esse trabalho?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self._recover(snapshot)
        else:
            discard(snapshot)

    def _recover(self, snapshot) -> None:
        """Reabre o projeto inteiro do snapshot; ele passa a ser desta janela."""
        self._switch_to(restore_project(snapshot), cursor_position=snapshot.cursor_position)
        # O texto recuperado ainda nao esta no `.plez`: salvar e o que consolida.
        self._state_dirty = True
        self._write_snapshot(self.editor_panel.document_text())
        discard(snapshot)
        self._update_window_title()
        self.statusBar().showMessage("Sessão recuperada. Salve o projeto para consolidar.", 8000)

    def closeEvent(self, event):
        if self._is_dirty():
            choice = QMessageBox.question(
                self,
                "Sair",
                "Ha alterações que ainda não foram salvas no projeto.\n\nDeseja salvar antes de sair?",
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
        self.diagram_ocr.shutdown()
        self.full_pdf_recognition.shutdown()
        self.editor_panel.shutdown()
        self.engine_scanner.shutdown()
        self.pdf_panel.shutdown()
        self.recovery.clear()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
