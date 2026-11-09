"""Operações puras sobre a fila de revisão de diagramas OCR."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def record_key(record: dict[str, Any]) -> tuple[str, int, int]:
    """Identidade de uma leitura: documento, página e índice do diagrama."""
    return str(record.get("pdf_path", "")), int(record.get("page", 0)), int(record.get("diagram", 0))


def pending_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devolve pendências únicas, em ordem de origem."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for record in records:
        if str(record.get("review", "pending")) != "pending":
            continue
        key = record_key(record)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def upsert_record(records: list[dict[str, Any]], record: dict[str, Any]) -> bool:
    """Insere ou atualiza uma leitura; devolve se houve alteração."""
    key = record_key(record)
    for index, current in enumerate(records):
        if record_key(current) == key:
            if current == record:
                return False
            records[index] = dict(record)
            return True
    records.append(dict(record))
    return True


def set_review_state(records: list[dict[str, Any]], key: tuple[str, int, int], state: str) -> bool:
    """Altera apenas estados conhecidos e preserva o restante do registro."""
    if state not in {"pending", "accepted", "rejected", "commented", "ignored"}:
        raise ValueError(f"Estado de revisão inválido: {state}")
    for record in records:
        if record_key(record) == key:
            if record.get("review") == state:
                return False
            record["review"] = state
            return True
    return False
