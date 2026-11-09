import _isolation  # noqa: F401

from pgn_live_editor.core.diagram_review import pending_records, set_review_state, upsert_record


def _record(page=1, diagram=1, review="pending"):
    return {"pdf_path": "livro.pdf", "page": page, "diagram": diagram, "review": review}


def test_pending_records_are_unique_and_filter_accepted():
    records = [_record(), _record(), _record(2), _record(3, review="accepted")]
    assert pending_records(records) == [_record(), _record(2)]


def test_upsert_does_not_duplicate_same_source():
    records = []
    assert upsert_record(records, _record())
    assert not upsert_record(records, _record())
    assert upsert_record(records, {**_record(), "review": "accepted"})
    assert records == [{**_record(), "review": "accepted"}]


def test_review_state_is_validated_and_persisted():
    records = [_record()]
    assert set_review_state(records, ("livro.pdf", 1, 1), "accepted")
    assert records[0]["review"] == "accepted"
    try:
        set_review_state(records, ("livro.pdf", 1, 1), "unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("estado inválido deveria falhar")
