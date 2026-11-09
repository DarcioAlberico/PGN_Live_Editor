from __future__ import annotations

import unittest

from pgn_live_editor.core.recognition_record import (
    apply_action,
    build_recognition_records,
    pending_records,
    reconcile_records,
)


class RecognitionRecordTests(unittest.TestCase):
    def test_identity_survives_text_inserted_before_token(self):
        original = build_recognition_records("1.e4 Xf6")
        target = next(record for record in original if record["raw_text"] == "Xf6")
        target["action"] = "ignored"

        shifted = build_recognition_records("{nota nova} 1.e4 Xf6")
        reconciled = reconcile_records(shifted, original)
        current = next(record for record in reconciled if record["raw_text"] == "Xf6")

        self.assertEqual("ignored", current["action"])
        self.assertNotEqual(target["source"]["start"], current["source"]["start"])
        self.assertNotEqual(target["document_sha256"], current["document_sha256"])

    def test_changed_token_does_not_inherit_old_decision(self):
        previous = build_recognition_records("1.e4 Xf6")
        next_records = build_recognition_records("1.e4 Xg6")
        previous[-1]["action"] = "rejected"

        current = reconcile_records(next_records, previous)

        self.assertEqual("pending", current[-1]["action"])

    def test_decision_survives_change_of_neighbouring_token(self):
        previous = build_recognition_records("1.e4 Xf6")
        target = next(record for record in previous if record["raw_text"] == "Xf6")
        target["action"] = "ignored"

        current = reconcile_records(build_recognition_records("1.e4 Xg6 Xf6"), previous)
        self.assertEqual("ignored", current[-1]["action"])

    def test_context_is_evidence_not_identity(self):
        first = build_recognition_records("1.e4 Xf6")
        second = build_recognition_records("1.d4 Xf6")
        first_token = next(record for record in first if record["raw_text"] == "Xf6")
        second_token = next(record for record in second if record["raw_text"] == "Xf6")
        self.assertEqual(first_token["source_id"], second_token["source_id"])
        self.assertNotEqual(first_token["context"], second_token["context"])

    def test_bulk_actions_are_exact_and_pending_is_filtered(self):
        records = build_recognition_records("1.e4 Xf6")
        source_id = str(records[-1]["source_id"])

        self.assertEqual(1, apply_action(records, {source_id}, "rejected"))
        self.assertEqual([], pending_records([records[-1]]))
        with self.assertRaises(ValueError):
            apply_action(records, {source_id}, "silence")


if __name__ == "__main__":
    unittest.main()
