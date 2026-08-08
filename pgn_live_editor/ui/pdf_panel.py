"""Painel de PDF lado a lado (SPEC 5.6).

O fluxo que este painel existe para encurtar e sempre o mesmo: olhar a pagina,
copiar um pedaco, voltar ao editor, colar, marcar. Tirar o alt-tab dessa conta
e o ganho.

Uma limitacao do toolkit decide o desenho: o `QPdfView` do Qt **desenha** a
pagina mas nao deixa selecionar texto nela. Entao o painel mostra as duas
coisas -- a pagina renderizada em cima, o texto extraido dela embaixo -- e a
selecao acontece no texto. Na pratica isso e melhor do que parece: o texto ja
vem extraido, que e exatamente o que se quer mandar para o editor, e da para
conferir na imagem se o extrator comeu alguma coisa.

`Ctrl+Enter` manda a selecao (ou a pagina inteira, se nao houver selecao) para
o cursor do editor. O botao *Proxima pagina* avanca e ja anexa, para percorrer
um capítulo sem sair do teclado.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QPointF, Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

PDF_FILTER = "Documentos PDF (*.pdf);;Todos os arquivos (*)"


class PdfPanel(QWidget):
    text_requested_signal = Signal(str)
    page_changed_signal = Signal(int)
    document_opened_signal = Signal(str)

    def __init__(self):
        super().__init__()

        self.document = QPdfDocument(self)
        self._buffer: QBuffer | None = None
        self._path: Path | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Abrir PDF...")
        self.btn_open.clicked.connect(self.choose_pdf)

        self.spn_page = QSpinBox()
        self.spn_page.setMinimum(1)
        self.spn_page.setMaximum(1)
        self.spn_page.setPrefix("pag. ")
        self.spn_page.valueChanged.connect(self._on_page_spin_changed)

        self.lbl_pages = QLabel("- / -")

        bar.addWidget(self.btn_open)
        bar.addWidget(self.spn_page)
        bar.addWidget(self.lbl_pages)
        bar.addStretch()
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        splitter.addWidget(self.view)

        self.txt_page = QPlainTextEdit()
        self.txt_page.setReadOnly(True)
        self.txt_page.setPlaceholderText(
            "O texto da página aparece aqui. Selecione o trecho e mande para o "
            "editor com Ctrl+Enter."
        )
        splitter.addWidget(self.txt_page)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        actions = QHBoxLayout()
        self.btn_send = QPushButton("Mandar ao cursor (Ctrl+Enter)")
        self.btn_send.clicked.connect(self.send_to_editor)
        self.btn_next = QPushButton("Próxima página e anexar")
        self.btn_next.setToolTip("Manda a página inteira e avanca -- para percorrer um capítulo sem sair do teclado")
        self.btn_next.clicked.connect(self.append_and_advance)
        actions.addWidget(self.btn_send)
        actions.addWidget(self.btn_next)
        layout.addLayout(actions)

        for widget in (self.txt_page, self.view):
            shortcut = QShortcut(QKeySequence("Ctrl+Return"), widget)
            shortcut.activated.connect(self.send_to_editor)

        self._update_enabled()

    # ------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def page_count(self) -> int:
        return self.document.pageCount()

    def current_page(self) -> int:
        """Indice da pagina, base zero -- e como o resto do programa conta."""
        return self.spn_page.value() - 1

    def has_document(self) -> bool:
        return self._path is not None and self.page_count > 0

    # ------------------------------------------------------------------

    def choose_pdf(self):
        file_path, _filter = QFileDialog.getOpenFileName(self, "Abrir PDF do livro", str(Path.cwd()), PDF_FILTER)
        if file_path:
            self.open_pdf(Path(file_path))

    def open_pdf(self, path: Path, page: int = 0) -> str:
        """Abre o PDF e vai para `page`. Devolve mensagem de erro, ou vazio.

        O arquivo e lido **para a memoria** e o leitor trabalha sobre isso. Nao
        e detalhe: carregando por caminho, o Qt prende o arquivo enquanto o
        programa estiver aberto -- e nao o solta nem no `close()`. O livro do
        usuario ficaria travado para mover, sincronizar ou abrir noutro leitor
        pelo resto da sessao. O preco e o PDF ocupar memoria enquanto estiver
        aberto, o que e barato perto disso.
        """
        try:
            payload = path.read_bytes()
        except OSError as exc:
            self.close_pdf()
            return f"Não foi possível ler {path.name}: {exc.strerror or exc}."

        buffer = QBuffer(self)
        buffer.setData(QByteArray(payload))
        buffer.open(QBuffer.OpenModeFlag.ReadOnly)

        self.document.load(buffer)
        if self.document.error() != QPdfDocument.Error.None_ or self.document.pageCount() <= 0:
            self.close_pdf()
            return f"Não foi possível abrir {path.name}: o arquivo não parece um PDF válido."

        # O leitor le do buffer enquanto o documento existir: ele precisa
        # sobreviver a esta funcao.
        self._buffer = buffer
        self._path = path
        self.spn_page.setMaximum(max(1, self.page_count))
        self.go_to_page(page)
        self._update_enabled()
        self.document_opened_signal.emit(str(path))
        return ""

    def close_pdf(self):
        self.document.close()
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
        self._path = None
        self.txt_page.clear()
        self._update_enabled()

    def go_to_page(self, page: int):
        target = max(0, min(page, max(0, self.page_count - 1)))
        self._loading = True
        self.spn_page.setValue(target + 1)
        self._loading = False
        self._apply_page(target)

    def _on_page_spin_changed(self, value: int):
        if not self._loading:
            self._apply_page(value - 1)

    def _apply_page(self, page: int):
        if not self.has_document():
            return

        navigator = self.view.pageNavigator()
        if navigator is not None:
            navigator.jump(page, QPointF())

        self.txt_page.setPlainText(self.page_text(page))
        self.lbl_pages.setText(f"{page + 1} / {self.page_count}")
        self.page_changed_signal.emit(page)

    def page_text(self, page: int) -> str:
        """Texto extraido de uma pagina, ja sem as quebras duplas do extrator."""
        if not self.has_document() or not 0 <= page < self.page_count:
            return ""
        selection = self.document.getAllText(page)
        return selection.text() if selection is not None else ""

    # ------------------------------------------------------------------

    def selected_text(self) -> str:
        """A selecao do usuario, ou a pagina inteira quando não há selecao."""
        chosen = self.txt_page.textCursor().selectedText()
        if chosen:
            # O Qt usa separador de parágrafo proprio na selecao.
            return chosen.replace(" ", "\n")
        return self.txt_page.toPlainText()

    def send_to_editor(self):
        text = self.selected_text().strip()
        if text:
            self.text_requested_signal.emit(text)

    def append_and_advance(self):
        if not self.has_document():
            return

        text = self.txt_page.toPlainText().strip()
        if text:
            self.text_requested_signal.emit(text)

        if self.current_page() + 1 < self.page_count:
            self.go_to_page(self.current_page() + 1)

    # ------------------------------------------------------------------

    def _update_enabled(self):
        has_document = self.has_document()
        for widget in (self.spn_page, self.btn_send, self.btn_next):
            widget.setEnabled(has_document)
        if not has_document:
            self.lbl_pages.setText("- / -")
