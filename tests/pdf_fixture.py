"""Gera um PDF minimo, de verdade, para os testes do painel de PDF.

O `QPdfWriter` do Qt escreve fontes sem tabela ToUnicode, e por isso o texto do
PDF que ele gera **nao se extrai** -- o que servia de fixture para desenhar nao
serve para testar leitura. Entao aqui se escreve o arquivo na unha, com a
Helvetica base-14, que qualquer leitor de PDF sabe mapear de volta para texto.

Sao ~40 linhas e nenhuma dependencia nova, contra um PDF binario opaco no
repositorio que ninguem consegue conferir nem alterar.
"""

from __future__ import annotations

from pathlib import Path


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[str]) -> bytes:
    body = ["BT", "/F1 14 Tf", "72 720 Td", "18 TL"]
    for index, line in enumerate(lines):
        if index:
            body.append("T*")
        body.append(f"({_escape(line)}) Tj")
    body.append("ET")
    return "\n".join(body).encode("latin-1")


def write_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Escreve um PDF com uma pagina por lista de linhas."""
    objects: list[bytes] = []

    def add(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: list[int] = []
    content_ids: list[int] = []
    for lines in pages:
        stream = _content_stream(lines)
        content_ids.append(add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)))
        page_ids.append(0)  # reservado; preenchido abaixo

    pages_id = len(objects) + len(pages) + 1
    for index, content_id in enumerate(content_ids):
        page_ids[index] = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" % (pages_id, font_id, content_id)
        )

    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)))
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + payload + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_id,
        xref_at,
    )

    path.write_bytes(bytes(out))
    return path
