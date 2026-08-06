import re
from collections.abc import Callable, Sequence

import chess
from PyQt6.QtCore import QTimer
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

from ..core.normalizer import LiveTextNormalizer
from ..core.parser import GameAST, TolerantParser
from ..core.paste_cleanup import clean_pdf_text
from ..core.pgn_headers import extract_pgn_headers, insert_or_replace_headers, validate_headers
from ..core.text_insertion import build_board_move_insertion
from ..core.text_ops import (
    OperationResult,
    check_balance,
    paragraph_to_comment,
    renumber_document,
    unwrap_at,
    wrap_as_comment,
    wrap_as_variation,
)
from ..core.tokenizer import PGNTokenizer

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
    cursor_position_changed_signal = Signal(int)

    def __init__(self):
        super().__init__()

        self.locale = 'pt'
        self.normalizer = LiveTextNormalizer(locale=self.locale)
        self.tokenizer = PGNTokenizer()
        self.parser = TolerantParser(locale=self.locale)

        self.current_ast = GameAST()
        self.current_start_fen = chess.STARTING_FEN
        self.current_move_span = None
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
        editor_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        editor_font.setPointSize(11)
        self.text_edit.setFont(editor_font)
        self.highlighter = MoveHighlighter(self.text_edit.document())
        layout.addWidget(self.text_edit)

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._do_reparse)

        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.cursorPositionChanged.connect(self._on_cursor_position_changed)

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
        self.reparse_now()

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
        self.debounce_timer.start(300)

    def _on_cursor_position_changed(self):
        if self._suspend_cursor_sync:
            return
        self.cursor_position_changed_signal.emit(self.text_edit.textCursor().position())

    def _do_reparse(self):
        raw_text = self.text_edit.toPlainText()
        headers, body_text, body_offset = extract_pgn_headers(raw_text)

        if not raw_text.strip():
            self.current_ast = GameAST()
            self.current_start_fen = chess.STARTING_FEN
            self.current_move_span = None
            self._apply_highlights(self.current_ast)
            self.text_parsed_signal.emit(self.current_ast)
            return

        # Fragmento de livro que parte de um diagrama: o parser sempre soube
        # receber uma posicao inicial, mas o editor nunca a repassava.
        start_fen = headers.get("FEN", chess.STARTING_FEN)
        try:
            chess.Board(start_fen)
        except ValueError:
            start_fen = chess.STARTING_FEN

        normalized = self.normalizer.normalize_with_mapping(body_text, base_offset=body_offset)
        tokens = self.tokenizer.tokenize(normalized)
        ast = self.parser.parse(tokens, start_fen=start_fen)
        ast.headers = list(headers.items())
        ast.issues.extend(validate_headers(headers, raw_text))

        self.current_ast = ast
        self.current_start_fen = start_fen
        self._apply_highlights(ast)
        self.text_parsed_signal.emit(ast)

    # ------------------------------------------------------------------
    # Configuracao do reconhecimento
    # ------------------------------------------------------------------

    def set_locale(self, locale: str):
        self.locale = locale
        self.normalizer.locale = locale
        self.parser.set_locale(locale)
        self.reparse_now()

    def set_dictionary(self, dictionary: dict[str, str]):
        self.parser.set_dictionary(dictionary)
        self.reparse_now()

    def set_ignored_tokens(self, tokens):
        self.parser.set_ignored_tokens(tokens)
        self.reparse_now()

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
        return self.apply_operation(renumber_document(self.document_text(), self.current_ast))

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
        if self.debounce_timer.isActive():
            self.debounce_timer.stop()
        self._do_reparse()
        return self.current_ast

    def _make_selection(self, start_index: int, end_index: int, text_format: QTextCharFormat) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.cursor = self.text_edit.textCursor()
        selection.cursor.setPosition(start_index)
        selection.cursor.setPosition(end_index, QTextCursor.MoveMode.KeepAnchor)
        selection.format = text_format
        return selection

    def _iter_moves_with_depth(self, moves, depth: int = 0):
        for move in moves:
            yield move, depth
            for variation in move.variations:
                yield from self._iter_moves_with_depth(variation, depth + 1)

    def _apply_highlights(self, ast: GameAST):
        highlight_ranges = []

        fmt_valid = QTextCharFormat()
        fmt_valid.setForeground(QColor("#78aefc"))
        fmt_valid.setFontWeight(QFont.Weight.Bold)

        fmt_valid_variation = QTextCharFormat(fmt_valid)
        fmt_valid_variation.setForeground(QColor("#9fc4ff"))
        fmt_valid_variation.setFontWeight(QFont.Weight.Normal)

        fmt_fixed = QTextCharFormat()
        fmt_fixed.setForeground(QColor("#95e1a7"))
        fmt_fixed.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
        fmt_fixed.setUnderlineColor(QColor("#5bb974"))
        fmt_fixed.setFontWeight(QFont.Weight.Bold)

        fmt_fixed_variation = QTextCharFormat(fmt_fixed)
        fmt_fixed_variation.setFontWeight(QFont.Weight.Normal)

        # Amarelo pontilhado: aplicado em carater provisorio, pendente de conferencia.
        fmt_review = QTextCharFormat()
        fmt_review.setForeground(QColor("#f0c05a"))
        fmt_review.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
        fmt_review.setUnderlineColor(QColor("#f0c05a"))
        fmt_review.setFontWeight(QFont.Weight.Bold)

        fmt_review_variation = QTextCharFormat(fmt_review)
        fmt_review_variation.setFontWeight(QFont.Weight.Normal)

        fmt_error = QTextCharFormat()
        fmt_error.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        fmt_error.setUnderlineColor(QColor("#ff6666"))
        fmt_error.setForeground(QColor("#ffd5d5"))
        fmt_error.setFontWeight(QFont.Weight.Bold)

        fmt_error_variation = QTextCharFormat(fmt_error)
        fmt_error_variation.setFontWeight(QFont.Weight.Normal)

        for move, depth in self._iter_moves_with_depth(ast.moves):
            if move.start_index < 0 or move.end_index <= move.start_index:
                continue

            valid_format = fmt_valid if depth == 0 else fmt_valid_variation
            fixed_format = fmt_fixed if depth == 0 else fmt_fixed_variation
            review_format = fmt_review if depth == 0 else fmt_review_variation
            error_format = fmt_error if depth == 0 else fmt_error_variation

            if not move.is_valid:
                highlight_ranges.append((move.start_index, move.end_index, error_format))
            elif move.needs_review:
                highlight_ranges.append((move.start_index, move.end_index, review_format))
            elif move.corrected_san:
                highlight_ranges.append((move.start_index, move.end_index, fixed_format))
            else:
                highlight_ranges.append((move.start_index, move.end_index, valid_format))

        self.highlighter.set_ranges(highlight_ranges)
        self._apply_current_selection()

    def _apply_current_selection(self):
        """Realce do lance sob o cursor. Nao mexe no realce sintatico."""
        if not self.current_move_span:
            self.text_edit.setExtraSelections([])
            return

        fmt_current = QTextCharFormat()
        fmt_current.setBackground(QColor("#4b5568"))

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
