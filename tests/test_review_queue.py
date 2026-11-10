import _isolation  # noqa: F401

from pgn_live_editor.core.recognition_record import RecognitionRecord, RecordSource, ReviewAction
from pgn_live_editor.core.review_queue import (
    ReviewFilter,
    accept_identical,
    apply_batch_action,
    filter_records,
    reject_rule,
)


def _record(source_id, *, decision="Nf3", rule="ocr", action=ReviewAction.PENDING, game=0, page=2):
    return RecognitionRecord(
        source_id=source_id,
        raw_text="Nf8",
        normalized_text="Nf8",
        interpretation="MOVE_CANDIDATE",
        confidence="low",
        source=RecordSource(0, 3, page=page),
        action=action,
        decision=decision,
        rule_id=rule,
        game=game,
    )


def test_filters_by_game_page_confidence_type_and_altered():
    records = [_record("a"), _record("b", game=1, page=3, decision="Bc4")]
    found = filter_records(
        records, ReviewFilter(game=1, page=3, confidence="low", interpretation="MOVE_CANDIDATE", altered="yes")
    )
    assert [item.source_id for item in found] == ["b"]


def test_accept_identical_is_atomic_for_mixed_proposals():
    records = [_record("a"), _record("b", decision="Bc4")]
    updated, changed = accept_identical(records, {"a", "b"})
    assert changed == 0
    assert all(item.action is ReviewAction.PENDING for item in updated)


def test_accept_identical_and_comment_preserve_source():
    records = [_record("a"), _record("b")]
    records, changed = accept_identical(records, {"a", "b"})
    assert changed == 2
    records, changed = apply_batch_action(records, {"a"}, ReviewAction.COMMENTED)
    assert changed == 1
    assert records[0].comment_text == "Nf8"


def test_reject_rule_only_touches_pending_records():
    records = [_record("a"), _record("b", action=ReviewAction.ACCEPTED), _record("c", rule="other")]
    records, changed = reject_rule(records, "ocr")
    assert changed == 1
    assert [item.action.value for item in records] == ["rejected", "accepted", "pending"]
