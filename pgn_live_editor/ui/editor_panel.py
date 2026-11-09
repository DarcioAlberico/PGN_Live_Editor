import re
from collections.abc import Callable, Sequence
from functools import lru_cache

import chess
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.book_import import BookImportOptions, convert_book_text
from ..core.fen_tools import normalize_diagram_marks, set_start_fen
from ..core.game_splitter import GameSegment
from ..core.layout import flatten_variations, indent_variations
from ..core.move_tree import build_entry_maps, build_move_entries
from ..core.nag_table import NAG_TABLE
from ..core.notation_detect import detect_notation_locale
from ..core.parser import GameAST, MoveNode, iter_moves
from ..core.paste_cleanup import clean_pdf_text
from ..core.pgn_headers import insert_or_replace_headers, upsert_headers
from ..core.text_insertion import build_board_move_insertion
from ..core.text_ops import (
    OperationResult,
    TextEdit,
    check_balance,
    insert_comment,
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
from ..services.preferences_service import LOCALE_EXAMPLES, LOCALE_NAME, LOCALES, THEME_DARK
from .nag_bar import NagBar
from .pgn_tree_panel import PgnTreePanel
from .theme import color_for_depth, palette_of

# O dialogo, o rodape e a exportacao leem da **mesma** tabela. Eram tres
# listas independentes, e as tres estavam incompletas de um jeito diferente:
# faltava `+-`/`-+` numa, compensacao noutra, ataque na terceira.
SYMBOL_PALETTE: Sequence[tuple[str, str, str]] = tuple(
    (nag.insert_text, f"{nag.code}/{nag.black_code}" if nag.black_code else nag.code, nag.name) for nag in NAG_TABLE
)


class SymbolPaletteDialog(QDialog):
    def __init__(self, insert_symbol: Callable[[str], None], parent: QWidget | None = None):
        super().__init__(parent)
        self._insert_symbol = insert_symbol

        self.setWindowTitle("Paleta de Símbolos")
        self.setModal(False)
        self.resize(420, 320)

        layout = QVBoxLayout(self)
        intro = QLabel("Clique em um símbolo para inseri-lo no cursor do Text Editor.")
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

        note = QLabel("No PGN salvo, os símbolos são convertidos para o NAG canonico correspondente.")
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
    # Em livros extensos, colorir todos os delimitadores na abertura cria
    # centenas de milhares de ranges. A partida ativa é suficiente para editar;
    # as outras entram no cache quando o cursor chega nelas.
    LAZY_DELIMITER_TEXT_CHARS = 500_000

    text_parsed_signal = Signal(object)
    document_analyzed_signal = Signal(object)
    cursor_position_changed_signal = Signal(int)
    #: Recado de um gesto que partiu **de dentro** do painel -- da arvore, por
    #: exemplo. Os gestos que a janela dispara devolvem a mensagem no retorno.
    status_message_signal = Signal(str)
    #: O usuario trocou o idioma do texto na caixa do cabecalho.
    notation_locale_changed_signal = Signal(str)

    def __init__(self):
        super().__init__()

        self.locale = "pt"
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
        self._delimiter_pairs: dict[int, int] = {}
        self._delimiter_cache: dict[tuple[str, str], tuple[list[tuple[int, int, int]], dict[int, int]]] = {}
        self._parse_schedule_timer = QTimer(self)
        self._parse_schedule_timer.setSingleShot(True)
        self._parse_schedule_timer.timeout.connect(self._dispatch_scheduled_parse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        lbl = QLabel("Text Editor (Raw PDF Text)")
        lbl.setStyleSheet("font-weight: bold;")

        btn_clean = QPushButton("Join Lines")
        btn_clean.setToolTip("Removes awkward newlines from PDF copy")
        btn_clean.clicked.connect(self.action_join_lines)

        btn_palette = QPushButton("Paleta de Símbolos")
        btn_palette.setToolTip("Insere sinais de avaliação no cursor do editor")
        btn_palette.clicked.connect(self.open_symbol_palette)

        btn_headers = QPushButton("Inserir Headers")
        btn_headers.setToolTip("Insere ou atualiza os headers PGN padrão no topo do editor")
        btn_headers.clicked.connect(self.insert_default_headers)

        # Idioma do texto a vista, e nao so nas Preferencias: e a primeira coisa
        # a acertar ao colar um livro novo -- um livro espanhol lido como
        # portugues perde todo lance de bispo (`A5C`) sem dizer por que.
        lbl_locale = QLabel("Idioma do texto:")
        self.cmb_notation_locale = QComboBox()
        for code in LOCALES:
            self.cmb_notation_locale.addItem(LOCALE_NAME[code], code)
            self.cmb_notation_locale.setItemData(
                self.cmb_notation_locale.count() - 1, LOCALE_EXAMPLES[code], Qt.ItemDataRole.ToolTipRole
            )
        self.cmb_notation_locale.setToolTip(
            "Idioma da notação do livro: letras das peças e notação descritiva.\n"
            "Vale para este documento e é gravado no projeto; a análise é refeita na hora."
        )
        lbl_locale.setBuddy(self.cmb_notation_locale)
        self._sync_locale_box()
        self.cmb_notation_locale.currentIndexChanged.connect(self._on_locale_box_changed)

        # Sugestao, nunca troca sozinha: o texto colado parece de outro idioma.
        self.btn_locale_hint = QPushButton()
        self.btn_locale_hint.setVisible(False)
        self.btn_locale_hint.setStyleSheet("color: #d08a00; font-weight: bold;")
        self.btn_locale_hint.clicked.connect(self._accept_locale_hint)
        self._suggested_locale: str | None = None
        self._dismissed_suggestion: str | None = None
        self._hint_checked_length = -1

        toolbar.addWidget(lbl)
        toolbar.addStretch()
        toolbar.addWidget(lbl_locale)
        toolbar.addWidget(self.cmb_notation_locale)
        toolbar.addWidget(self.btn_locale_hint)
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
        # A arvore de lances mora **aqui**, e nao do outro lado da janela: a
        # pergunta que ela responde ("em que variante estou?") aparece
        # enquanto se digita, e olhar para o outro extremo da tela a cada
        # duvida custa mais do que a resposta vale.
        self.pgn_tree = PgnTreePanel()
        self.pgn_tree.move_selected_signal.connect(self.focus_span)
        self.pgn_tree.comment_paste_requested_signal.connect(self._on_tree_comment_requested)

        self.body_splitter = QSplitter(Qt.Orientation.Vertical)
        self.body_splitter.addWidget(self.text_edit)
        self.body_splitter.addWidget(self.pgn_tree)
        self.body_splitter.setStretchFactor(0, 3)
        self.body_splitter.setStretchFactor(1, 2)
        self.body_splitter.setSizes([460, 260])
        layout.addWidget(self.body_splitter, 1)

        # Rodape de simbolos: sempre visivel, porque anotar e o gesto mais
        # repetido da transcricao e abrir um dialogo para cada `!` custa mais
        # do que o simbolo vale.
        self.nag_bar = NagBar()
        self.nag_bar.symbol_requested_signal.connect(self.insert_symbol_at_cursor)
        layout.addWidget(self.nag_bar)

        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.cursorPositionChanged.connect(self._on_cursor_position_changed)

    def toggle_pgn_tree(self) -> bool:
        visible = not self.pgn_tree.isVisibleTo(self)
        self.pgn_tree.setVisible(visible)
        return visible

    def toggle_nag_bar(self) -> bool:
        # `isVisibleTo`, e nao `isVisible`: com a janela ainda nao mostrada --
        # ao restaurar o layout, e nos testes -- `isVisible` e sempre falso e
        # o interruptor so ligava.
        visible = not self.nag_bar.isVisibleTo(self)
        self.nag_bar.setVisible(visible)
        return visible

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
        self._parse_schedule_timer.stop()
        self.text_edit.setPlainText(text)
        cursor = self.text_edit.textCursor()
        cursor.setPosition(max(0, min(cursor_position, len(text))))
        self.text_edit.setTextCursor(cursor)
        self.text_edit.document().setModified(False)
        self._dismissed_suggestion = None
        self._update_locale_hint(text)
        # Uma vez, ao abrir: e o que faz o livro inteiro nascer realcado e com a
        # lista de partidas preenchida. Depois disso so a partida editada e
        # reanalisada.
        self.analyze_whole_book()

    def replace_all_text(self, new_text: str):
        """Substitui todo o texto **preservando o historico de desfazer**.

        Usar sempre isto no lugar de `setPlainText` para transformacoes do
        proprio usuario: `setPlainText` apaga a pilha de Ctrl+Z e joga o cursor
        para o inicio, o que ja custou trabalho perdido em capítulos grandes.
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
        clean_text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
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
        # aplicado se ainda corresponder ao texto que esta na tela. Nao copie o
        # documento aqui: `toPlainText()` seria O(tamanho do livro) por tecla.
        interval = self.async_parser.FAST_DEBOUNCE_MS
        if self.text_edit.document().characterCount() > self.async_parser.SMALL_DOCUMENT_BYTES:
            interval = self.async_parser.SLOW_DEBOUNCE_MS
        self._parse_schedule_timer.start(interval)

    def _dispatch_scheduled_parse(self):
        text = self.text_edit.toPlainText()
        # Uma colagem muda o tamanho de uma vez; digitar, nao. So a colagem
        # merece olhar o idioma de novo.
        if abs(len(text) - self._hint_checked_length) > 200:
            self._update_locale_hint(text)
        self.async_parser.schedule(text, self.cursor_position())

    def _on_cursor_position_changed(self):
        position = self.text_edit.textCursor().position()

        # Ao entrar noutra partida do livro, o tabuleiro e o preview passam a
        # segui-la. Vem do cache, entao não há custo de analise aqui.
        if self.analysis.segments and self.analysis.active_index != self._segment_index_at(position):
            self._do_reparse()

        # A arvore segue o cursor mesmo quando a sincronia com o tabuleiro
        # esta suspensa: quem suspendeu foi um `focus_span`, e nesse caso o
        # cursor acabou de ir para o lance que se quer ver marcado ali.
        self.pgn_tree.select_at(position)

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
        self.pgn_tree.set_game(self.current_ast)
        self.pgn_tree.select_at(self.cursor_position())
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
        self.async_parser.configure(
            ParseConfig.build(
                locale=changes.get("locale", config.locale),
                dictionary=changes.get("dictionary", config.dictionary_map),
                ignored_tokens=changes.get("ignored_tokens", config.ignored_tokens),
                separator_pattern=changes.get("separator_pattern", config.separator_pattern),
            )
        )
        self.reparse_now()

    def configure_recognition(self, locale: str, dictionary: dict[str, str], ignored_tokens):
        """Idioma, dicionario e silenciados de uma vez -- uma unica reanalise."""
        self.locale = locale
        self._sync_locale_box()
        self._reconfigure(locale=locale, dictionary=dictionary, ignored_tokens=ignored_tokens)

    def set_locale(self, locale: str):
        if locale == self.locale and locale == self.async_parser.config.locale:
            self._sync_locale_box()
            return
        self.locale = locale
        self._sync_locale_box()
        if self._suggested_locale == locale:
            self._show_locale_hint(None)
        self._reconfigure(locale=locale)

    def _sync_locale_box(self) -> None:
        """A caixa mostra o idioma em uso, sem disparar a troca de volta."""
        index = self.cmb_notation_locale.findData(self.locale)
        if index < 0 or index == self.cmb_notation_locale.currentIndex():
            return
        self.cmb_notation_locale.blockSignals(True)
        self.cmb_notation_locale.setCurrentIndex(index)
        self.cmb_notation_locale.blockSignals(False)

    def _on_locale_box_changed(self, _index: int) -> None:
        locale = self.cmb_notation_locale.currentData()
        if not locale or locale == self.locale:
            return
        if self._suggested_locale and locale != self._suggested_locale:
            # O usuario escolheu outro idioma com a sugestao na tela: nao insistir.
            self._dismissed_suggestion = self._suggested_locale
        self.set_locale(locale)
        self._show_locale_hint(None)
        self.notation_locale_changed_signal.emit(locale)

    def _update_locale_hint(self, text: str) -> None:
        self._hint_checked_length = len(text)
        detected = detect_notation_locale(text)
        if detected == self._dismissed_suggestion:
            detected = None
        self._show_locale_hint(detected if detected != self.locale else None)

    def _show_locale_hint(self, locale: str | None) -> None:
        self._suggested_locale = locale
        if locale is None:
            self.btn_locale_hint.setVisible(False)
            return
        name = LOCALE_NAME.get(locale, locale)
        self.btn_locale_hint.setText(f"Parece {name} — usar")
        self.btn_locale_hint.setToolTip(
            f"Os lances deste texto têm cara de notação {name.lower()} ({LOCALE_EXAMPLES[locale]}). "
            "Clique para ler o texto nesse idioma."
        )
        self.btn_locale_hint.setVisible(True)

    def _accept_locale_hint(self) -> None:
        if self._suggested_locale:
            self.cmb_notation_locale.setCurrentIndex(self.cmb_notation_locale.findData(self._suggested_locale))

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

    # ------------------------------------------------------------------
    # Comentario no lance escolhido
    # ------------------------------------------------------------------

    def move_by_node_id(self, node_id: str) -> MoveNode | None:
        """O lance a que um `node_id` da arvore se refere.

        Os `node_id` da arvore do PGN, do preview e da anotacao visual saem
        todos da mesma convencao (`move_tree.build_move_entries`), e e por isso
        que este caminho existe uma vez so.
        """
        by_node_id, _by_identity = build_entry_maps(build_move_entries(self.current_ast))
        entry = by_node_id.get(node_id)
        return entry.node if entry else None

    def paste_comment_on_move(self, body: str, *, before: bool, node_id: str | None = None) -> str:
        """Cola prosa como comentario de um lance -- o da arvore, ou o do cursor."""
        move = self.move_by_node_id(node_id) if node_id else self.move_at_cursor()
        if move is None:
            return "Escolha o lance na árvore, ou ponha o cursor nele, para colar o comentário."
        return self.apply_operation(insert_comment(self.document_text(), move, body, before=before))

    def action_paste_comment(self, before: bool, node_id: str | None = None) -> str:
        """Ctrl+Alt+A / Ctrl+Alt+D -- a area de transferencia vira comentario."""
        return self.paste_comment_on_move(QGuiApplication.clipboard().text(), before=before, node_id=node_id)

    def _on_tree_comment_requested(self, node_id: str, before: bool):
        self.status_message_signal.emit(self.action_paste_comment(before, node_id))

    def action_renumber(self) -> str:
        """Ctrl+R -- renumera o **livro inteiro**, nao so a partida sob o cursor."""
        text = self.document_text()
        edits: list[TextEdit] = []
        for analysis in self.analyze_whole_book():
            edits.extend(renumber_document(text, analysis.ast).edits)

        if not edits:
            return "A numeracao já esta correta."
        return self.apply_operation(OperationResult(edits, f"{len(edits)} número(s) de lance corrigido(s)."))

    def action_indent_variations(self) -> str:
        """Ctrl+T -- uma variante por linha, recuada pela profundidade."""
        return self.apply_operation(indent_variations(self.document_text(), self.segments()))

    def action_flatten_variations(self) -> str:
        """Ctrl+Shift+T -- o inverso, para voltar ao movetext corrido."""
        return self.apply_operation(flatten_variations(self.document_text(), self.segments()))

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
            return "Os cabeçalhos já estao assim."

        start, end, block = change
        return self.apply_operation(OperationResult([TextEdit(start, end, block)], "Cabeçalhos atualizados."))

    def action_convert_book_text(self, options: BookImportOptions | None = None, converted: str | None = None) -> str:
        """Converte o documento (ou a selecao) de texto de livro em movetext.

        Se `converted` vier pronto -- e vem, quando o dialogo ja mostrou a
        previa --, ele e usado como esta: reconverter aqui poderia dar um
        resultado diferente do que o usuario aprovou.
        """
        start, end = self._selection()
        has_selection = end > start
        source = self.document_text()[start:end] if has_selection else self.document_text()

        if converted is None:
            converted = convert_book_text(source, options).text
        if not converted or converted == source:
            return "Nada a converter."

        if has_selection:
            self.replace_span(start, end, converted)
        else:
            self.replace_all_text(converted)
        self.reparse_now()
        return "Texto de livro convertido."

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
        self._parse_schedule_timer.stop()
        self.async_parser.cancel_pending()
        self._do_reparse()
        return self.current_ast

    def _make_selection(
        self, start_index: int, end_index: int, text_format: QTextCharFormat
    ) -> QTextEdit.ExtraSelection:
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

        touched = {
            ("fixed", False): fmt_fixed,
            ("fixed", True): fmt_fixed_variation,
            ("review", False): fmt_review,
            ("review", True): fmt_review_variation,
            ("error", False): fmt_error,
            ("error", True): fmt_error_variation,
        }

        # Um formato por profundidade, criado sob demanda: um livro tem quatro
        # ou cinco niveis, nao trinta, e construir tudo de antemao seria gastar
        # com o que nao aparece.
        valid_by_depth: dict[int, QTextCharFormat] = {}

        def valid_format(depth: int) -> QTextCharFormat:
            if depth not in valid_by_depth:
                text_format = QTextCharFormat()
                if self.review_mode:
                    # Dupla visao (SPEC 5, fase 5): a pergunta deixa de ser "o
                    # que e lance?" e passa a ser "onde o programa **mexeu**?".
                    # Tudo que ele apenas confirmou apaga -- inclusive a cor de
                    # profundidade, que aqui so atrapalharia.
                    text_format.setForeground(QColor(colors["confirmed_dimmed"]))
                else:
                    text_format.setForeground(QColor(color_for_depth(self.theme, depth)))
                    # So a linha principal em negrito: e o fio que se segue
                    # quando os olhos passam pela pagina.
                    text_format.setFontWeight(QFont.Weight.Bold if depth == 0 else QFont.Weight.Normal)
                valid_by_depth[depth] = text_format
            return valid_by_depth[depth]

        # Corrigido, duvidoso e nao entendido mantem a **sua** cor, e nao a da
        # profundidade: "o programa mexeu aqui" vale mais do que "isto e uma
        # sub-variante", e o recuo do texto continua dizendo a profundidade.
        ranges = [
            (start, end, valid_format(depth) if kind == "valid" else touched[(kind, depth > 0)])
            for start, end, kind, depth in analysis.highlight_spans
        ]
        delimiter_segments = analysis.segments
        if len(analysis.raw_text) > self.LAZY_DELIMITER_TEXT_CHARS and analysis.segments:
            delimiter_segments = [analysis.segments[analysis.active_index]]
        delimiter_ranges, self._delimiter_pairs = _delimiter_highlight_data(
            analysis.raw_text, delimiter_segments, self.theme, self._delimiter_cache
        )
        ranges.extend(delimiter_ranges)
        self.highlighter.set_ranges(ranges)
        self._apply_current_selection()

    # ------------------------------------------------------------------

    def _renumber_pasted_movetext(self, text: str) -> str:
        """Renumera o trecho colado a partir da posicao do tabuleiro no cursor."""
        if "\n" in text.strip() or not text.strip():
            # Colagem de bloco (uma pagina inteira, por exemplo) nao e uma linha
            # de lances: não há posicao unica de onde renumerar.
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
        self.pgn_tree.set_theme(theme)
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
        for _start, _end, kind, _depth in self.analysis.highlight_spans:
            key = names.get(kind)
            if key:
                counts[key] += 1
        return counts

    def _apply_current_selection(self):
        """Realce o lance e o delimitador correspondente sob o cursor."""
        selections: list[QTextEdit.ExtraSelection] = []

        if self.current_move_span:
            fmt_current = QTextCharFormat()
            fmt_current.setBackground(QColor(palette_of(self.theme)["current_move"]))
            start_index, end_index = self.current_move_span
            selections.append(self._make_selection(start_index, end_index, fmt_current))

        delimiter = self._delimiter_at_cursor()
        if delimiter is not None:
            colors = palette_of(self.theme)
            fmt_delimiter = QTextCharFormat()
            fmt_delimiter.setBackground(QColor(colors["current_move"]))
            fmt_delimiter.setForeground(QColor(colors["text"]))
            fmt_delimiter.setFontWeight(QFont.Weight.Bold)
            selections.append(self._make_selection(delimiter, delimiter + 1, fmt_delimiter))

            pair = self._delimiter_pairs.get(delimiter)
            if pair is not None:
                selections.append(self._make_selection(pair, pair + 1, fmt_delimiter))

        self.text_edit.setExtraSelections(selections)

    def _delimiter_at_cursor(self) -> int | None:
        position = self.cursor_position()
        text = self.document_text()
        for candidate in (position, position - 1):
            if 0 <= candidate < len(text) and text[candidate] in "(){}":
                return candidate
        return None


def _delimiter_highlight_data(raw_text: str, segments: Sequence, theme: str, cache: dict):
    """Retorna realce e pares de `{}`, `()` apenas no movetext das partidas."""
    ranges: list[tuple[int, int, QTextCharFormat]] = []
    pairs: dict[int, int] = {}

    for segment in segments:
        cache_key = (segment.body, theme)
        cached = cache.get(cache_key)
        if cached is not None:
            cached_ranges, cached_pairs = cached
            offset = segment.raw_offset
            ranges.extend((start + offset, end + offset, text_format) for start, end, text_format in cached_ranges)
            pairs.update({start + offset: end + offset for start, end in cached_pairs.items()})
            continue

        stack: list[tuple[str, int]] = []
        in_comment = False
        start = segment.raw_offset
        end = segment.body_end_offset
        segment_ranges: list[tuple[int, int, QTextCharFormat]] = []
        segment_pairs: dict[int, int] = {}

        for position in range(start, min(end, len(raw_text))):
            char = raw_text[position]
            if in_comment:
                if char == "}":
                    in_comment = False
                    segment_ranges.append(
                        (position - start, position - start + 1, _delimiter_format(theme, len(stack)))
                    )
                    if stack and stack[-1][0] == "{":
                        _opening, opening_position = stack.pop()
                        segment_pairs[opening_position - start] = position - start
                        segment_pairs[position - start] = opening_position - start
                continue

            if char == "{":
                in_comment = True
                stack.append((char, position))
                segment_ranges.append(
                    (position - start, position - start + 1, _delimiter_format(theme, len(stack) - 1))
                )
            elif char == "(":
                stack.append((char, position))
                segment_ranges.append(
                    (position - start, position - start + 1, _delimiter_format(theme, len(stack) - 1))
                )
            elif char == ")":
                depth = max(0, len(stack) - 1)
                segment_ranges.append((position - start, position - start + 1, _delimiter_format(theme, depth)))
                if stack and stack[-1][0] == "(":
                    _opening, opening_position = stack.pop()
                    segment_pairs[opening_position - start] = position - start
                    segment_pairs[position - start] = opening_position - start

        cache[cache_key] = (segment_ranges, segment_pairs)
        ranges.extend(
            (relative_start + start, relative_end + start, text_format)
            for relative_start, relative_end, text_format in segment_ranges
        )
        pairs.update(
            {relative_start + start: relative_end + start for relative_start, relative_end in segment_pairs.items()}
        )

    return ranges, pairs


@lru_cache(maxsize=32)
def _delimiter_format(theme: str, depth: int) -> QTextCharFormat:
    text_format = QTextCharFormat()
    # A cor do delimitador acompanha a mesma escala visual das variantes.
    text_format.setForeground(QColor(color_for_depth(theme, depth)))
    return text_format


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

        # `rehighlight()` percorre todos os blocos do documento. Em um livro
        # grande, porém, quase sempre só uma pequena parte dos ranges mudou
        # depois de uma tecla. Reaplicar apenas os blocos cujo índice mudou
        # mantém a formatação incremental e evita congelar o editor ao fim do
        # debounce do parser.
        changed_blocks = set(self._ranges_by_block) | set(indexed)
        changed_blocks = {
            block_number
            for block_number in changed_blocks
            if self._ranges_by_block.get(block_number) != indexed.get(block_number)
        }
        self._ranges_by_block = indexed

        for block_number in changed_blocks:
            block = document.findBlockByNumber(block_number)
            if block.isValid():
                self.rehighlightBlock(block)

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
