"""Métricas locais de operação; nunca registra texto ou conteúdo do usuário."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_METRICS_LOCK = threading.Lock()
MAX_METRIC_LINES = 1000


def metrics_path() -> Path:
    override = os.environ.get("PGN_LIVE_EDITOR_HOME", "").strip()
    root = Path(override) if override else Path(os.environ.get("APPDATA") or Path.home()) / "PGN Live Editor"
    root.mkdir(parents=True, exist_ok=True)
    return root / "metrics.jsonl"


#: (linhas, bytes) do arquivo corrente, por caminho. Contado uma vez; depois
#: so incrementa. Se o tamanho no disco nao bate (outra instancia gravou, ou o
#: arquivo foi apagado), reconta -- custa um `stat`, nunca uma leitura.
_line_counts: dict[Path, tuple[int, int]] = {}


def _rotated(path: Path) -> Path:
    return path.with_name(path.name + ".1")


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def record_metric(operation: str, duration_ms: float, **counters: int | float | str) -> None:
    """Acrescenta uma linha ao metrics.jsonl. Nunca levanta (S-04).

    So append: ao passar de `MAX_METRIC_LINES` o arquivo vira `.1` (o antigo
    `.1` some), e o corrente recomeca. Antes cada medida relia e reescrevia o
    arquivo inteiro na thread da UI, e um byte invalido nele derrubava o parse.
    """
    try:
        payload = {
            "timestamp": round(time.time(), 3),
            "operation": operation,
            "duration_ms": round(max(0.0, duration_ms), 2),
            **counters,
        }
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        path = metrics_path()
        with _METRICS_LOCK:
            size = path.stat().st_size if path.exists() else 0
            count, known_size = _line_counts.get(path, (-1, -1))
            if known_size != size:
                count = _count_lines(path)
            if count >= MAX_METRIC_LINES:
                os.replace(path, _rotated(path))
                count = 0
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            _line_counts[path] = (count + 1, path.stat().st_size)
    except Exception:  # noqa: BLE001 - metrica nunca pode derrubar a operacao medida
        logger.debug("metrica %s nao gravada", operation, exc_info=True)


def read_metrics() -> list[dict[str, object]]:
    """Read valid local metrics for diagnostics without exposing document data."""
    path = metrics_path()
    lines: list[str] = []
    with _METRICS_LOCK:
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
