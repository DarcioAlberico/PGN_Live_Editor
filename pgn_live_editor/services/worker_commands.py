"""Subcomandos `--worker` do programa (SPEC_V3 §4.4, R-304).

Sem Qt de proposito: `__main__` despacha estes subcomandos **antes** de
importar qualquer coisa da interface -- o filho de OCR nao abre janela, e o
Torch nao disputa DLLs com o Qt no mesmo processo.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence

#: Nome do subcomando -> modulo com `main(argv) -> int`.
WORKER_MODULES = {
    "diagram-ocr": "pgn_live_editor.services.diagram_ocr_process",
    "full-pdf": "pgn_live_editor.services.full_pdf_recognition_process",
    "warm-up": "pgn_live_editor.services.ocr_warm_up",
}


def worker_command(name: str, *args: str) -> list[str]:
    """A linha de comando que roda o subcomando `name` num processo filho.

    No executavel congelado `sys.executable` e o proprio programa, e `-m` nao
    existe la: o filho abria uma segunda janela do editor (S-11).
    """
    if name not in WORKER_MODULES:
        raise ValueError(f"subcomando de worker desconhecido: {name}")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", name, *args]
    return [sys.executable, "-m", "pgn_live_editor", "--worker", name, *args]


def run_worker(argv: Sequence[str]) -> int:
    """`argv` = [nome, args...]. Devolve o codigo de saida do processo."""
    if not argv or argv[0] not in WORKER_MODULES:
        known = ", ".join(sorted(WORKER_MODULES))
        print(
            f"subcomando de worker desconhecido: {argv[0] if argv else '(nenhum)'} (conhecidos: {known})",
            file=sys.stderr,
        )
        return 2
    module = importlib.import_module(WORKER_MODULES[argv[0]])
    return int(module.main(list(argv[1:])))
