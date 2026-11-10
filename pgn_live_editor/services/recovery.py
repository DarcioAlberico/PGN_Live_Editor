"""Recuperacao de sessao v2: o projeto inteiro, um arquivo por instancia (R-102).

O que mudou e por que (docs/AUDITORIA_2026-11.md, S-01 e S-14):

- o snapshot guarda o **projeto inteiro** (as mesmas cinco partes do `.plez`,
  por `project_to_parts`), nao so o texto. Antes, recuperar e salvar gravava
  por cima do `.plez` um projeto sem cabecalhos, dicionario, partidas
  concluidas e leituras de diagrama;
- cada instancia tem o seu `recovery/<instancia>.json`, e segura uma trava de
  sistema operacional em `recovery/<instancia>.lock` enquanto vive. So e
  "orfao" -- oferecido ao abrir -- o snapshot cuja trava ninguem segura: a
  instancia morreu. Antes havia um `session.json` so, e a segunda janela
  sobrescrevia e, ao fechar, apagava a recuperacao da primeira.

O `session.json` antigo (formato 1, so texto) continua sendo oferecido, e ao
restaurar ele carrega o projeto de origem e troca so o texto.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .durable_io import write_durable_text
from .project_service import Project, load_project, project_from_parts, project_to_parts, recovery_dir
from .schemas import ProjectFormatError

logger = logging.getLogger(__name__)

SNAPSHOT_FORMAT = "pgn-live-editor/recovery"
SNAPSHOT_VERSION = 2
LEGACY_FILE = "session.json"


# ----------------------------------------------------------------------
# Trava por instancia: o sistema operacional a solta quando o processo morre
# ----------------------------------------------------------------------


def _lock(handle) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _held_by_someone(lock_path: Path) -> bool:
    """Alguma instancia viva segura esta trava?"""
    if not lock_path.exists():
        return False
    try:
        with open(lock_path, "r+b") as handle:
            try:
                _lock(handle)
            except OSError:
                return True
            _unlock(handle)
            return False
    except OSError:
        # Nem abrir deu: no Windows, outra instancia segurando o arquivo.
        return True


# ----------------------------------------------------------------------
# O snapshot
# ----------------------------------------------------------------------


@dataclass
class RecoverySnapshot:
    project: Project
    origin_path: Path | None
    saved_at: float
    cursor_position: int
    snapshot_path: Path
    legacy: bool = False

    @property
    def raw_text(self) -> str:
        return self.project.raw_text

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.saved_at)


class RecoverySession:
    """O snapshot desta instancia. A trava so e criada no primeiro `write`."""

    def __init__(self) -> None:
        self.instance = uuid.uuid4().hex
        self._lock_handle = None

    @property
    def snapshot_path(self) -> Path:
        return recovery_dir() / f"{self.instance}.json"

    @property
    def lock_path(self) -> Path:
        return recovery_dir() / f"{self.instance}.lock"

    def write(self, project: Project, raw_text: str, *, cursor_position: int = 0) -> Path | None:
        """Grava o projeto com o texto atual do editor. Texto em branco nao vale snapshot."""
        if not raw_text.strip():
            return None
        self._hold_lock()
        parts = project_to_parts(project)
        parts["raw_text"] = raw_text
        payload = {
            "format": SNAPSHOT_FORMAT,
            "version": SNAPSHOT_VERSION,
            "instance": self.instance,
            "pid": os.getpid(),
            "saved_at": time.time(),
            "origin_path": str(project.path) if project.path else "",
            "cursor_position": int(cursor_position),
            "project": parts,
        }
        write_durable_text(self.snapshot_path, json.dumps(payload, ensure_ascii=False))
        return self.snapshot_path

    def clear(self) -> None:
        """Fechamento normal (ou projeto salvo): o snapshot desta instancia nao e mais preciso."""
        self.snapshot_path.unlink(missing_ok=True)
        if self._lock_handle is not None:
            try:
                _unlock(self._lock_handle)
            except OSError:
                pass
            self._lock_handle.close()
            self._lock_handle = None
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("trava %s nao removida", self.lock_path, exc_info=True)

    def _hold_lock(self) -> None:
        if self._lock_handle is not None:
            return
        handle = open(self.lock_path, "a+b")  # noqa: SIM115 - fica aberto enquanto a instancia vive
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode() or b"0")
        handle.flush()
        _lock(handle)
        self._lock_handle = handle


def orphan_snapshots() -> list[RecoverySnapshot]:
    """Snapshots de instancias que morreram, do mais novo para o mais antigo.

    Arquivo ilegivel, sem projeto ou so com espaco em branco e ignorado (e
    registrado no log), nunca levanta: isto roda na abertura do programa.
    """
    found: list[RecoverySnapshot] = []
    for path in sorted(recovery_dir().glob("*.json")):
        if path.name != LEGACY_FILE and _held_by_someone(path.with_suffix(".lock")):
            continue
        snapshot = _read(path)
        if snapshot is not None:
            found.append(snapshot)
    found.sort(key=lambda snapshot: snapshot.saved_at, reverse=True)
    return found


def _read(path: Path) -> RecoverySnapshot | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Snapshot de recuperacao ilegivel: %s", path.name, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    if path.name == LEGACY_FILE:
        parts = {"raw_text": payload.get("raw_text")}
    else:
        parts = payload.get("project")
        if not isinstance(parts, dict):
            return None
    raw_text = parts.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    origin = payload.get("origin_path")
    origin_path = Path(origin) if isinstance(origin, str) and origin else None
    try:
        project = project_from_parts(
            raw_text=raw_text,
            meta_payload=parts.get("meta", {}),
            state_payload=parts.get("state", {}),
            headers_payload=parts.get("headers", {}),
            dictionary_payload=parts.get("dictionary", {}),
            path=origin_path,
        )
    except ProjectFormatError:
        logger.warning("Snapshot %s e de uma versao mais nova", path.name)
        return None
    saved_at = payload.get("saved_at")
    cursor = payload.get("cursor_position")
    return RecoverySnapshot(
        project=project,
        origin_path=origin_path,
        saved_at=float(saved_at) if isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool) else 0.0,
        cursor_position=cursor if isinstance(cursor, int) and not isinstance(cursor, bool) else 0,
        snapshot_path=path,
        legacy=path.name == LEGACY_FILE,
    )


def restore_project(snapshot: RecoverySnapshot) -> Project:
    """O projeto a reabrir. Aponta para a origem quando ela ainda existe.

    O snapshot novo ja traz o projeto inteiro. O antigo so trazia o texto: a
    origem e carregada e so o texto e trocado -- o resto do projeto fica.
    """
    origin = snapshot.origin_path if snapshot.origin_path and snapshot.origin_path.is_file() else None
    if not snapshot.legacy:
        project = snapshot.project
        project.path = origin
        return project
    if origin is not None:
        try:
            project = load_project(origin)
        except (OSError, ValueError):
            logger.warning("Origem %s nao abriu; recuperando so o texto", origin, exc_info=True)
            return Project(raw_text=snapshot.raw_text)
        project.raw_text = snapshot.raw_text
        return project
    return Project(raw_text=snapshot.raw_text)


def discard(snapshot: RecoverySnapshot) -> None:
    """O usuario decidiu (recuperou ou descartou): o orfao nao volta a ser oferecido."""
    snapshot.snapshot_path.unlink(missing_ok=True)
    lock = snapshot.snapshot_path.with_suffix(".lock")
    if not _held_by_someone(lock):
        lock.unlink(missing_ok=True)
