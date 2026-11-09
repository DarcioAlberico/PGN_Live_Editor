"""Reconhecimento integral de PDF, fora do processo Qt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fitz

from .diagram_ocr import DiagramOcrAdapter
from .observability import measure
from .text_ocr import OCR_DPIS, recognize_renders, rgb_to_bgra, text_ocr_available


def _ocr_page_text(page) -> tuple[str, str]:
    """(texto, metodo) de uma pagina sem camada de texto, pelo OCR de texto."""
    renders = []
    for dpi in OCR_DPIS:
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        renders.append((rgb_to_bgra(pixmap.samples, pixmap.width, pixmap.height), pixmap.width, pixmap.height, dpi))
    try:
        return recognize_renders(renders).text, "ocr"
    except Exception:  # noqa: BLE001 - uma pagina ruim nao interrompe o livro
        return "", "no-text-layer"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    adapter = DiagramOcrAdapter()
    pages: list[dict[str, object]] = []
    diagrams_total = 0
    with measure("pdf_full_recognition") as metric, fitz.open(str(args.pdf_path)) as document:
        first = max(1, args.start_page)
        last = min(len(document), args.end_page or len(document))
        total_pages = max(0, last - first + 1)
        for page_index in range(first - 1, last):
            page = document[page_index]
            page_text = page.get_text("text")
            text_method = "text-layer" if page_text.strip() else "no-text-layer"
            if not page_text.strip() and text_ocr_available():
                # Livro escaneado: o texto vem do OCR do Windows (services/text_ocr.py).
                page_text, text_method = _ocr_page_text(page)
            ocr_error = ""
            try:
                diagrams = adapter.recognize_pdf_page(args.pdf_path, page_index, dpi=args.dpi)
            except Exception as exc:  # uma página problemática não interrompe o livro inteiro
                diagrams = []
                ocr_error = str(exc)
            diagrams_total += len(diagrams)
            metric["pages"] = page_index - first + 2
            metric["diagrams"] = diagrams_total
            pages.append(
                {
                    "page": page_index + 1,
                    "text": page_text,
                    "text_method": text_method,
                    "diagrams": [
                        result.to_record(pdf_path=str(args.pdf_path), page=page_index + 1) for result in diagrams
                    ],
                    "ocr_error": ocr_error,
                }
            )
            print(
                f"PROGRESS {page_index - first + 2} {total_pages} {len(diagrams)} {diagrams_total}",
                file=sys.stderr,
                flush=True,
            )
    args.output.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
