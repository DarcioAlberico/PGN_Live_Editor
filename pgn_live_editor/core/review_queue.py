"""Fila determinística de revisão dos registros de reconhecimento.

Este módulo não conhece Qt nem o editor.  Ele concentra as regras que tornam
ações em lote seguras e, por isso, pode ser usado tanto pela interface quanto
por exportadores/diagnósticos.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

REVIEW_ACTIONS = {"pending", "accepted", "rejected", "commented", "ignored"}


@dataclass(frozen=True)
class ReviewFilter:
    game: int | None = None
    page: int | None = None
    confidence: str = "all"
    interpretation: str = "all"
    altered: str = "all"
    action: str = "pending"


def _source(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("source", {})
    return value if isinstance(value, dict) else {}


def record_is_altered(record: dict[str, Any]) -> bool:
    """Whether recognition changed or proposed changing the source token."""
    if "altered" in record:
        return bool(record["altered"])
    raw = str(record.get("raw_text", ""))
    normalized = str(record.get("normalized_text", raw))
    decision = record.get("decision")
    return raw != normalized or (decision not in (None, "", raw))


def matches(record: dict[str, Any], criteria: ReviewFilter) -> bool:
    source = _source(record)
    if criteria.game is not None and record.get("game") != criteria.game:
        return False
    if criteria.page is not None and source.get("page") != criteria.page:
        return False
    if criteria.confidence != "all" and str(record.get("confidence", "")) != criteria.confidence:
        return False
    if criteria.interpretation != "all" and str(record.get("interpretation", "")) != criteria.interpretation:
        return False
    if criteria.altered != "all":
        expected = criteria.altered == "yes"
        if record_is_altered(record) != expected:
            return False
    if criteria.action != "all" and str(record.get("action", "pending")) != criteria.action:
        return False
    return True


def filter_records(records: Iterable[dict[str, Any]], criteria: ReviewFilter | None = None) -> list[dict[str, Any]]:
    criteria = criteria or ReviewFilter()
    return [record for record in records if matches(record, criteria)]


def _selected(records: list[dict[str, Any]], source_ids: set[str]) -> list[dict[str, Any]]:
    return [record for record in records if str(record.get("source_id", record.get("id", ""))) in source_ids]


def accept_identical(records: list[dict[str, Any]], source_ids: set[str]) -> int:
    """Accept a batch only when every proposal is the same correction.

    An empty proposal or a mixed batch is rejected as a whole.  This is the
    guard against applying one candidate to unrelated OCR ambiguities.
    """
    selected = _selected(records, source_ids)
    proposals = {str(record.get("decision") or record.get("normalized_text", "")) for record in selected}
    if not selected or "" in proposals or len(proposals) != 1:
        return 0
    changed = 0
    for record in selected:
        if record.get("action") != "accepted":
            record["action"] = "accepted"
            changed += 1
    return changed


def apply_batch_action(records: list[dict[str, Any]], source_ids: set[str], action: str) -> int:
    """Apply a non-destructive review action to exact record identities."""
    if action not in REVIEW_ACTIONS - {"accepted"}:
        raise ValueError(f"ação em lote inválida: {action}")
    changed = 0
    for record in _selected(records, source_ids):
        if record.get("action") != action:
            record["action"] = action
            changed += 1
        if action == "commented":
            # Keep the source visible when the UI converts the span to a PGN
            # comment; export and audit tools can use this without rereading it.
            record.setdefault("comment_text", str(record.get("raw_text", "")))
    return changed


def reject_rule(records: list[dict[str, Any]], rule_id: str) -> int:
    """Reject all current proposals produced by one exact rule."""
    ids = {
        str(record.get("source_id", record.get("id", "")))
        for record in records
        if str(record.get("rule_id", "")) == rule_id and record.get("action", "pending") == "pending"
    }
    return apply_batch_action(records, ids, "rejected")
