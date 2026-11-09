"""Detecção e ordenação determinística de blocos de texto de PDF."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PdfTextBlock:
    text: str
    x: float
    y: float
    width: float = 0.0
    height: float = 0.0


def detect_columns(blocks: list[PdfTextBlock], *, gap_ratio: float = 0.18) -> list[list[PdfTextBlock]]:
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda block: block.x)
    page_width = max(block.x + block.width for block in ordered)
    gap = page_width * gap_ratio
    columns: list[list[PdfTextBlock]] = []
    for block in ordered:
        for column in columns:
            right = max(item.x + item.width for item in column)
            if block.x <= right + gap:
                column.append(block)
                break
        else:
            columns.append([block])
    return [sorted(column, key=lambda item: (item.y, item.x)) for column in columns]


def order_columns(columns: list[list[PdfTextBlock]], order: list[int]) -> str:
    return "\n".join(block.text.strip() for index in order for block in columns[index] if block.text.strip())
