import re
from collections.abc import Callable, Sequence

import chess
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.fen_tools import normalize_diagram_marks, set_start_fen
from ..core.game_splitter import GameSegment
from ..core.parser import GameAST, iter_moves
from ..core.paste_cleanup import clean_pdf_text
from ..core.pgn_headers import insert_or_replace_headers, upsert_headers
from ..core.text_insertion import build_board_move_insertion
from ..core.text_ops import (
    OperationResult,
    TextEdit,
    check_balance,
    paragraph_to_comment,
    renumber_document,
    renumber_movetext,
    unwrap_at,
    wrap_as_comment,
    wrap_as_variation,
)
from ..services.parse_worker import (
    AsyncParser,
    DocumentAnalysis,
    ParseConfig,
    SegmentAnalysis,
)
from ..services.preferences_service import THEME_DARK
from .theme import palette_of

SYMBOL_PALETTE: Sequence[tuple[str, str, str]] = (
    ("!", "$1", "Bom lance"),
    ("?", "$2", "Lance fraco"),
    ("!!", "$3", "Lance excelente"),
    ("??", "$4", "Erro grave"),
    ("!?", "$5", "Lance interessante"),
    ("?!", "$6", "Lance duvidoso"),
    ("\u25a1", "$7", "Lance forçado / único"),
    ("\u221e", "$13", "Posição pouco clara"),
    ("\u2a72", "$14", "Brancas com pequena vantagem"),
    ("\u2a71", "$15", "Pretas com pequena vantagem"),
    ("\u00b1", "$16", "Brancas com vantagem moderada"),
    ("\u2213", "$17", "Pretas com vantagem moderada"),
    ("\u2a00", "$22/$23", "Zugzwang"),
)


