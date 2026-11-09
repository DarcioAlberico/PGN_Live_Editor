"""Diagnóstico seguro: ambiente e contadores, nunca texto do documento."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from .observability import metrics_path, read_metrics, summarize_metrics


def _optional_parts() -> list[str]:
    """As duas pecas opcionais: sem elas o programa abre, mas perde uma funcao."""
    # Import tardio: o diagnostico precisa sair mesmo com o ambiente quebrado.
    try:
        from .engine_service import discover_engine_path, load_engine_settings

        engine = discover_engine_path(load_engine_settings().path)
    except Exception as exc:  # noqa: BLE001 - diagnostico nunca levanta
        engine = f"erro ao procurar ({exc})"
    try:
        from .text_ocr import text_ocr_available

        ocr = "disponível (Windows)" if text_ocr_available() else "indisponível"
    except Exception as exc:  # noqa: BLE001
        ocr = f"erro ({exc})"
    return [
        f"Motor de xadrez: {engine or 'nenhum encontrado'}",
        f"OCR de texto: {ocr}",
    ]


def build_diagnostic_report(*, project_path: Path | None = None, pdf_path: Path | None = None) -> str:
    lines = [
        "PGN Live Editor — diagnóstico",
        "==============================",
        f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})",
        f"Sistema: {platform.platform()}",
        f"Máquina: {platform.machine()}",
        f"Executável: {sys.executable}",
        f"Projeto aberto: {'sim' if project_path else 'não'}",
        f"PDF aberto: {'sim' if pdf_path else 'não'}",
        f"Métricas locais: {metrics_path()}",
        *_optional_parts(),
    ]
    metrics = read_metrics()
    if metrics:
        operations = sorted({str(item["operation"]) for item in metrics})
        lines.append(f"Métricas registradas: {len(metrics)} ({', '.join(operations)})")
        summary = summarize_metrics(metrics)
        for operation in sorted(summary):
            item = summary[operation]
            lines.append(
                f"  {operation}: {int(item['count'])}x, média {item['average_ms']:.2f} ms, máximo {item['max_ms']:.2f} ms"
            )
    if project_path:
        lines.append(f"Projeto: {project_path.name}")
    if pdf_path:
        lines.append(f"PDF: {pdf_path.name}")
    return "\n".join(lines) + "\n"
