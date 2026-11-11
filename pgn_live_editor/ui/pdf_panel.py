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

Pagina escaneada nao tem texto nenhum para extrair. Nela o painel passa a pagina
pelo OCR do Windows (`services/text_ocr.py`) sozinho, numa thread a parte, e o
resultado entra no mesmo lugar do texto extraido -- o resto do fluxo nao muda.

`Ctrl+Enter` manda a selecao (ou a pagina inteira, se nao houver selecao) para
o cursor do editor. O botao *Proxima pagina* avanca e ja anexa, para percorrer
um capítulo sem sair do teclado.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QEvent, QPointF, QSize, Qt
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtGui import QColor, QImage, QKeySequence, QMouseEvent, QPainter, QShortcut
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..services.text_ocr import OCR_DPI, OCR_DPIS, recognize_renders, text_ocr_available
from ..services.worker_host import JobContext, WorkerFailed, WorkerHost

PDF_FILTER = "Documentos PDF (*.pdf);;Todos os arquivos (*)"

SCANNED_PAGE_HINT = "Página sem camada de texto (escaneada)."


def read_page_text(payload: tuple[int, list], context: JobContext) -> tuple[int, str]:
    """OCR de uma pagina ja desenhada (bytes); roda na thread do host, nunca toca em widget."""
    page, renders = payload
    return page, recognize_renders(renders).text


