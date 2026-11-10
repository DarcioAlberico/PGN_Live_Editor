"""Registro de reconhecimento tipado: combinacoes ilegais nao se constroem (R-302a).

L-10: `altered` era uma flag gravada, nao derivada da decisao -- a decisao
`Qh5` sobre `Qh4` aparecia como "nao alterada" e o filtro "alterados" a perdia.
L-15: `accept_identical` aceitava um lote sem decisao; e "comentado" ->
"rejeitado" deixava o `comment_text` velho no registro.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.recognition_record import (
    RecognitionRecord,
    RecordSource,
    ReviewAction,
    build_recognition_records,
)
from pgn_live_editor.core.review_queue import ReviewFilter, accept_identical, apply_batch_action, filter_records


def record(source_id: str = "a", *, raw: str = "Qh4", **changes) -> RecognitionRecord:
    values = dict(
        source_id=source_id,
        raw_text=raw,
        normalized_text=raw,
        interpretation="MOVE_CANDIDATE",
        confidence="low",
        source=RecordSource(start=0, end=len(raw)),
    )
    values.update(changes)
    return RecognitionRecord(**values)


class IllegalStatesTests(unittest.TestCase):
    def test_commented_without_comment_text_is_refused(self):
        with self.assertRaises(ValueError):
            record(action=ReviewAction.COMMENTED)

    def test_comment_text_outside_commented_is_refused(self):
        with self.assertRaises(ValueError):
            record(action=ReviewAction.REJECTED, comment_text="velho")

    def test_accepted_with_an_empty_decision_is_refused(self):
        with self.assertRaises(ValueError):
            record(action=ReviewAction.ACCEPTED, decision="")

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            ReviewAction("silence")


class AlteredTests(unittest.TestCase):
    def test_altered_follows_the_decision_not_a_stored_flag(self):
        decided = record(decision="Qh5")

        self.assertTrue(decided.altered)
        self.assertFalse(record().altered)
        self.assertEqual(["a"], [r.source_id for r in filter_records([decided], ReviewFilter(altered="yes"))])

    def test_a_stored_altered_flag_is_ignored_when_loading(self):
        payload = record(decision="Qh5").to_json() | {"altered": False}

        self.assertTrue(RecognitionRecord.from_json(payload).altered)


class TransitionTests(unittest.TestCase):
    def test_leaving_commented_drops_the_comment_text(self):
        commented = record().decide(ReviewAction.COMMENTED)

        self.assertEqual("Qh4", commented.comment_text)
        self.assertIsNone(commented.decide(ReviewAction.REJECTED).comment_text)

    def test_accepting_identical_proposals_records_what_was_accepted(self):
        records = [record("a", decision="Qh5"), record("b", decision="Qh5")]

        updated, changed = accept_identical(records, {"a", "b"})

        self.assertEqual(2, changed)
        self.assertEqual([(ReviewAction.ACCEPTED, "Qh5")] * 2, [(r.action, r.decision) for r in updated])

    def test_a_batch_without_a_correction_is_not_accepted(self):
        records = [record("a"), record("b")]

        updated, changed = accept_identical(records, {"a", "b"})

        self.assertEqual(0, changed)
        self.assertEqual(records, updated)

    def test_batch_comment_then_reject_leaves_no_comment_text(self):
        records, _ = apply_batch_action([record()], {"a"}, ReviewAction.COMMENTED)
        records, _ = apply_batch_action(records, {"a"}, ReviewAction.REJECTED)

        self.assertIsNone(records[0].comment_text)


class JsonBoundaryTests(unittest.TestCase):
    def test_round_trip(self):
        for item in build_recognition_records("1. e4 Xf6 {nota} 2. Nf3"):
            with self.subTest(raw=item.raw_text):
                self.assertEqual(item, RecognitionRecord.from_json(item.to_json()))

    def test_damaged_payloads_are_repaired_not_fatal(self):
        commented = record().to_json() | {"action": "commented"}
        commented.pop("comment_text", None)

        self.assertEqual("Qh4", RecognitionRecord.from_json(commented).comment_text)
        self.assertEqual(ReviewAction.PENDING, RecognitionRecord.from_json(record().to_json() | {"action": "?"}).action)
        self.assertIsNone(RecognitionRecord.from_json({"raw_text": 3}))


if __name__ == "__main__":
    unittest.main()
