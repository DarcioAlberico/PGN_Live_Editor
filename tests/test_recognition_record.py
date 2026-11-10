from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.recognition_record import (
    ReviewAction,
    build_recognition_records,
    pending_records,
    reconcile_records,
)
from pgn_live_editor.core.review_queue import apply_batch_action


def decide(records, raw_text, action):
    return [record.decide(action) if record.raw_text == raw_text else record for record in records]


class RecognitionRecordTests(unittest.TestCase):
    def test_identity_survives_text_inserted_before_token(self):
        original = decide(build_recognition_records("1.e4 Xf6"), "Xf6", ReviewAction.IGNORED)
        target = next(record for record in original if record.raw_text == "Xf6")

        shifted = build_recognition_records("{nota nova} 1.e4 Xf6")
        reconciled = reconcile_records(shifted, original)
        current = next(record for record in reconciled if record.raw_text == "Xf6")

        self.assertEqual(ReviewAction.IGNORED, current.action)
        self.assertNotEqual(target.source.start, current.source.start)
        self.assertNotEqual(target.source.document_sha256, current.source.document_sha256)

    def test_changed_token_does_not_inherit_old_decision(self):
        previous = decide(build_recognition_records("1.e4 Xf6"), "Xf6", ReviewAction.REJECTED)
        next_records = build_recognition_records("1.e4 Xg6")

        current = reconcile_records(next_records, previous)

        self.assertEqual(ReviewAction.PENDING, current[-1].action)

    def test_decision_survives_change_of_neighbouring_token(self):
        previous = decide(build_recognition_records("1.e4 Xf6"), "Xf6", ReviewAction.IGNORED)

        current = reconcile_records(build_recognition_records("1.e4 Xg6 Xf6"), previous)
        self.assertEqual(ReviewAction.IGNORED, current[-1].action)

    def test_context_is_evidence_not_identity(self):
        first = build_recognition_records("1.e4 Xf6")
        second = build_recognition_records("1.d4 Xf6")
        first_token = next(record for record in first if record.raw_text == "Xf6")
        second_token = next(record for record in second if record.raw_text == "Xf6")
        self.assertEqual(first_token.source_id, second_token.source_id)
        self.assertNotEqual(first_token.previous, second_token.previous)

    def test_bulk_actions_are_exact_and_pending_is_filtered(self):
        records = build_recognition_records("1.e4 Xf6")
        source_id = records[-1].source_id

        records, changed = apply_batch_action(records, {source_id}, ReviewAction.REJECTED)
        self.assertEqual(1, changed)
        self.assertEqual([], pending_records([records[-1]]))
        with self.assertRaises(ValueError):
            apply_batch_action(records, {source_id}, "silence")


if __name__ == "__main__":
    unittest.main()
