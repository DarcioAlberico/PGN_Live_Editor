"""Métricas locais de operação; nunca registra texto ou conteúdo do usuário."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_METRICS_LOCK = threading.Lock()
MAX_METRIC_LINES = 1000


def metrics_path() -> Path:
    override = os.environ.get("PGN_LIVE_EDITOR_HOME", "").strip()
    root = Path(override) if override else Path(os.environ.get("APPDATA") or Path.home()) / "PGN Live Editor"
    root.mkdir(parents=True, exist_ok=True)
    return root / "metrics.jsonl"


def record_metric(operation: str, duration_ms: float, **counters: int | float | str) -> None:
    payload = {
        "timestamp": round(time.time(), 3),
        "operation": operation,
        "duration_ms": round(max(0.0, duration_ms), 2),
        **counters,
    }
    path = metrics_path()
    try:
        with _METRICS_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_METRIC_LINES:
                path.write_text("\n".join(lines[-MAX_METRIC_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_metrics() -> list[dict[str, object]]:
    """Read valid local metrics for diagnostics without exposing document data."""
    try:
        with _METRICS_LOCK:
            lines = metrics_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
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
