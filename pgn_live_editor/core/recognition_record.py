"""Registros auditaveis de cada token do texto bruto (SPEC_V3 §6.1, R-302a).

O registro e um valor imutavel: uma decisao humana produz um registro novo
(`decide`). As combinacoes que nao fazem sentido -- comentado sem texto de
comentario, texto de comentario fora de "comentado", aceito com decisao vazia
-- nao se constroem. Dicionarios crus so existem na fronteira do JSON
(`to_json`/`from_json`), e o carregador conserta o que vier estragado em vez
de recusar o projeto.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

from .tokenizer import PGNTokenizer

RECOGNITION_SCHEMA_VERSION = "3"


class ReviewAction(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMMENTED = "commented"
    IGNORED = "ignored"


@dataclass(frozen=True)
class RecordSource:
    """Onde o token estava quando o registro foi feito."""

    start: int
    end: int
    document_sha256: str = ""
    page: int | None = None
    pdf_path: str | None = None


@dataclass(frozen=True)
class RecognitionRecord:
    source_id: str
    raw_text: str
    normalized_text: str
    interpretation: str
    confidence: str
    source: RecordSource
    action: ReviewAction = ReviewAction.PENDING
    # O texto que o revisor escolheu no lugar do bruto; `None` = sem correcao.
    decision: str | None = None
    # Existe se e somente se `action` e COMMENTED.
    comment_text: str | None = None
    rule_id: str = "tokenizer"
    analysis_version: str = RECOGNITION_SCHEMA_VERSION
    candidates: tuple[str, ...] = ()
    previous: str = "<BOF>"
    following: str = "<EOF>"
    game: int | None = None
    decided_at: float | None = None

    def __post_init__(self) -> None:
        action = ReviewAction(self.action)
        object.__setattr__(self, "action", action)
        if (action is ReviewAction.COMMENTED) != (self.comment_text is not None):
            raise ValueError(f"comment_text so existe em 'commented' (acao {action.value!r}, {self.comment_text!r})")
        if action is ReviewAction.ACCEPTED and self.decision == "":
            raise ValueError("'accepted' com decisao vazia")

    @property
    def proposal(self) -> str:
        """O que entra no texto se o registro for aceito."""
        return self.decision if self.decision is not None else self.normalized_text

    @property
    def is_human_decision(self) -> bool:
        """Se alguem decidiu algo sobre este token (e nao so o reconhecimento automatico)."""
        return (
            self.decided_at is not None
            or self.decision is not None
            or self.action in (ReviewAction.REJECTED, ReviewAction.COMMENTED, ReviewAction.IGNORED)
        )

    @property
    def altered(self) -> bool:
        """Se o reconhecimento ou o revisor mudou (ou propoe mudar) o token. Derivado, nunca gravado."""
        return self.proposal != self.raw_text

    def decide(
        self, action: ReviewAction, *, decision: str | None = None, at: float | None = None
    ) -> RecognitionRecord:
        """O registro depois de uma decisao. Sair de COMMENTED descarta o texto do comentario."""
        return replace(
            self,
            action=action,
            decision=decision if decision is not None else self.decision,
            comment_text=(self.comment_text or self.raw_text) if action is ReviewAction.COMMENTED else None,
            decided_at=at if at is not None else self.decided_at,
        )

    def with_provenance(self, *, game: int | None, page: int | None, pdf_path: str | None) -> RecognitionRecord:
        return replace(self, game=game, source=replace(self.source, page=page, pdf_path=pdf_path))

    # ------------------------------------------------------------------
    # Fronteira JSON
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        source: dict[str, Any] = {
            "start": self.source.start,
            "end": self.source.end,
            "document_sha256": self.source.document_sha256,
        }
        if self.source.page is not None:
            source["page"] = self.source.page
        if self.source.pdf_path is not None:
            source["pdf_path"] = self.source.pdf_path
        payload: dict[str, Any] = {
            "id": self.source_id,
            "source_id": self.source_id,
            "analysis_version": self.analysis_version,
            "source": source,
            "context": {"previous": self.previous, "following": self.following},
            "document_sha256": self.source.document_sha256,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "interpretation": self.interpretation,
            "candidates": list(self.candidates),
            "confidence": self.confidence,
            # So para leitores externos; `from_json` recalcula.
            "altered": self.altered,
            "action": self.action.value,
            "rule_id": self.rule_id,
            "decision": self.decision,
        }
        for name in ("comment_text", "game", "decided_at"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_json(cls, payload: object) -> RecognitionRecord | None:
        """O registro de um JSON gravado, consertado se preciso; `None` se nao der para salvar."""
        if not isinstance(payload, Mapping):
            return None
        raw_text = payload.get("raw_text")
        source_id = payload.get("source_id") or payload.get("id")
        if not isinstance(raw_text, str) or not isinstance(source_id, str):
            return None
        source = payload.get("source")
        source = source if isinstance(source, Mapping) else {}
        context = payload.get("context")
        context = context if isinstance(context, Mapping) else {}
        try:
            action = ReviewAction(payload.get("action", "pending"))
        except ValueError:
            action = ReviewAction.PENDING
        decision = _text_or_none(payload.get("decision"))
        if action is ReviewAction.ACCEPTED and decision == "":
            decision = None
        comment_text = _text_or_none(payload.get("comment_text")) if action is ReviewAction.COMMENTED else None
        if action is ReviewAction.COMMENTED and comment_text is None:
            comment_text = raw_text
        start = _int_or_none(source.get("start"))
        end = _int_or_none(source.get("end"))
        candidates = payload.get("candidates")
        return cls(
            source_id=source_id,
            raw_text=raw_text,
            normalized_text=_text_or_none(payload.get("normalized_text")) or raw_text,
            interpretation=str(payload.get("interpretation", "")),
            confidence=str(payload.get("confidence", "")),
            source=RecordSource(
                start=start if start is not None else -1,
                end=end if end is not None else -1,
                document_sha256=str(source.get("document_sha256") or payload.get("document_sha256") or ""),
                page=_int_or_none(source.get("page")),
                pdf_path=_text_or_none(source.get("pdf_path")),
            ),
            action=action,
            decision=decision,
            comment_text=comment_text,
            rule_id=str(payload.get("rule_id", "tokenizer")),
            analysis_version=str(payload.get("analysis_version", RECOGNITION_SCHEMA_VERSION)),
            candidates=tuple(str(c) for c in candidates) if isinstance(candidates, list) else (),
            previous=str(context.get("previous", "<BOF>")),
            following=str(context.get("following", "<EOF>")),
            game=_int_or_none(payload.get("game")),
            decided_at=_float_or_none(payload.get("decided_at")),
        )


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def records_from_json(payload: object) -> tuple[list[RecognitionRecord], int]:
    """Os registros de uma lista JSON e quantos itens foram descartados."""
    if not isinstance(payload, list):
        return [], 0 if payload in (None, []) else 1
    records = [RecognitionRecord.from_json(item) for item in payload]
    kept = [item for item in records if item is not None]
    return kept, len(records) - len(kept)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_recognition_records(
    raw_text: str, *, analysis_version: str = RECOGNITION_SCHEMA_VERSION
) -> list[RecognitionRecord]:
    """Um registro por token, com identidade estavel diante de edicoes em volta.

    O texto vizinho e evidencia, nao identidade: entra em `previous`/`following`
    para diagnostico, mas o `source_id` usa so o tipo do token, a grafia bruta e
    a ocorrencia no documento.
    """
    tokens = list(PGNTokenizer().tokenize(raw_text))
    document_hash = _digest(raw_text)
    records: list[RecognitionRecord] = []
    occurrences: dict[tuple[str, str], int] = {}
    for index, token in enumerate(tokens):
        raw_value = raw_text[token.start_index : token.end_index]
        fingerprint = (token.type, raw_value)
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        recognized = token.type in {"SAN_MOVE", "MOVE_NUMBER", "NAG", "COMMENT", "RESULT", "NULL_MOVE"}
        confidence = "high" if recognized else "low" if token.type == "MOVE_CANDIDATE" else "medium"
        records.append(
            RecognitionRecord(
                source_id=_digest(f"{analysis_version}|{token.type}|{raw_value}|{occurrence}")[:24],
                raw_text=raw_value,
                normalized_text=token.value,
                interpretation=token.type,
                confidence=confidence,
                source=RecordSource(token.start_index, token.end_index, document_hash),
                action=ReviewAction.PENDING if confidence == "low" else ReviewAction.ACCEPTED,
                analysis_version=analysis_version,
                candidates=(token.value,) if token.type == "MOVE_CANDIDATE" else (),
                previous=tokens[index - 1].value if index else "<BOF>",
                following=tokens[index + 1].value if index + 1 < len(tokens) else "<EOF>",
            )
        )
    return records


def reconcile_with_orphans(
    fresh: Iterable[RecognitionRecord],
    previous: Iterable[RecognitionRecord],
) -> tuple[list[RecognitionRecord], list[RecognitionRecord]]:
    """Leva as decisoes dos registros antigos para os novos, por alinhamento (SPEC_V3 §6.2).

    As duas sequencias de (tipo, grafia bruta) sao alinhadas como um diff:
    so tokens que casam num trecho igual herdam a decisao, um para um. Antes a
    chave era (tipo, grafia, ocorrencia), e inserir um `Qz9` antes de outro
    passava a decisao de cada `Qz9` para o vizinho (L-04). Um token que mudou
    de grafia nao herda nada -- a decisao era sobre o texto antigo.

    Devolve os registros novos e os **orfaos**: decisoes humanas cujo token
    sumiu, para a interface mostrar em vez de perder em silencio.
    """
    new = list(fresh)
    old = list(previous)
    pairs = _aligned_pairs([_alignment_key(r) for r in old], [_alignment_key(r) for r in new])
    result = list(new)
    for old_index, new_index in pairs:
        decided = old[old_index]
        if not decided.is_human_decision and decided.action is new[new_index].action:
            continue
        result[new_index] = replace(
            new[new_index],
            action=decided.action,
            decision=decided.decision if decided.decision is not None else new[new_index].decision,
            comment_text=decided.comment_text,
            decided_at=decided.decided_at,
        )
    matched = {old_index for old_index, _ in pairs}
    orphans = [record for index, record in enumerate(old) if index not in matched and record.is_human_decision]
    return result, orphans


def _alignment_key(record: RecognitionRecord) -> tuple[str, str]:
    return record.interpretation, record.raw_text


# Acima disto (produto dos tamanhos de uma lacuna sem ancora), o
# `SequenceMatcher` -- quadratico -- trava a interface num livro inteiro.
_SMALL_GAP = 40_000


def _aligned_pairs(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> list[tuple[int, int]]:
    """Pares (indice antigo, indice novo) dos tokens que casam, em ordem nos dois lados.

    Diff em estilo *patience*: corta prefixo e sufixo iguais; no meio, ancora
    nos tokens que aparecem uma vez so em cada lado (numeros de lance, quase
    sempre), na maior sequencia crescente; e repete em cada lacuna. So lacunas
    pequenas e sem ancora vao ao `SequenceMatcher`. Lacuna grande sem ancora nao
    casa nada: melhor uma decisao orfa, visivel, que uma decisao no token errado.
    """
    pairs: list[tuple[int, int]] = []
    ranges = [(0, len(old), 0, len(new))]
    while ranges:
        alo, ahi, blo, bhi = ranges.pop()
        while alo < ahi and blo < bhi and old[alo] == new[blo]:
            pairs.append((alo, blo))
            alo, blo = alo + 1, blo + 1
        while alo < ahi and blo < bhi and old[ahi - 1] == new[bhi - 1]:
            ahi, bhi = ahi - 1, bhi - 1
            pairs.append((ahi, bhi))
        if alo == ahi or blo == bhi:
            continue
        anchors = _unique_anchors(old, alo, ahi, new, blo, bhi)
        if anchors:
            for a, b in anchors:
                pairs.append((a, b))
                ranges.append((alo, a, blo, b))
                alo, blo = a + 1, b + 1
            ranges.append((alo, ahi, blo, bhi))
        elif (ahi - alo) * (bhi - blo) <= _SMALL_GAP:
            matcher = SequenceMatcher(None, old[alo:ahi], new[blo:bhi], autojunk=False)
            for block in matcher.get_matching_blocks():
                pairs.extend((alo + block.a + step, blo + block.b + step) for step in range(block.size))
    return pairs


def _unique_anchors(
    old: list[tuple[str, str]], alo: int, ahi: int, new: list[tuple[str, str]], blo: int, bhi: int
) -> list[tuple[int, int]]:
    """Tokens unicos nos dois trechos, na maior sequencia crescente nos dois lados."""
    old_count = Counter(old[alo:ahi])
    new_count = Counter(new[blo:bhi])
    where_old = {old[i]: i for i in range(alo, ahi) if old_count[old[i]] == 1}
    candidates = [(where_old[new[j]], j) for j in range(blo, bhi) if new_count[new[j]] == 1 and new[j] in where_old]
    # Maior subsequencia crescente no indice antigo (os candidatos ja vem em
    # ordem no novo): O(k log k), com os predecessores para reconstruir.
    tails: list[int] = []
    tail_index: list[int] = []
    parent = [-1] * len(candidates)
    for position, (a, _) in enumerate(candidates):
        slot = bisect_left(tails, a)
        if slot == len(tails):
            tails.append(a)
            tail_index.append(position)
        else:
            tails[slot] = a
            tail_index[slot] = position
        parent[position] = tail_index[slot - 1] if slot else -1
    chain: list[tuple[int, int]] = []
    position = tail_index[-1] if tail_index else -1
    while position != -1:
        chain.append(candidates[position])
        position = parent[position]
    return chain[::-1]


def pending_records(records: Iterable[RecognitionRecord]) -> list[RecognitionRecord]:
    return [record for record in records if record.action is ReviewAction.PENDING]
