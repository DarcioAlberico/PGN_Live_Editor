"""Registros auditáveis de cada token do texto bruto."""

from __future__ import annotations

import hashlib
from typing import Any

from .tokenizer import PGNTokenizer

RECOGNITION_SCHEMA_VERSION = "3"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_recognition_records(
    raw_text: str, *, analysis_version: str = RECOGNITION_SCHEMA_VERSION
) -> list[dict[str, Any]]:
    """Build audit records with an identity stable across surrounding edits.

    The text around a token is useful evidence, but it is not the token's
    identity.  Older records included both neighbours in ``source_id``; an
    insertion or correction next to an already reviewed token therefore made
    the old decision disappear on the next parse.  The identity now uses the
    token kind, its raw spelling and its occurrence in the document.  The
    neighbours remain available under ``context`` for diagnostics and future
    matching strategies.
    """
    tokens = list(PGNTokenizer().tokenize(raw_text))
    document_hash = _digest(raw_text)
    records: list[dict[str, Any]] = []
    occurrences: dict[tuple[str, str], int] = {}
    for index, token in enumerate(tokens):
        raw_value = raw_text[token.start_index : token.end_index]
        previous = tokens[index - 1].value if index else "<BOF>"
        following = tokens[index + 1].value if index + 1 < len(tokens) else "<EOF>"
        fingerprint = (token.type, raw_value)
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        source_id = _digest(f"{analysis_version}|{token.type}|{raw_value}|{occurrence}")[:24]
        recognized = token.type in {"SAN_MOVE", "MOVE_NUMBER", "NAG", "COMMENT", "RESULT", "NULL_MOVE"}
        confidence = "high" if recognized else "low" if token.type == "MOVE_CANDIDATE" else "medium"
        records.append(
            {
                "id": source_id,
                "source_id": source_id,
                "analysis_version": analysis_version,
                "source": {
                    "start": token.start_index,
                    "end": token.end_index,
                    "document_sha256": document_hash,
                },
                "context": {"previous": previous, "following": following},
                "document_sha256": document_hash,
                "raw_text": raw_value,
                "normalized_text": token.value,
                "interpretation": token.type,
                "candidates": [token.value] if token.type == "MOVE_CANDIDATE" else [],
                "confidence": confidence,
                "altered": raw_value != token.value,
                "action": "pending" if confidence == "low" else "accepted",
                "rule_id": "tokenizer",
                "decision": None,
            }
        )
    return records


def reconcile_records(
    fresh: list[dict[str, Any]],
    previous: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    legacy_ids: dict[str, dict[str, Any]] = {}
    legacy_keys: dict[tuple[str, str, int], dict[str, Any]] = {}
    occurrences: dict[tuple[str, str], int] = {}
    for item in previous:
        action = item.get("action", "pending")
        if action not in {"pending", "accepted", "rejected", "commented", "ignored"}:
            action = "pending"
        source_id = item.get("source_id") or item.get("id")
        if source_id:
            decisions[str(source_id)] = {"action": action, "decision": item.get("decision")}
        if item.get("id"):
            legacy_ids[str(item["id"])] = {"action": action, "decision": item.get("decision")}
        key = (str(item.get("interpretation", "")), str(item.get("raw_text", "")))
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        legacy_keys[(*key, occurrence)] = {"action": action, "decision": item.get("decision")}
    occurrences.clear()
    for item in fresh:
        decision = decisions.get(str(item.get("source_id")))
        if decision is None:
            source = item.get("source", {})
            old_id = _digest(f"1|{source.get('start')}|{source.get('end')}|{item.get('raw_text', '')}")[:24]
            decision = legacy_ids.get(old_id)
        if decision is None:
            key = (str(item.get("interpretation", "")), str(item.get("raw_text", "")))
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            decision = legacy_keys.get((*key, occurrence))
        if decision is not None:
            item["action"] = decision["action"]
            if decision.get("decision") is not None:
                item["decision"] = decision["decision"]
    return fresh


def pending_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return unresolved records without exposing the caller's list."""
    return [record for record in records if record.get("action", "pending") == "pending"]


def apply_action(records: list[dict[str, Any]], source_ids: set[str], action: str) -> int:
    """Apply a review action to exact identities and return the count."""
    if action not in {"accepted", "rejected", "commented", "ignored", "pending"}:
        raise ValueError(f"ação de revisão inválida: {action}")
    changed = 0
    for record in records:
        if str(record.get("source_id", record.get("id", ""))) in source_ids and record.get("action") != action:
            record["action"] = action
            changed += 1
    return changed
