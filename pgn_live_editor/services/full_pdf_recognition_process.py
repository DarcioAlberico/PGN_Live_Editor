"""Reconhecimento integral de PDF, fora do processo Qt.

A saida e JSONL: uma linha por pagina, gravada assim que a pagina termina
(R-109 do docs/ROADMAP_V3.md). Se o processo morrer na pagina 300, as 299
anteriores ja estao no arquivo e o editor as aproveita; antes o resultado so
era gravado no fim e uma queda perdia tudo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .diagram_ocr import DiagramOcrAdapter
from .observability import measure
from .text_ocr import OCR_DPIS, recognize_renders, rgb_to_bgra, text_ocr_available


def _ocr_page_text(page) -> tuple[str, str, str]:
    """(texto, metodo, erro) de uma pagina sem camada de texto, pelo OCR de texto.

    Falha do OCR e `ocr-failed` com o motivo, nao `no-text-layer`: o problema
    nao esta no PDF, e o usuario precisa saber disso para ir atras (S-15).
    """
    renders = []
    for dpi in OCR_DPIS:
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        renders.append((rgb_to_bgra(pixmap.samples, pixmap.width, pixmap.height), pixmap.width, pixmap.height, dpi))
    try:
        return recognize_renders(renders).text, "ocr", ""
    except Exception as exc:  # noqa: BLE001 - uma pagina ruim nao interrompe o livro; o motivo vai junto
        return "", "ocr-failed", f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import fitz  # extra `diagram-ocr`; importado aqui para o modulo carregar sem ele

    adapter = DiagramOcrAdapter()
    diagrams_total = 0
    with (
        measure("pdf_full_recognition") as metric,
        fitz.open(str(args.pdf_path)) as document,
        args.output.open("w", encoding="utf-8") as output,
    ):
        first = max(1, args.start_page)
        last = min(len(document), args.end_page or len(document))
        total_pages = max(0, last - first + 1)
        for page_index in range(first - 1, last):
            page = document[page_index]
            page_text = page.get_text("text")
            text_method = "text-layer" if page_text.strip() else "no-text-layer"
            text_error = ""
            if not page_text.strip() and text_ocr_available():
                # Livro escaneado: o texto vem do OCR do Windows (services/text_ocr.py).
                page_text, text_method, text_error = _ocr_page_text(page)
            ocr_error = ""
            try:
                diagrams = adapter.recognize_pdf_page(args.pdf_path, page_index, dpi=args.dpi)
            except Exception as exc:  # noqa: BLE001 - uma página problemática não interrompe o livro inteiro
                diagrams = []
                ocr_error = str(exc)
            diagrams_total += len(diagrams)
            metric["pages"] = page_index - first + 2
            metric["diagrams"] = diagrams_total
            record = {
                "page": page_index + 1,
                "text": page_text,
                "text_method": text_method,
                "text_error": text_error,
                "diagrams": [result.to_record(pdf_path=str(args.pdf_path), page=page_index + 1) for result in diagrams],
                "ocr_error": ocr_error,
            }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
            print(
                f"PROGRESS {page_index - first + 2} {total_pages} {len(diagrams)} {diagrams_total}",
                file=sys.stderr,
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
