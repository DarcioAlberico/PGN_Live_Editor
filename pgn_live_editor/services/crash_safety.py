"""Excecao inesperada vira log + trabalho guardado, nunca processo morto (S-03).

Sem `sys.excepthook` proprio, o PyQt6 chama `qFatal` quando um slot levanta: o
processo some com 0xC0000409, sem traceback -- o executavel nao tem console -- e
sem gravar o que estava aberto. Com o hook instalado, o PyQt so o chama e segue.

Isto **nao corrige** defeito nenhum: torna o proximo visivel (o log) e barato
para o usuario (as acoes de emergencia gravam a recuperacao). As causas ficam
nos itens proprios do docs/ROADMAP_V3.md.

Nada do documento vai para o log alem do que a propria excecao carregar.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

LOG_FILE_NAME = "pgn-live-editor.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 5

logger = logging.getLogger("pgn_live_editor")

_emergency_actions: list[Callable[[], None]] = []
_error_listeners: list[Callable[[str], None]] = []
_installed_handler: logging.Handler | None = None
_handling = threading.local()


def install(log_dir: Path) -> Path:
    """Liga o log em arquivo e os hooks de excecao. Idempotente; devolve o log."""
    global _installed_handler
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    if _installed_handler is None:
        handler = RotatingFileHandler(log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _installed_handler = handler
    sys.excepthook = _on_uncaught
    threading.excepthook = _on_thread_uncaught
    return log_path


def log_path() -> Path | None:
    """Onde o log esta sendo gravado, ou None antes de `install`."""
    if _installed_handler is None:
        return None
    return Path(getattr(_installed_handler, "baseFilename", ""))


def add_emergency_action(action: Callable[[], None]) -> None:
    """Algo a fazer antes de avisar o usuario (ex.: gravar a recuperacao)."""
    _emergency_actions.append(action)


def add_error_listener(listener: Callable[[str], None]) -> None:
    """Quem mostra o erro ao usuario; recebe uma linha de resumo."""
    _error_listeners.append(listener)


def _on_thread_uncaught(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit or args.exc_value is None:
        return
    _on_uncaught(args.exc_type, args.exc_value, args.exc_traceback)


def _on_uncaught(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    # Uma excecao dentro do proprio tratamento nao pode virar laco.
    if getattr(_handling, "active", False):
        return
    _handling.active = True
    try:
        logger.critical("Excecao nao tratada", exc_info=(exc_type, exc_value, exc_traceback))
        for action in list(_emergency_actions):
            try:
                action()
            except Exception:  # noqa: BLE001 - a emergencia nao pode piorar a emergencia
                logger.exception("Acao de emergencia falhou")
        summary = f"{exc_type.__name__}: {exc_value}"
        for listener in list(_error_listeners):
            try:
                listener(summary)
            except Exception:  # noqa: BLE001
                logger.exception("Aviso de erro ao usuario falhou")
    finally:
        _handling.active = False
