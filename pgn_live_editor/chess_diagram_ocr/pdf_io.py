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


def get_pdf_page_count(pdf_source: PdfSource) -> int:
    with open_document(pdf_source) as doc:
        return doc.page_count


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
