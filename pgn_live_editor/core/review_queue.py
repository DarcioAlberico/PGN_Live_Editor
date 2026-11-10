"""Fila deterministica de revisao dos registros de reconhecimento.

Este modulo nao conhece Qt nem o editor. Ele concentra as regras que tornam
acoes em lote seguras e, por isso, pode ser usado tanto pela interface quanto
por exportadores/diagnosticos. Registros sao imutaveis: cada acao devolve a
lista nova e quantos registros mudaram.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .recognition_record import RecognitionRecord, ReviewAction


@dataclass(frozen=True)
class ReviewFilter:
    game: int | None = None
    page: int | None = None
    confidence: str = "all"
    interpretation: str = "all"
    altered: str = "all"
    action: str = "pending"


def matches(record: RecognitionRecord, criteria: ReviewFilter) -> bool:
    if criteria.game is not None and record.game != criteria.game:
        return False
    if criteria.page is not None and record.source.page != criteria.page:
        return False
    if criteria.confidence != "all" and record.confidence != criteria.confidence:
        return False
    if criteria.interpretation != "all" and record.interpretation != criteria.interpretation:
        return False
    if criteria.altered != "all" and record.altered != (criteria.altered == "yes"):
        return False
    return criteria.action == "all" or record.action.value == criteria.action


def filter_records(
    records: Iterable[RecognitionRecord], criteria: ReviewFilter | None = None
) -> list[RecognitionRecord]:
    criteria = criteria or ReviewFilter()
    return [record for record in records if matches(record, criteria)]


def _decide_selected(
    records: list[RecognitionRecord], source_ids: set[str], action: ReviewAction, decision: str | None = None
) -> tuple[list[RecognitionRecord], int]:
    changed = 0
    result = []
    for record in records:
        if record.source_id in source_ids:
            updated = record.decide(action, decision=decision)
            if updated != record:
                changed += 1
            record = updated
        result.append(record)
    return result, changed


def accept_identical(records: list[RecognitionRecord], source_ids: set[str]) -> tuple[list[RecognitionRecord], int]:
    """Aceita um lote so quando todas as propostas sao a mesma correcao.

    Lote misto, proposta vazia ou lote sem correcao nenhuma (proposta igual ao
    bruto) e recusado inteiro -- e a guarda contra aplicar um candidato a
    ambiguidades de OCR que nao tem nada a ver entre si. O aceito leva a
    decisao: o registro diz o que entrou no texto.
    """
    selected = [record for record in records if record.source_id in source_ids]
    proposals = {record.proposal for record in selected}
    if not selected or len(proposals) != 1 or "" in proposals or not all(r.altered for r in selected):
        return records, 0
    return _decide_selected(records, source_ids, ReviewAction.ACCEPTED, decision=proposals.pop())


def apply_batch_action(
    records: list[RecognitionRecord], source_ids: set[str], action: ReviewAction
) -> tuple[list[RecognitionRecord], int]:
    """Aplica uma acao nao destrutiva a identidades exatas."""
    action = ReviewAction(action)
    if action is ReviewAction.ACCEPTED:
        raise ValueError("aceitar em lote e `accept_identical`")
    return _decide_selected(records, source_ids, action)


def reject_rule(records: list[RecognitionRecord], rule_id: str) -> tuple[list[RecognitionRecord], int]:
    """Rejeita todas as propostas pendentes de uma regra."""
    ids = {
        record.source_id for record in records if record.rule_id == rule_id and record.action is ReviewAction.PENDING
    }
    return apply_batch_action(records, ids, ReviewAction.REJECTED)
