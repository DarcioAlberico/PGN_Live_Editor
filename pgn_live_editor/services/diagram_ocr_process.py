"""Ponto de entrada do OCR nativo, isolado do processo Qt do editor.

O isolamento é importante no Windows: Qt/PyQt6 e Torch podem carregar
versões incompatíveis de DLLs OpenMP/C++ no mesmo processo.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diagram_ocr import DiagramOcrAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("page_index", type=int)
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    results = DiagramOcrAdapter().recognize_pdf_page(args.pdf_path, args.page_index, dpi=args.dpi)
    payload = json.dumps(
        [result.to_record(pdf_path=str(args.pdf_path), page=args.page_index) for result in results],
        ensure_ascii=False,
    )
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
