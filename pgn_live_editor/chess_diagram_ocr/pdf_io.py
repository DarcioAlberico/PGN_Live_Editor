"""Leitura de PDF: abrir, contar paginas, renderizar.

**Sobre abrir o documento uma vez por varredura (S-61).** Cada pagina passava por tres
aberturas independentes -- render, deteccao e camada de texto --, e os docstrings chamavam
isso de irrelevante ao lado do render. Medido, depende muito do livro: 1,16 ms no `Polgar` e
**28,80 ms** no `Yusupov`, o que da 4,1 s contra 225,7 s numa varredura completa. Uma
diferenca de 50x entre o melhor e o pior caso nao e detalhe.

`OpenPdf` e o emprestimo: quem varre um livro abre uma vez e passa o objeto adiante, e as
funcoes por caminho continuam existindo e continuam sendo a porta dos CLIs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import fitz
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OpenPdf:
    """Um documento ja aberto, emprestado ao pipeline de uma pagina (S-61).

    Quem cria fecha; quem recebe **nao** fecha. E por isso que ele existe como tipo em vez de
    o `fitz.Document` cru circular pelas assinaturas: um `with fitz.open(...)` no meio do
    pipeline fecharia o documento da varredura inteira, e o sintoma apareceria na pagina
    seguinte, longe da causa.
    """

    source: PdfSource
    doc: fitz.Document

    @property
    def page_count(self) -> int:
        return int(self.doc.page_count)

    def page(self, index: int) -> fitz.Page:
        return self.doc[index]


PdfSource = str | Path | bytes | OpenPdf

_opens = 0
"""Quantas vezes um documento foi de fato aberto. Existe para o teste da S-61 poder afirmar
"uma abertura por pagina, nao tres" sem instrumentar o `fitz`."""


def open_count() -> int:
    return _opens


def _open_document(pdf_source: PdfSource) -> fitz.Document:
    """Abre o documento, e **recusa o que está protegido por senha** (S-331).

    `fitz.open` aceita um PDF cifrado sem reclamar: `needs_pass` fica ligado, `page_count`
    responde o número certo -- 1, no teste -- e só o primeiro acesso a uma página levanta
    `ValueError: document closed or encrypted`, em inglês e longe daqui.

    Isso furava a S-123, que pôs `get_pdf_page_count` como primeira linha de `load_pdf`
    justamente para validar antes de mutar: a contagem **passava**, o painel trocava o livro do
    programa inteiro -- Galeria, resultados, aba de estudo -- e a falha só aparecia no render,
    com a tela ainda mostrando o livro anterior. O programa ficava por dentro num arquivo que
    ele nunca conseguiria ler.

    A recusa mora aqui, e não no painel, porque **nenhum** caminho deste programa sabe pedir
    senha: o `cvoff-scan`, o censo e a exportação encontrariam o mesmo muro três camadas
    adiante. `ValueError` porque é o que o `cli_errors` da S-126 traduz em código 2 e frase em
    pt-BR, e é o que o `except` do painel já apanha.
    """
    global _opens
    _opens += 1
    if isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
        nome = "O PDF recebido em memória"
    else:
        doc = fitz.open(str(pdf_source))
        nome = Path(str(pdf_source)).name
    if doc.needs_pass:
        doc.close()
        raise ValueError(
            f"{nome} está protegido por senha. Este programa não tem onde pedi-la: abra o "
            "arquivo no leitor de PDF do sistema, salve uma cópia sem senha, e use a cópia."
        )
    return doc


@contextmanager
def open_document(pdf_source: PdfSource) -> Iterator[fitz.Document]:
    """O documento de `pdf_source`, aberto agora ou emprestado se ja estiver aberto.

    Fecha **apenas** o que abriu. Substituiu o `with _open_document(...)` espalhado por tres
    modulos, que era o ponto exato em que a S-61 media as tres aberturas por pagina.
    """
    if isinstance(pdf_source, OpenPdf):
        yield pdf_source.doc
        return

    doc = _open_document(pdf_source)
    try:
        yield doc
    finally:
        doc.close()


@contextmanager
def opened(pdf_source: PdfSource) -> Iterator[OpenPdf]:
    """Abre uma vez para uma varredura inteira. Reentrante: um `OpenPdf` volta como ele mesmo."""
    if isinstance(pdf_source, OpenPdf):
        yield pdf_source
        return

    doc = _open_document(pdf_source)
    try:
        yield OpenPdf(source=pdf_source, doc=doc)
    finally:
        doc.close()


@contextmanager
def opened_or_source(pdf_source: PdfSource) -> Iterator[PdfSource]:
    """Como `opened`, mas devolve a **origem intacta** quando não consegue abrir.

    O empréstimo da S-61 é uma otimização, não uma validação: antes dele, cada etapa abria o
    seu documento, e quem não conseguia abrir falhava ali, com a mensagem daquela etapa.
    Levantar aqui trocaria "não consegui renderizar a página 1" por um erro vindo de uma
    camada que quem chamou não sabe que existe -- e quebraria os testes de varredura que
    substituem render, detecção e contexto e passam um nome que nunca foi arquivo.
    """
    if isinstance(pdf_source, OpenPdf):
        yield pdf_source
        return

    try:
        doc = _open_document(pdf_source)
    except Exception as exc:  # noqa: BLE001 -- a falha real aparece na etapa que precisa dela
        logger.debug("Documento não pôde ser aberto para empréstimo (%s); cada etapa abre o seu.", exc)
        yield pdf_source
        return

    try:
        yield OpenPdf(source=pdf_source, doc=doc)
    finally:
        doc.close()


def get_pdf_page_count(pdf_source: PdfSource) -> int:
    with open_document(pdf_source) as doc:
        return doc.page_count


def sample_pages(page_count: int, wanted: int) -> list[int]:
    """Indices igualmente espacados, sem repeticao e sem sorteio.

    As bordas ficam de fora de proposito: a primeira e a ultima pagina de um livro de xadrez
    sao capa, indice ou catalogo, e gasta-las e gastar dois doze avos da amostra em paginas
    que nunca tem diagrama.

    Mora aqui, e nao no levantamento que a estreou (`side_survey`), porque a S-82 precisa da
    mesma amostragem **sem** arrastar junto o `OcrService` -- e com ele o torch. Um censo de
    deteccao que paga 8 s de import de modelo para nao usar modelo nenhum nao seria rodado a
    cada mudanca, que e a unica coisa que ele existe para ser.
    """
    if page_count <= 0 or wanted <= 0:
        return []
    if page_count <= wanted:
        return list(range(page_count))
    passo = page_count / (wanted + 1)
    return sorted({min(page_count - 1, int(round(passo * (i + 1)))) for i in range(wanted)})


def render_pdf_page(pdf_source: PdfSource, page_index: int, dpi: int = 220) -> np.ndarray:
    """Renderiza uma pagina do PDF como array RGB (H, W, 3) proprio e gravavel."""
    with open_document(pdf_source) as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"Página {page_index} fora do intervalo (0..{doc.page_count - 1})")

        page = doc[page_index]
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=matrix, alpha=False)

    # np.frombuffer devolve uma view somente-leitura sobre o buffer do Pixmap.
    # A copia garante um array proprio e gravavel, isolado do ciclo de vida do pix.
    buffer = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    image: np.ndarray = buffer[:, :, :3].copy() if pix.n == 4 else buffer.copy()
    return image