class SymbolPaletteDialog(QDialog):
    def __init__(self, insert_symbol: Callable[[str], None], parent: QWidget | None = None):
        super().__init__(parent)
        self._insert_symbol = insert_symbol

        self.setWindowTitle("Paleta de Simbolos")
        self.setModal(False)
        self.resize(420, 320)

        layout = QVBoxLayout(self)
        intro = QLabel("Clique em um simbolo para inseri-lo no cursor do Text Editor.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        for index, (symbol, nag_code, description) in enumerate(SYMBOL_PALETTE):
            button = QPushButton(f"{symbol}\n{nag_code}")
            button.setToolTip(description)
            button.setMinimumSize(84, 56)
            button.clicked.connect(lambda _checked=False, value=symbol: self._insert_symbol(value))
            grid.addWidget(button, index // 3, index % 3)

        layout.addLayout(grid)

        note = QLabel("No PGN salvo, os simbolos sao convertidos para o NAG canonico correspondente.")
        note.setWordWrap(True)
        layout.addWidget(note)

        close_button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button_box.rejected.connect(self.close)
        layout.addWidget(close_button_box)


class PGNTextEdit(QPlainTextEdit):
    """Editor com auto-fechamento de `{}` e `()`.

    Com texto selecionado, digitar o delimitador **envolve a selecao** -- e o
    caminho mais curto do fluxo: selecionar a prosa e apertar `{`.
    """

    PAIRS = {"{": "}", "(": ")"}

    def __init__(self):
        super().__init__()
        self.auto_close_enabled = True
        # Quem sabe renumerar e o painel: aqui so se pergunta.
        self.renumber_paste: Callable[[str], str] | None = None

    def insertFromMimeData(self, source):
        """Colar um trecho de movetext o renumera a partir da posicao do cursor.

        Copiar uma linha de outro ponto do livro traz os numeros de la, e no
        destino eles estao errados. Renumerar na colagem tira esse retrabalho --
        e como a renumeracao empurra os lances num tabuleiro de verdade, um
        trecho que nao encaixa na posicao volta **intocado** em vez de sair
        numerado errado.
        """
        if self.renumber_paste is None or not source.hasText():
            super().insertFromMimeData(source)
            return

        text = source.text()
        renumbered = self.renumber_paste(text)
        if renumbered == text:
            super().insertFromMimeData(source)
            return

        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(renumbered)
        cursor.endEditBlock()

    def keyPressEvent(self, event):
        if not self.auto_close_enabled:
            super().keyPressEvent(event)
            return

        typed = event.text()
        cursor = self.textCursor()

        if typed in self.PAIRS:
            closing = self.PAIRS[typed]
            if cursor.hasSelection():
                start, end = cursor.selectionStart(), cursor.selectionEnd()
                body = cursor.selectedText()
                cursor.beginEditBlock()
                cursor.insertText(f"{typed}{body}{closing}")
                cursor.endEditBlock()
                selection = self.textCursor()
                selection.setPosition(start)
                selection.setPosition(end + 2, QTextCursor.MoveMode.KeepAnchor)
                self.setTextCursor(selection)
                return

            cursor.beginEditBlock()
            cursor.insertText(typed + closing)
            cursor.endEditBlock()
            cursor.setPosition(cursor.position() - 1)
            self.setTextCursor(cursor)
            return

        if typed in self.PAIRS.values() and not cursor.hasSelection():
            position = cursor.position()
            text = self.toPlainText()
            # Ja existe o fechamento logo a frente: so passa por cima dele.
            if position < len(text) and text[position] == typed:
                cursor.setPosition(position + 1)
                self.setTextCursor(cursor)
                return

        super().keyPressEvent(event)


class EditorPanel(QWidget):
    text_parsed_signal = Signal(object)
    document_analyzed_signal = Signal(object)
    cursor_position_changed_signal = Signal(int)

    def __init__(self):
        super().__init__()

        self.locale = 'pt'
        self.async_parser = AsyncParser(ParseConfig.build(locale=self.locale), parent=self)
        self.async_parser.analysis_ready_signal.connect(self._on_background_analysis)

        self.analysis = DocumentAnalysis()
        self.current_ast = GameAST()
        self.current_start_fen = chess.STARTING_FEN
        self.current_move_span = None
        # Dupla visao: quando ligada, o realce apaga o que so foi confirmado.
        self.review_mode = False
        self.theme = THEME_DARK
        self._suspend_cursor_sync = False
        self._symbol_palette_dialog: SymbolPaletteDialog | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        lbl = QLabel("Text Editor (Raw PDF Text)")
        lbl.setStyleSheet("font-weight: bold;")

        btn_clean = QPushButton("Join Lines")
        btn_clean.setToolTip("Removes awkward newlines from PDF copy")
        btn_clean.clicked.connect(self.action_join_lines)

        btn_palette = QPushButton("Paleta de Simbolos")
        btn_palette.setToolTip("Insere sinais de avaliacao no cursor do editor")
        btn_palette.clicked.connect(self.open_symbol_palette)

        btn_headers = QPushButton("Inserir Headers")
        btn_headers.setToolTip("Insere ou atualiza os headers PGN padrao no topo do editor")
        btn_headers.clicked.connect(self.insert_default_headers)

        toolbar.addWidget(lbl)
        toolbar.addStretch()
        toolbar.addWidget(btn_headers)
        toolbar.addWidget(btn_palette)
        toolbar.addWidget(btn_clean)
        layout.addLayout(toolbar)

        self.text_edit = PGNTextEdit()
        self.text_edit.renumber_paste = self._renumber_pasted_movetext
        editor_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        editor_font.setPointSize(11)
        self.text_edit.setFont(editor_font)
        self.highlighter = MoveHighlighter(self.text_edit.document())
        layout.addWidget(self.text_edit)

        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.cursorPositionChanged.connect(self._on_cursor_position_changed)

    def shutdown(self):
        """Encerra a thread de analise. A janela chama isto ao fechar."""
        self.async_parser.shutdown()

    # ------------------------------------------------------------------
    # Documento
    # ------------------------------------------------------------------

    def document_text(self) -> str:
        return self.text_edit.toPlainText()

    def cursor_position(self) -> int:
        return self.text_edit.textCursor().position()

    def load_document_text(self, text: str, cursor_position: int = 0):
        """Carrega um documento novo (abrir/importar). Zera o historico de desfazer."""
        self.text_edit.setPlainText(text)
        cursor = self.text_edit.textCursor()
        cursor.setPosition(max(0, min(cursor_position, len(text))))
        self.text_edit.setTextCursor(cursor)
        self.text_edit.document().setModified(False)
        # Uma vez, ao abrir: e o que faz o livro inteiro nascer realcado e com a
        # lista de partidas preenchida. Depois disso so a partida editada e
        # reanalisada.
        self.analyze_whole_book()

    def replace_all_text(self, new_text: str):
        """Substitui todo o texto **preservando o historico de desfazer**.

        Usar sempre isto no lugar de `setPlainText` para transformacoes do
        proprio usuario: `setPlainText` apaga a pilha de Ctrl+Z e joga o cursor
        para o inicio, o que ja custou trabalho perdido em capitulos grandes.
        """
        if new_text == self.text_edit.toPlainText():
            return

        previous_position = self.text_edit.textCursor().position()
        scrollbar = self.text_edit.verticalScrollBar()
        previous_scroll = scrollbar.value()

        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(new_text)
        cursor.endEditBlock()

        restored = self.text_edit.textCursor()
        restored.setPosition(max(0, min(previous_position, len(new_text))))
        self.text_edit.setTextCursor(restored)
        scrollbar.setValue(previous_scroll)

    def replace_span(self, start_index: int, end_index: int, replacement: str):
        """Troca um trecho do texto bruto, de forma undoavel."""
        if start_index < 0 or end_index < start_index:
            return

        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start_index)
        cursor.setPosition(end_index, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.text_edit.setTextCursor(cursor)
        self.reparse_now()

    def action_join_lines(self):
        text = self.text_edit.toPlainText()
        clean_text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
        self.replace_all_text(clean_text)

    def open_symbol_palette(self):
        if self._symbol_palette_dialog is None:
            self._symbol_palette_dialog = SymbolPaletteDialog(self.insert_symbol_at_cursor, self)
            self._symbol_palette_dialog.finished.connect(self._on_symbol_palette_closed)

        self._symbol_palette_dialog.show()
        self._symbol_palette_dialog.raise_()
        self._symbol_palette_dialog.activateWindow()

    def _on_symbol_palette_closed(self, _result: int):
        self._symbol_palette_dialog = None

    def insert_symbol_at_cursor(self, symbol: str):
        cursor = self.text_edit.textCursor()
        cursor.insertText(symbol)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.setFocus()

    def insert_text_at_cursor(self, text: str):
        if not text:
            return

        cursor = self.text_edit.textCursor()
        cursor.insertText(text)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.setFocus()
        self.reparse_now()

    def insert_default_headers(self):
        updated_text = insert_or_replace_headers(self.text_edit.toPlainText())
        self.replace_all_text(updated_text)
        self.text_edit.setFocus()
        self.reparse_now()

    def insert_board_move(self, san: str, is_white_turn: bool, fullmove_number: int):
        cursor = self.text_edit.textCursor()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        insertion = build_board_move_insertion(
            self.text_edit.toPlainText(),
            start,
            end,
            san,
            chess.WHITE if is_white_turn else chess.BLACK,
            fullmove_number,
        )

        cursor.insertText(insertion)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.setFocus()
        self.reparse_now()

    def focus_span(self, start_index: int, end_index: int):
        if start_index < 0 or end_index < start_index:
            return

        self._suspend_cursor_sync = True
        try:
            cursor = self.text_edit.textCursor()
            cursor.setPosition(start_index)
            cursor.setPosition(end_index, QTextCursor.MoveMode.KeepAnchor)
            self.text_edit.setTextCursor(cursor)
            self.text_edit.centerCursor()
        finally:
            self._suspend_cursor_sync = False

        self.set_current_move_span(start_index, end_index)
        self.cursor_position_changed_signal.emit(self.text_edit.textCursor().position())

    def set_current_move_span(self, start_index: int | None, end_index: int | None):
        if start_index is None or end_index is None or start_index < 0 or end_index < start_index:
            self.current_move_span = None
        else:
            self.current_move_span = (start_index, end_index)

        # So a selecao muda aqui. Refazer o realce sintatico do documento
        # inteiro a cada movimento do cursor custava O(blocos x lances).
        self._apply_current_selection()

    def _on_text_changed(self):
        # A analise sai da thread de UI enquanto se digita; o resultado so e
        # aplicado se ainda corresponder ao texto que esta na tela.
        self.async_parser.schedule(self.text_edit.toPlainText(), self.cursor_position())

    def _on_cursor_position_changed(self):
        position = self.text_edit.textCursor().position()

        # Ao entrar noutra partida do livro, o tabuleiro e o preview passam a
        # segui-la. Vem do cache, entao nao ha custo de analise aqui.
        if self.analysis.segments and self.analysis.active_index != self._segment_index_at(position):
            self._do_reparse()

        if self._suspend_cursor_sync:
            return
        self.cursor_position_changed_signal.emit(position)

    def _segment_index_at(self, position: int) -> int:
        segment = self.analysis.segment_at(position)
        return segment.index if segment else 0

    def _do_reparse(self):
        analysis = self.async_parser.parse_now(self.text_edit.toPlainText(), self.cursor_position())
        self._apply_analysis(analysis)

    def _on_background_analysis(self, analysis: DocumentAnalysis):
        # O texto pode ter mudado enquanto a thread trabalhava: nesse caso ja ha
        # um pedido mais novo a caminho e este resultado nao vale nada.
        if analysis.raw_text != self.text_edit.toPlainText():
            return
        self._apply_analysis(analysis)

    def _apply_analysis(self, analysis: DocumentAnalysis):
        self.analysis = analysis
        self.current_ast = analysis.ast
        self.current_start_fen = analysis.start_fen

        if not analysis.raw_text.strip():
            self.current_move_span = None

        self._apply_highlights(analysis)
        self.document_analyzed_signal.emit(analysis)
        self.text_parsed_signal.emit(self.current_ast)

    # ------------------------------------------------------------------
    # Segmentos (fase 4)
    # ------------------------------------------------------------------

    def segments(self) -> list[GameSegment]:
        return self.analysis.segments

    def active_segment(self) -> GameSegment | None:
        segments = self.analysis.segments
        if not segments:
            return None
        return segments[min(self.analysis.active_index, len(segments) - 1)]

    def active_region(self) -> tuple[int, int]:
        """Limites da partida sob o cursor -- toda edicao de cabecalho usa isto."""
        segment = self.active_segment()
        if segment is None:
            return (0, len(self.document_text()))
        return (segment.content_offset, segment.end_offset)

    def go_to_segment(self, index: int):
        segments = self.analysis.segments
        if not 0 <= index < len(segments):
            return

        segment = segments[index]
        cursor = self.text_edit.textCursor()
        cursor.setPosition(min(segment.raw_offset, len(self.document_text())))
        self.text_edit.setTextCursor(cursor)
        self.text_edit.centerCursor()
        self.text_edit.setFocus()
        self.reparse_now()

    def analyze_whole_book(self) -> list[SegmentAnalysis]:
        """Analisa todas as partidas -- exportacao em lote e contagens da lista."""
        raw_text = self.document_text()
        analyses = self.async_parser.pipeline.analyze_all(raw_text)
        self._apply_analysis(self.async_parser.parse_now(raw_text, self.cursor_position()))
        return analyses

    # ------------------------------------------------------------------
    # Configuracao do reconhecimento
    # ------------------------------------------------------------------

    def _reconfigure(self, **changes):
        config = self.async_parser.config
        self.async_parser.configure(ParseConfig.build(
            locale=changes.get("locale", config.locale),
            dictionary=changes.get("dictionary", config.dictionary_map),
            ignored_tokens=changes.get("ignored_tokens", config.ignored_tokens),
            separator_pattern=changes.get("separator_pattern", config.separator_pattern),
        ))
        self.reparse_now()

    def configure_recognition(self, locale: str, dictionary: dict[str, str], ignored_tokens):
        """Idioma, dicionario e silenciados de uma vez -- uma unica reanalise."""
        self.locale = locale
        self._reconfigure(locale=locale, dictionary=dictionary, ignored_tokens=ignored_tokens)

    def set_locale(self, locale: str):
        self.locale = locale
        self._reconfigure(locale=locale)

    def set_dictionary(self, dictionary):
        self._reconfigure(dictionary=dictionary)

    def set_ignored_tokens(self, tokens):
        self._reconfigure(ignored_tokens=tokens)

    def wrap_span_as_comment(self, start_index: int, end_index: int):
        """Envolve um trecho do texto bruto em chaves, de forma undoavel."""
        text = self.text_edit.toPlainText()
        if start_index < 0 or end_index > len(text) or end_index <= start_index:
            return
        self.replace_span(start_index, end_index, "{" + text[start_index:end_index] + "}")

    # ------------------------------------------------------------------
    # Gestos de edicao estrutural (Fase 3)
    # ------------------------------------------------------------------

    def apply_operation(self, result: OperationResult) -> str:
        """Aplica um gesto inteiro num unico bloco de desfazer."""
        if not result.ok:
            return result.message

        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        for edit in sorted(result.edits, key=lambda item: item.start, reverse=True):
            cursor.setPosition(edit.start)
            cursor.setPosition(edit.end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(edit.replacement)
        cursor.endEditBlock()

        if result.select_start is not None and result.select_end is not None:
            selection = self.text_edit.textCursor()
            selection.setPosition(result.select_start)
            selection.setPosition(result.select_end, QTextCursor.MoveMode.KeepAnchor)
            self.text_edit.setTextCursor(selection)

        self.text_edit.setFocus()
        self.reparse_now()
        return result.message

    def _selection(self) -> tuple[int, int]:
        cursor = self.text_edit.textCursor()
        return (cursor.selectionStart(), cursor.selectionEnd())

    def action_wrap_comment(self) -> str:
        start, end = self._selection()
        return self.apply_operation(wrap_as_comment(self.document_text(), start, end))

    def action_wrap_variation(self) -> str:
        start, end = self._selection()
        return self.apply_operation(wrap_as_variation(self.document_text(), start, end, self.current_ast))

    def action_unwrap(self) -> str:
        return self.apply_operation(unwrap_at(self.document_text(), self.cursor_position()))

    def action_paragraph_to_comment(self) -> str:
        return self.apply_operation(
            paragraph_to_comment(self.document_text(), self.cursor_position(), self.current_ast)
        )

    def action_renumber(self) -> str:
        """Ctrl+R -- renumera o **livro inteiro**, nao so a partida sob o cursor."""
        text = self.document_text()
        edits: list[TextEdit] = []
        for analysis in self.analyze_whole_book():
            edits.extend(renumber_document(text, analysis.ast).edits)

        if not edits:
            return "A numeracao ja esta correta."
        return self.apply_operation(
            OperationResult(edits, f"{len(edits)} numero(s) de lance corrigido(s).")
        )

    def action_normalize_diagrams(self) -> str:
        return self.apply_operation(normalize_diagram_marks(self.document_text()))

    def action_set_start_fen(self, fen: str) -> str:
        """Grava a posicao do tabuleiro como inicio da partida sob o cursor."""
        region_start, region_end = self.active_region()
        return self.apply_operation(set_start_fen(self.document_text(), fen, region_start, region_end))

    def action_apply_headers(self, headers: dict[str, str]) -> str:
        """Reescreve o bloco de cabecalhos da partida sob o cursor."""
        text = self.document_text()
        region_start, region_end = self.active_region()

        segment = self.active_segment()
        current = dict(segment.headers) if segment else {}
        # Um cabecalho que existia e voltou vazio deve sumir, nao virar `""`.
        updates: dict[str, str | None] = {tag: None for tag in current if tag not in headers}
        updates.update(headers)

        change = upsert_headers(text, updates, region_start, region_end)
        if change is None:
            return "Os cabecalhos ja estao assim."

        start, end, block = change
        return self.apply_operation(OperationResult([TextEdit(start, end, block)], "Cabecalhos atualizados."))

    def action_clean_paste(self, options) -> str:
        report = clean_pdf_text(self.document_text(), options)
        if report.text == self.document_text():
            return "Nada a limpar."
        self.replace_all_text(report.text)
        self.reparse_now()
        return report.summary()

    def balance_problems(self):
        return check_balance(self.document_text())

    def reparse_now(self) -> GameAST:
        self.async_parser.cancel_pending()
        self._do_reparse()
        return self.current_ast

    def _make_selection(self, start_index: int, end_index: int, text_format: QTextCharFormat) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.cursor = self.text_edit.textCursor()
        selection.cursor.setPosition(start_index)
        selection.cursor.setPosition(end_index, QTextCursor.MoveMode.KeepAnchor)
        selection.format = text_format
        return selection

    def _apply_highlights(self, analysis: DocumentAnalysis):
        """Realce do **livro inteiro**, e nao so da partida sob o cursor.

        As partidas que nao estao sendo editadas vem do resumo em cache, ja
        deslocado para a posicao atual delas -- por isso elas nao apagam quando
        uma edicao acima empurra o texto.
        """
        colors = palette_of(self.theme)

        fmt_valid = QTextCharFormat()
        fmt_valid.setForeground(QColor(colors["move"]))
        fmt_valid.setFontWeight(QFont.Weight.Bold)

        fmt_valid_variation = QTextCharFormat(fmt_valid)
        fmt_valid_variation.setForeground(QColor(colors["move_variation"]))
        fmt_valid_variation.setFontWeight(QFont.Weight.Normal)

        fmt_fixed = QTextCharFormat()
        fmt_fixed.setForeground(QColor(colors["fixed"]))
        fmt_fixed.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
        fmt_fixed.setUnderlineColor(QColor(colors["fixed_underline"]))
        fmt_fixed.setFontWeight(QFont.Weight.Bold)

        fmt_fixed_variation = QTextCharFormat(fmt_fixed)
        fmt_fixed_variation.setFontWeight(QFont.Weight.Normal)

        # Pontilhado: aplicado em carater provisorio, pendente de conferencia.
        fmt_review = QTextCharFormat()
        fmt_review.setForeground(QColor(colors["review"]))
        fmt_review.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
        fmt_review.setUnderlineColor(QColor(colors["review"]))
        fmt_review.setFontWeight(QFont.Weight.Bold)

        fmt_review_variation = QTextCharFormat(fmt_review)
        fmt_review_variation.setFontWeight(QFont.Weight.Normal)

        fmt_error = QTextCharFormat()
        fmt_error.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        fmt_error.setUnderlineColor(QColor(colors["error_underline"]))
        fmt_error.setForeground(QColor(colors["error_text"]))
        fmt_error.setFontWeight(QFont.Weight.Bold)

        fmt_error_variation = QTextCharFormat(fmt_error)
        fmt_error_variation.setFontWeight(QFont.Weight.Normal)

        if self.review_mode:
            # Dupla visao (SPEC 5, fase 5): a pergunta deixa de ser "o que e
            # lance?" e passa a ser "onde o programa **mexeu**?". Tudo que ele
            # apenas confirmou apaga; o que ele tocou fica como estava. Numa
            # partida de 80 lances com tres correcoes, sao as tres que saltam.
            fmt_valid = QTextCharFormat()
            fmt_valid.setForeground(QColor(colors["confirmed_dimmed"]))
            fmt_valid_variation = QTextCharFormat(fmt_valid)

        formats = {
            ("valid", False): fmt_valid,
            ("valid", True): fmt_valid_variation,
            ("fixed", False): fmt_fixed,
            ("fixed", True): fmt_fixed_variation,
            ("review", False): fmt_review,
            ("review", True): fmt_review_variation,
            ("error", False): fmt_error,
            ("error", True): fmt_error_variation,
        }

        self.highlighter.set_ranges([
            (start, end, formats[(kind, in_variation)])
            for start, end, kind, in_variation in analysis.highlight_spans
        ])
        self._apply_current_selection()

    # ------------------------------------------------------------------

    def _renumber_pasted_movetext(self, text: str) -> str:
        """Renumera o trecho colado a partir da posicao do tabuleiro no cursor."""
        if "\n" in text.strip() or not text.strip():
            # Colagem de bloco (uma pagina inteira, por exemplo) nao e uma linha
            # de lances: nao ha posicao unica de onde renumerar.
            return text

        board = self.board_at_cursor()
        if board is None:
            return text
        return renumber_movetext(text, board)

    def board_at_cursor(self) -> chess.Board | None:
        """A posicao do tabuleiro no ponto do cursor, se der para saber."""
        position = self.cursor_position()
        board = chess.Board(self.current_start_fen)

        pushed = False
        for move in self.current_ast.moves:
            if move.san_start_index >= position:
                break
            if not move.is_valid:
                return None
            try:
                board.push_san(move.display_san)
                pushed = True
            except (ValueError, AssertionError):
                return None

        return board if pushed or not self.current_ast.moves else board

    def move_at_cursor(self):
        """O lance sobre o qual o cursor esta, se houver um."""
        position = self.cursor_position()
        for move in iter_moves(self.current_ast.moves):
            if move.san_start_index <= position <= move.san_end_index:
                return move
        return None

    def legal_move_suggestions(self, limit: int = 12) -> list[str]:
        """Lances legais na posicao do cursor -- o `Ctrl+Espaco` da SPEC 4.5."""
        board = self.board_at_cursor()
        if board is None:
            return []
        return sorted(board.san(move) for move in board.legal_moves)[:limit]

    def set_theme(self, theme: str):
        self.theme = theme
        self._apply_highlights(self.analysis)

    def set_editor_font(self, family: str, size: int):
        font = QFont(family, size)
        self.text_edit.setFont(font)

    def set_review_mode(self, enabled: bool):
        """Liga a dupla visao: confirmado apaga, tocado permanece."""
        if enabled == self.review_mode:
            return
        self.review_mode = enabled
        self._apply_highlights(self.analysis)

    def touch_counts(self) -> dict[str, int]:
        """`{confirmado, corrigido, por_conferir, nao_entendido}` do livro inteiro.

        Vem dos mesmos spans do realce, e por isso conta o **documento**, nao so
        a partida sob o cursor.
        """
        counts = {"confirmado": 0, "corrigido": 0, "por_conferir": 0, "nao_entendido": 0}
        names = {"valid": "confirmado", "fixed": "corrigido", "review": "por_conferir", "error": "nao_entendido"}
        for _start, _end, kind, _in_variation in self.analysis.highlight_spans:
            key = names.get(kind)
            if key:
                counts[key] += 1
        return counts

    def _apply_current_selection(self):
        """Realce do lance sob o cursor. Nao mexe no realce sintatico."""
        if not self.current_move_span:
            self.text_edit.setExtraSelections([])
            return

        fmt_current = QTextCharFormat()
        fmt_current.setBackground(QColor(palette_of(self.theme)["current_move"]))

        start_index, end_index = self.current_move_span
        self.text_edit.setExtraSelections([self._make_selection(start_index, end_index, fmt_current)])


class MoveHighlighter(QSyntaxHighlighter):
    """Realce sintatico com os ranges pre-indexados por bloco.

    A versao anterior varria a lista inteira de lances para cada bloco do
    documento, o que dava O(blocos x lances) a cada rehighlight.
    """

    def __init__(self, document):
        super().__init__(document)
        self._ranges_by_block: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}

    def set_ranges(self, ranges):
        document = self.document()
        indexed: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}

        for start_index, end_index, text_format in ranges:
            first_block = document.findBlock(start_index)
            if not first_block.isValid():
                continue

            block = first_block
            while block.isValid() and block.position() < end_index:
                indexed.setdefault(block.blockNumber(), []).append((start_index, end_index, text_format))
                block = block.next()

        self._ranges_by_block = indexed
        self.rehighlight()

    def highlightBlock(self, text: str):
        block_ranges = self._ranges_by_block.get(self.currentBlock().blockNumber())
        if not block_ranges:
            return

        block_start = self.currentBlock().position()
        for start_index, end_index, text_format in block_ranges:
            local_start = max(0, start_index - block_start)
            local_end = min(len(text), end_index - block_start)
            if local_end > local_start:
                self.setFormat(local_start, local_end - local_start, text_format)
