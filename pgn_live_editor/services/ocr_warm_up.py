"""Aquecimento opcional do OCR de diagramas (R-405).

A leitura de diagramas roda num processo filho, que importa torch, OpenCV e
PyMuPDF: ~1,5 s com o disco quente, ~20 s frio (O-11). Na primeira leitura da
sessao o usuario esperava os 20 s achando que o programa tinha travado.

Com o extra instalado e um PDF aberto, um filho de baixa prioridade importa o
servico e sai -- uma vez por sessao. Ele nao guarda nada nem conversa com
ninguem: so deixa os arquivos no cache do sistema para a leitura de verdade.
Se nao der para iniciar, nada acontece; e uma conveniencia.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys

from .worker_commands import worker_command

logger = logging.getLogger(__name__)

#: Os modulos que o servico de OCR de diagramas importa no topo.
_HEAVY_MODULES = ("torch", "cv2", "pymupdf")

_started = False


def diagram_ocr_installed() -> bool:
    """O extra `diagram-ocr` esta instalado? So procura os modulos, nao importa nenhum."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in _HEAVY_MODULES)
    except (ImportError, ValueError):
        return False


def warm_up_diagram_ocr() -> bool:
    """Inicia o aquecimento, se fizer sentido. `True` se um processo foi iniciado agora."""
    global _started
    if _started or not diagram_ocr_installed():
        return False
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    try:
        subprocess.Popen(  # noqa: S603 - o proprio programa, com argumentos fixos
            worker_command("warm-up"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError:
        logger.debug("aquecimento do OCR nao iniciado", exc_info=True)
        return False
    _started = True
    return True


def main(argv: list[str] | None = None) -> int:
    """Subcomando `--worker warm-up`: importa o servico e sai. Nunca falha alto."""
    # O servico importa o torch so na hora de inferir -- e ele e o maior dos
    # tres. Aquecer e importar tudo o que a leitura vai importar.
    for module in ("pgn_live_editor.chess_diagram_ocr.service", "torch"):
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001 - aquecer e opcional; quem importa de verdade relata o erro
            logger.debug("aquecimento do OCR: %s nao importou", module, exc_info=True)
    return 0
