"""Métricas locais de operação; nunca registra texto ou conteúdo do usuário.

Medir não pode custar o que é medido (R-404). Quem mede só põe a medida numa
fila em memória (microssegundos); uma thread de fundo grava em lote, por
append, a cada `FLUSH_INTERVAL_S`. Antes cada medida fazia mkdir + stat +
abrir e fechar o arquivo na thread da interface: ~0,6 ms por chamada, e abrir
um livro de 3 500 partidas gastava segundos só com isso (S-04).
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

#: Tamanho maximo do metrics.jsonl; passou, ele vira `.1` (o `.1` antigo some).
MAX_METRIC_BYTES = 256 * 1024
FLUSH_INTERVAL_S = 2.0
#: Se o disco falhar por muito tempo, a fila nao cresce sem fim: as mais antigas saem.
MAX_PENDING_METRICS = 50_000

_FILE_LOCK = threading.Lock()
_pending: deque[tuple[Path, dict[str, object]]] = deque(maxlen=MAX_PENDING_METRICS)
_flusher: threading.Thread | None = None
_flusher_lock = threading.Lock()
_paths: dict[tuple[str, str], Path] = {}


def metrics_path() -> Path:
    # Montar `Path` custa microssegundos que, por medida, viravam a maior parte
    # do custo de medir: o caminho fica guardado por ambiente.
    key = (os.environ.get("PGN_LIVE_EDITOR_HOME", ""), os.environ.get("APPDATA", ""))
    path = _paths.get(key)
    if path is None:
        override = key[0].strip()
        root = Path(override) if override else Path(key[1] or Path.home()) / "PGN Live Editor"
        root.mkdir(parents=True, exist_ok=True)
        path = _paths[key] = root / "metrics.jsonl"
    return path


def _rotated(path: Path) -> Path:
    return path.with_name(path.name + ".1")


def record_metric(operation: str, duration_ms: float, **counters: int | float | str) -> None:
    """Enfileira uma medida para o metrics.jsonl. Nunca levanta, nunca toca no disco."""
    try:
        payload: dict[str, object] = {
            "timestamp": round(time.time(), 3),
            "operation": operation,
            "duration_ms": round(max(0.0, duration_ms), 2),
            **counters,
        }
        _pending.append((metrics_path(), payload))
        _ensure_flusher()
    except Exception:  # noqa: BLE001 - metrica nunca pode derrubar a operacao medida
        logger.debug("metrica %s nao registrada", operation, exc_info=True)


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None:
        return
    with _flusher_lock:
        if _flusher is None:
            _flusher = threading.Thread(target=_flush_forever, name="metricas", daemon=True)
            _flusher.start()


def _flush_forever() -> None:
    while True:
        time.sleep(FLUSH_INTERVAL_S)
        flush_metrics()


def flush_metrics() -> None:
    """Grava o que esta na fila, agora. Nunca levanta."""
    batches: dict[Path, list[str]] = {}
    while True:
        try:
            path, payload = _pending.popleft()
        except IndexError:
            break
        try:
            batches.setdefault(path, []).append(json.dumps(payload, ensure_ascii=False) + "\n")
        except (TypeError, ValueError):
            continue
    for path, lines in batches.items():
        try:
            with _FILE_LOCK:
                _append_rotating(path, lines)
        except Exception:  # noqa: BLE001 - metrica perdida nao derruba nada
            logger.debug("metricas nao gravadas em %s", path, exc_info=True)


def _append_rotating(path: Path, lines: list[str]) -> None:
    """Append em lote; ao passar de `MAX_METRIC_BYTES`, o arquivo vira `.1` e recomeca."""
    path.parent.mkdir(parents=True, exist_ok=True)  # na thread de fundo: a pasta pode ter sumido
    size = path.stat().st_size if path.exists() else 0
    chunk: list[str] = []
    for line in lines:
        length = len(line.encode("utf-8"))
        if size + length > MAX_METRIC_BYTES and (chunk or size):
            if chunk:
                with path.open("a", encoding="utf-8", newline="") as handle:
                    handle.writelines(chunk)
                chunk = []
            os.replace(path, _rotated(path))
            size = 0
        chunk.append(line)
        size += length
    if chunk:
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.writelines(chunk)


atexit.register(flush_metrics)


def read_metrics() -> list[dict[str, object]]:
    """Read valid local metrics for diagnostics without exposing document data."""
    flush_metrics()
    path = metrics_path()
    lines: list[str] = []
    with _FILE_LOCK:
        for source in (_rotated(path), path):
            try:
                lines.extend(source.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                continue
    result: list[dict[str, object]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("operation"), str):
            result.append(item)
    return result


def summarize_metrics(metrics: list[dict[str, object]] | None = None) -> dict[str, dict[str, float]]:
    """Return count/average/max by operation for diagnostics and QA."""
    source = metrics if metrics is not None else read_metrics()
    grouped: dict[str, list[float]] = {}
    for item in source:
        operation = item.get("operation")
        duration = item.get("duration_ms")
        if isinstance(operation, str) and isinstance(duration, (int, float)):
            grouped.setdefault(operation, []).append(float(duration))
    return {
        operation: {
            "count": float(len(values)),
            "average_ms": round(sum(values) / len(values), 2),
            "max_ms": round(max(values), 2),
        }
        for operation, values in grouped.items()
    }


@contextmanager
def measure(operation: str, **counters: int | float | str) -> Iterator[dict[str, int | float | str]]:
    started = time.perf_counter()
    result = dict(counters)
    try:
        yield result
    finally:
        record_metric(operation, (time.perf_counter() - started) * 1000, **result)
