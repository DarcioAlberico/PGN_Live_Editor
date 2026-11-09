import _isolation  # noqa: F401

from pgn_live_editor.core.review_queue import (
    ReviewFilter,
    accept_identical,
    apply_batch_action,
    filter_records,
    reject_rule,
)


def _record(source_id, *, decision="Nf3", rule="ocr", action="pending", game=0, page=2):
    return {
        "source_id": source_id,
        "raw_text": "Nf3",
        "normalized_text": "Nf3",
        "decision": decision,
        "rule_id": rule,
        "action": action,
        "confidence": "low",
        "interpretation": "MOVE_CANDIDATE",
        "game": game,
        "source": {"page": page},
        "altered": True,
    }


def test_filters_by_game_page_confidence_type_and_altered():
    records = [_record("a"), _record("b", game=1, page=3, decision="Bc4")]
    found = filter_records(
        records, ReviewFilter(game=1, page=3, confidence="low", interpretation="MOVE_CANDIDATE", altered="yes")
    )
    assert [item["source_id"] for item in found] == ["b"]


def test_accept_identical_is_atomic_for_mixed_proposals():
    records = [_record("a"), _record("b", decision="Bc4")]
    assert accept_identical(records, {"a", "b"}) == 0
    assert all(item["action"] == "pending" for item in records)


def test_accept_identical_and_comment_preserve_source():
    records = [_record("a"), _record("b")]
    assert accept_identical(records, {"a", "b"}) == 2
    records[0]["action"] = "pending"
    assert apply_batch_action(records, {"a"}, "commented") == 1
    assert records[0]["comment_text"] == "Nf3"


def test_reject_rule_only_touches_pending_records():
    records = [_record("a"), _record("b", action="accepted"), _record("c", rule="other")]
    assert reject_rule(records, "ocr") == 1
    assert [item["action"] for item in records] == ["rejected", "accepted", "pending"]