class PdfPageView(QPdfView):
    """QPdfView com gesto explícito para selecionar um diagrama visual."""

    diagram_double_clicked_signal = Signal(object)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.diagram_double_clicked_signal.emit(event.position())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PdfPanel(QWidget):
    text_requested_signal = Signal(str)
    #: O mesmo texto, mas passando antes pelo conversor de livro (rotulos de
    #: variante, colchetes, prosa) com previa. E o caminho normal para uma
    #: pagina de livro de repertorio; o outro serve para um trecho solto.
    book_text_requested_signal = Signal(str)
    page_changed_signal = Signal(int)
    document_opened_signal = Signal(str)
    diagram_ocr_requested_signal = Signal()
    full_pdf_recognition_requested_signal = Signal()
    full_pdf_recognition_cancel_requested_signal = Signal()
    diagram_ocr_cancel_requested_signal = Signal()
    diagram_review_queue_requested_signal = Signal()
    diagram_clicked_signal = Signal(int, float, float)
    #: Mensagem da leitura de intervalo para a barra de status.
    range_status_signal = Signal(str)

    def __init__(self):
        super().__init__()

        self.document = QPdfDocument(self)
        self._buffer: QBuffer | None = None
        self._path: Path | None = None
        self._loading = False
        # OCR de texto: pagina -> texto lido, e o pedido em andamento.
        self._text_ocr_available = text_ocr_available()
        self._ocr_cache: dict[int, str] = {}
        # Dois hosts (R-303): a pagina da tela e o intervalo nao disputam a
        # mesma fila -- antes dividiam uma thread com ids negativos para um deles.
        self._page_ocr = WorkerHost(read_page_text, self)
        self._page_ocr.finished_signal.connect(self._on_page_ocr_finished)
        self._page_ocr.failed_signal.connect(self._on_page_ocr_failed)
        self._range_ocr = WorkerHost(read_page_text, self)
        self._range_ocr.finished_signal.connect(self._on_range_ocr_finished)
        self._range_ocr.failed_signal.connect(self._on_range_ocr_failed)
        self._ocr_pending_page: int | None = None
        self._range_page: int | None = None
        self._append_when_read: int | None = None
        # Leitura de um intervalo de paginas: fila e texto ja lido.
        self._range_queue: list[int] = []
        self._range_blocks: list[str] = []
        self._range_total = 0

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
        self.spn_start = QSpinBox()
        self.spn_start.setMinimum(1)
        self.spn_start.setMaximum(1)
        self.spn_start.setPrefix("de ")
        self.spn_end = QSpinBox()
        self.spn_end.setMinimum(1)
        self.spn_end.setMaximum(1)
        self.spn_end.setPrefix("até ")

        bar.addWidget(self.btn_open)
        bar.addWidget(self.spn_page)
        bar.addWidget(self.lbl_pages)
        bar.addWidget(self.spn_start)
        bar.addWidget(self.spn_end)
        bar.addStretch()
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.view = PdfPageView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.SinglePage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.view.diagram_double_clicked_signal.connect(self._on_diagram_double_click)
        navigator = self.view.pageNavigator()
        if navigator is not None:
            navigator.currentPageChanged.connect(self._on_view_page_changed)
        # QPdfView encaminha os eventos do desenho para este filho, não para o
        # próprio QPdfView. O filtro é necessário para o duplo clique funcionar
        # sobre a página renderizada em todas as versões do Qt 6.
        self.view.viewport().installEventFilter(self)
        splitter.addWidget(self.view)

        self.txt_page = QPlainTextEdit()
        self.txt_page.setReadOnly(True)
        self.txt_page.setPlaceholderText(
            "O texto da página aparece aqui. Selecione o trecho e mande para o editor com Ctrl+Enter."
        )
        splitter.addWidget(self.txt_page)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self.full_progress = QProgressBar()
        self.full_progress.setRange(0, 100)
        self.full_progress.setValue(0)
        self.full_progress.setFormat("Reconhecimento integral: %p%")
        self.full_progress.setVisible(False)
        layout.addWidget(self.full_progress)

        ocr_actions = QHBoxLayout()
        actions = QHBoxLayout()
        self.btn_convert = QPushButton("Converter e mandar (Ctrl+Shift+Enter)")
        self.btn_convert.setToolTip(
            "Passa a seleção pelo conversor de livro antes de inserir: rótulos A) B1) viram\n"
            "variantes, colchetes viram variantes, a prosa vira comentário. Mostra a prévia antes."
        )
        self.btn_convert.clicked.connect(self.convert_to_editor)
        self.btn_send = QPushButton("Mandar cru (Ctrl+Enter)")
        self.btn_send.clicked.connect(self.send_to_editor)
        self.btn_next = QPushButton("Próxima página e anexar")
        self.btn_next.setToolTip("Manda a página inteira e avanca -- para percorrer um capítulo sem sair do teclado")
        self.btn_next.clicked.connect(self.append_and_advance)
        self.btn_ocr = QPushButton("Ler todos os diagramas")
        self.btn_ocr.setToolTip("Detecta todos os diagramas da página e gera posições FEN para o editor.")
        self.btn_ocr.clicked.connect(self.diagram_ocr_requested_signal.emit)
        self.btn_full_recognition = QPushButton("Reconhecer PDF inteiro")
        self.btn_full_recognition.setToolTip("Carrega todo o texto e os headers/FEN dos diagramas no editor.")
        self.btn_full_recognition.clicked.connect(self.full_pdf_recognition_requested_signal.emit)
        self.btn_cancel_full_recognition = QPushButton("Cancelar reconhecimento")
        self.btn_cancel_full_recognition.clicked.connect(self.full_pdf_recognition_cancel_requested_signal.emit)
        self.btn_cancel_full_recognition.setVisible(False)
        self.btn_cancel_ocr = QPushButton("Cancelar OCR")
        self.btn_cancel_ocr.clicked.connect(self.diagram_ocr_cancel_requested_signal.emit)
        self.btn_cancel_ocr.setVisible(False)
        self.btn_review_queue = QPushButton("Revisar pendências")
        self.btn_review_queue.setToolTip("Abre a fila de diagramas OCR que precisam de conferência.")
        self.btn_review_queue.clicked.connect(self.diagram_review_queue_requested_signal.emit)
        self.btn_columns = QPushButton("Ordenar colunas")
        self.btn_columns.setToolTip("Detecta blocos posicionais e permite escolher a ordem de leitura.")
        self.btn_columns.clicked.connect(self.reorder_columns)
        self.btn_text_ocr = QPushButton("Ler texto (OCR)")
        self.btn_text_ocr.setToolTip(
            "Lê o texto da página pela imagem, com o OCR do Windows.\n"
            "Acontece sozinho nas páginas escaneadas; aqui serve para reler uma página."
        )
        self.btn_text_ocr.clicked.connect(lambda: self.read_page_text_with_ocr(self.current_page(), force=True))
        self.btn_text_ocr.setVisible(self._text_ocr_available)
        self.btn_range_ocr = QPushButton("Ler intervalo (OCR)")
        self.btn_range_ocr.setToolTip(
            "Lê as páginas do intervalo 'de'/'até' e manda tudo para o cursor do editor,\n"
            "com uma marca { PDF página N } antes de cada página. Página com texto usa o\n"
            "texto; página escaneada passa pelo OCR. Clique de novo para cancelar."
        )
        self.btn_range_ocr.clicked.connect(self.toggle_range_reading)
        self.btn_range_ocr.setVisible(self._text_ocr_available)
        ocr_actions.addWidget(self.btn_text_ocr)
        ocr_actions.addWidget(self.btn_range_ocr)
        ocr_actions.addWidget(self.btn_ocr)
        ocr_actions.addWidget(self.btn_full_recognition)
        ocr_actions.addWidget(self.btn_cancel_full_recognition)
        ocr_actions.addWidget(self.btn_cancel_ocr)
        ocr_actions.addWidget(self.btn_review_queue)
        ocr_actions.addWidget(self.btn_columns)
        ocr_actions.addStretch()
        actions.addWidget(self.btn_convert)
        actions.addWidget(self.btn_send)
        actions.addWidget(self.btn_next)
        layout.addLayout(ocr_actions)
        layout.addLayout(actions)

        for widget in (self.txt_page, self.view):
            QShortcut(QKeySequence("Ctrl+Return"), widget).activated.connect(self.send_to_editor)
            QShortcut(QKeySequence("Ctrl+Shift+Return"), widget).activated.connect(self.convert_to_editor)

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

    def recognition_range(self) -> tuple[int, int]:
        start = min(self.spn_start.value(), self.spn_end.value())
        end = max(self.spn_start.value(), self.spn_end.value())
        return start - 1, end - 1

    # ------------------------------------------------------------------

    def choose_pdf(self):
        file_path, _filter = QFileDialog.getOpenFileName(self, "Abrir PDF do livro", str(Path.cwd()), PDF_FILTER)
        if file_path:
            self.open_pdf(Path(file_path))

    def reorder_columns(self):
        if not self.has_document():
            return
        try:
            import fitz

            from ..core.pdf_columns import PdfTextBlock, detect_columns
            from .pdf_columns_dialog import PdfColumnsDialog

            with fitz.open(str(self.path)) as document:
                blocks = []
                for block in document[self.current_page()].get_text("blocks"):
                    if len(block) >= 5 and str(block[4]).strip():
                        blocks.append(
                            PdfTextBlock(
                                str(block[4]),
                                float(block[0]),
                                float(block[1]),
                                float(block[2] - block[0]),
                                float(block[3] - block[1]),
                            )
                        )
            columns = detect_columns(blocks)
            if len(columns) < 2:
                self.txt_page.setPlainText("\n".join(block.text.strip() for block in blocks).strip())
                return
            dialog = PdfColumnsDialog(columns, self)
            if dialog.exec() == dialog.DialogCode.Accepted:
                self.txt_page.setPlainText(dialog.ordered_text())
        except (ImportError, OSError, ValueError):
            return

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
        self.spn_start.setMaximum(max(1, self.page_count))
        self.spn_end.setMaximum(max(1, self.page_count))
        self.spn_start.setValue(1)
        self.spn_end.setValue(max(1, self.page_count))
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
        self._ocr_cache.clear()
        # O OCR em andamento era da pagina de outro documento.
        self._page_ocr.cancel()
        self._ocr_pending_page = None
        self._append_when_read = None
        self._stop_range_reading()
        self.txt_page.clear()
        self.clear_full_progress()
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

    def _on_view_page_changed(self, page: int):
        """Mantém texto, seletor e página visual no mesmo índice."""
        if self._loading or not self.has_document():
            return
        if not 0 <= page < self.page_count:
            return
        self._loading = True
        self.spn_page.setValue(page + 1)
        self._loading = False
        self._apply_page(page)

    def _on_diagram_double_click(self, position: QPointF):
        """Converte o clique na página renderizada para pontos PDF."""
        if not self.has_document():
            return
        page = self.current_page()
        page_size = self.document.pagePointSize(page)
        viewport = self.view.viewport()
        if page_size.width() <= 0 or page_size.height() <= 0 or viewport.width() <= 0:
            return
        scale = viewport.width() / page_size.width()
        x = (position.x() + self.view.horizontalScrollBar().value()) / scale
        y = (position.y() + self.view.verticalScrollBar().value()) / scale
        if 0 <= x <= page_size.width() and 0 <= y <= page_size.height():
            self.diagram_clicked_signal.emit(page, float(x), float(y))

    def eventFilter(self, watched, event):
        if watched is self.view.viewport() and event.type() == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                self._on_diagram_double_click(event.position())
                return True
        return super().eventFilter(watched, event)

    def _apply_page(self, page: int):
        if not self.has_document():
            return

        navigator = self.view.pageNavigator()
        if navigator is not None:
            navigator.jump(page, QPointF())

        text = self.page_text(page)
        if not text.strip() and page in self._ocr_cache:
            text = self._ocr_cache[page]
        self.txt_page.setPlainText(text)
        if not text.strip():
            self._on_page_without_text(page)
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

    def convert_to_editor(self):
        text = self.selected_text().strip()
        if text:
            self.book_text_requested_signal.emit(text)

    def append_and_advance(self):
        if not self.has_document():
            return

        if self._ocr_pending_page == self.current_page():
            # A pagina ainda esta no OCR: o texto e mandado quando chegar.
            self._append_when_read = self.current_page()
        else:
            text = self.txt_page.toPlainText().strip()
            if text:
                self.text_requested_signal.emit(text)

        if self.current_page() + 1 < self.page_count:
            self.go_to_page(self.current_page() + 1)

    # ------------------------------------------------------------------
    # OCR de texto
    # ------------------------------------------------------------------

    def _on_page_without_text(self, page: int) -> None:
        if not self._text_ocr_available:
            self.txt_page.setPlaceholderText(
                f"{SCANNED_PAGE_HINT} Sem OCR de texto nesta máquina: instale o extra 'text-ocr' (Windows)."
            )
            return
        self.read_page_text_with_ocr(page)

    def render_page_bgra(self, page: int, dpi: int = OCR_DPI) -> tuple[bytes, int, int]:
        """A pagina em BGRA sobre fundo branco, na resolucao pedida."""
        points = self.document.pagePointSize(page)
        scale = dpi / 72
        size = QSize(max(1, int(points.width() * scale)), max(1, int(points.height() * scale)))
        rendered = self.document.render(page, size)
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.drawImage(0, 0, rendered)
        painter.end()
        bits = image.constBits()
        bits.setsize(image.sizeInBytes())
        return bytes(bits), image.width(), image.height()

    def read_page_text_with_ocr(self, page: int, force: bool = False) -> None:
        if not self.has_document() or not self._text_ocr_available or not 0 <= page < self.page_count:
            return
        if not force and page in self._ocr_cache:
            return
        self._ocr_pending_page = page
        self.txt_page.clear()
        self.txt_page.setPlaceholderText(f"{SCANNED_PAGE_HINT} Lendo a página {page + 1} com OCR...")
        self.btn_text_ocr.setEnabled(False)
        # O QPdfDocument so desenha na thread da interface; o OCR, que e o caro,
        # roda na outra. Mais de uma resolucao: ver `services/text_ocr.py`.
        renders = [(*self.render_page_bgra(page, dpi), dpi) for dpi in OCR_DPIS]
        self._page_ocr.submit((page, renders))

    # -- intervalo de paginas -------------------------------------------

    @property
    def is_reading_range(self) -> bool:
        return bool(self._range_total)

    def toggle_range_reading(self) -> None:
        if self.is_reading_range:
            self._stop_range_reading()
            self.range_status_signal.emit("Leitura do intervalo cancelada; nada foi mandado ao editor.")
            return
        self.read_range_with_ocr(*self.recognition_range())

    def read_range_with_ocr(self, start: int, end: int) -> None:
        """Le as paginas `start..end` (base 0) e manda o texto ao editor no fim."""
        if not self.has_document() or not self._text_ocr_available:
            return
        start, end = max(0, start), min(self.page_count - 1, end)
        if end < start:
            return
        self._range_queue = list(range(start, end + 1))
        self._range_blocks = []
        self._range_total = len(self._range_queue)
        self.btn_range_ocr.setText("Cancelar leitura")
        self._next_range_page()

    def _next_range_page(self) -> None:
        while self._range_queue:
            page = self._range_queue.pop(0)
            self.set_range_progress(self._range_total - len(self._range_queue), self._range_total)
            text = self.page_text(page).strip() or self._ocr_cache.get(page, "").strip()
            if text:
                self._range_blocks.append(self._range_block(page, text))
                continue
            self._range_page = page
            renders = [(*self.render_page_bgra(page, dpi), dpi) for dpi in OCR_DPIS]
            self._range_ocr.submit((page, renders))
            return
        self._finish_range_reading()

    @staticmethod
    def _range_block(page: int, text: str) -> str:
        return f"{{ PDF página {page + 1} }}\n{text}"

    def _on_range_ocr_finished(self, _request: int, result: tuple[int, str]) -> None:
        page, text = result
        self._on_range_page_read(page, text, "")

    def _on_range_ocr_failed(self, failure: WorkerFailed) -> None:
        if self._range_page is not None:
            self._on_range_page_read(self._range_page, "", failure.message)

    def _on_range_page_read(self, page: int, text: str, error: str) -> None:
        if not self.is_reading_range:
            return  # leitura cancelada
        if error:
            self._range_blocks.append(f"{{ PDF página {page + 1}: o OCR falhou ({error}) }}")
        else:
            self._ocr_cache[page] = text
            if text.strip():
                self._range_blocks.append(self._range_block(page, text.strip()))
        self._next_range_page()

    def _finish_range_reading(self) -> None:
        blocks, total = self._range_blocks, self._range_total
        self._stop_range_reading()
        if blocks:
            self.text_requested_signal.emit("\n\n".join(blocks))
        self.range_status_signal.emit(f"{total} página(s) lida(s) e mandada(s) ao editor.")

    def _stop_range_reading(self) -> None:
        self._range_queue = []
        self._range_blocks = []
        self._range_total = 0
        self._range_page = None
        self._range_ocr.cancel()  # o que ainda estiver no OCR nao chega
        if hasattr(self, "btn_range_ocr"):
            self.btn_range_ocr.setText("Ler intervalo (OCR)")
        self.clear_full_progress()

    def set_range_progress(self, page: int, total: int) -> None:
        self.full_progress.setVisible(True)
        self.full_progress.setValue(min(100, (page - 1) * 100 // max(1, total)))
        self.full_progress.setFormat(f"Lendo página {page}/{total} — %p%")

    def _on_page_ocr_failed(self, failure: WorkerFailed) -> None:
        self._ocr_pending_page = None
        self.btn_text_ocr.setEnabled(self.has_document())
        self.txt_page.setPlaceholderText(f"{SCANNED_PAGE_HINT} O OCR falhou: {failure.message}")

    def _on_page_ocr_finished(self, _request: int, result: tuple[int, str]) -> None:
        page, text = result
        self._ocr_pending_page = None
        self.btn_text_ocr.setEnabled(self.has_document())
        self._ocr_cache[page] = text
        if page == self.current_page():
            self.txt_page.setPlainText(text)
        if self._append_when_read == page:
            self._append_when_read = None
            if text.strip():
                self.text_requested_signal.emit(text.strip())

    def shutdown(self) -> None:
        """Encerra as threads de OCR. A janela chama isto ao fechar."""
        self._page_ocr.shutdown(timeout_ms=10000)
        self._range_ocr.shutdown(timeout_ms=10000)

    # ------------------------------------------------------------------

    def _update_enabled(self):
        has_document = self.has_document()
        for widget in (
            self.spn_page,
            self.spn_start,
            self.spn_end,
            self.btn_send,
            self.btn_convert,
            self.btn_next,
            self.btn_ocr,
            self.btn_full_recognition,
            self.btn_review_queue,
            self.btn_columns,
            self.btn_text_ocr,
            self.btn_range_ocr,
        ):
            widget.setEnabled(has_document)
        self.btn_cancel_ocr.setEnabled(has_document)
        self.btn_cancel_full_recognition.setEnabled(has_document)
        if not has_document:
            self.lbl_pages.setText("- / -")

    def set_full_progress(self, page: int, total: int, diagrams: int):
        if total <= 0:
            self.full_progress.setVisible(False)
            return
        self.full_progress.setVisible(True)
        self.full_progress.setValue(min(100, page * 100 // total))
        self.full_progress.setFormat(f"Página {page}/{total} — {diagrams} diagrama(s) — %p%")

    def clear_full_progress(self):
        self.full_progress.setValue(0)
        self.full_progress.setVisible(False)
