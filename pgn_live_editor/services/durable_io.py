"""Gravacao duravel: um lugar so decide como um arquivo do usuario vai para o disco.

Contrato (SPEC v3 §2.1, R-101 do docs/ROADMAP_V3.md), nesta ordem:

1. grava num temporario ao lado do destino (mesmo volume), `flush` + `fsync`;
2. se o destino existe, **copia** a versao atual para `<nome>.bak.new`;
3. `os.replace(temporario, destino)`, com algumas novas tentativas curtas em
   `PermissionError` -- antivirus, indexador e OneDrive seguram o arquivo por
   instantes no Windows;
4. so depois do sucesso, desce os backups: `.bak` -> `.bak.1` -> ... e
   `.bak.new` -> `.bak`.

Se qualquer passo ate o 3 falhar, o destino e os backups ficam exatamente como
estavam, nada temporario sobra, e sobe `DurableWriteError` com o caminho. Antes,
cada gravador fazia o seu jeito, e o do projeto renomeava o arquivo para `.bak`
**antes** de por o novo no lugar (S-02): uma falha na troca final deixava o
usuario sem projeto nenhum no caminho.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

REPLACE_ATTEMPTS = 5
REPLACE_WAIT_S = 0.1


class DurableWriteError(OSError):
    """A gravacao nao aconteceu; o arquivo anterior e os backups estao intactos."""


def backup_paths(path: Path, backups: int) -> list[Path]:
    """`[x.bak, x.bak.1, ..., x.bak.(n-1)]` -- os nomes que o programa sempre usou."""
    return [path.with_name(path.name + (".bak" if index == 0 else f".bak.{index}")) for index in range(backups)]


def write_durable_with(path: Path, writer: Callable[[BinaryIO], object], *, backups: int = 0) -> None:
    """Grava `path` com o que `writer` escrever no arquivo aberto (binario, seekable)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    staged_backup = path.with_name(path.name + ".bak.new")
    try:
        with os.fdopen(handle, "wb") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        if backups and path.exists():
            shutil.copy2(path, staged_backup)
        _replace_with_retries(temp_path, path)
    except BaseException as exc:
        temp_path.unlink(missing_ok=True)
        staged_backup.unlink(missing_ok=True)
        if isinstance(exc, OSError) and not isinstance(exc, DurableWriteError):
            raise DurableWriteError(exc.errno, f"Não foi possível gravar {path}: {exc}", str(path)) from exc
        raise
    if staged_backup.exists():
        _rotate(staged_backup, backup_paths(path, backups))


def write_durable(path: Path, data: bytes, *, backups: int = 0) -> None:
    write_durable_with(path, lambda stream: stream.write(data), backups=backups)


def write_durable_text(path: Path, text: str, *, backups: int = 0) -> None:
    """Texto em UTF-8, com `\\n` como fim de linha em qualquer sistema."""
    write_durable(path, text.encode("utf-8"), backups=backups)


def _replace_with_retries(source: Path, target: Path) -> None:
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_WAIT_S)


def _rotate(staged: Path, chain: list[Path]) -> None:
    """Desce a cadeia de backups. Falhar aqui nao desfaz a gravacao, que ja aconteceu."""
    try:
        for older, newer in zip(reversed(chain[:-1]), reversed(chain[1:]), strict=True):
            if older.exists():
                os.replace(older, newer)
        os.replace(staged, chain[0])
    except OSError:
        logger.warning(
            "Backups de %s nao foram girados; a versao anterior ficou em %s", chain[0], staged, exc_info=True
        )
