from pathlib import Path

import _isolation  # noqa: F401
import pytest

from pgn_live_editor.core.pdf_import import PdfImportSession
from pgn_live_editor.services.export_service import decide_export, write_pgn


def test_pdf_import_session_preserves_origin_and_flags_scanned_page(tmp_path: Path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    session = PdfImportSession.from_page_text(pdf, 4, "")
    assert session.requires_ocr
    assert session.artifacts["extraction"].source.method == "OCR-required"
    restored = PdfImportSession.from_dict(session.to_dict())
    assert restored.source_sha256 == session.source_sha256
    assert restored.pages == [4]


def test_pdf_import_session_stages_are_validated(tmp_path: Path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    session = PdfImportSession.from_page_text(pdf, 1, "1. e4")
    session.put("cleanup", "1. e4")
    assert session.preview() == "1. e4"
    with pytest.raises(ValueError):
        session.put("unknown", "")


@pytest.mark.parametrize(
    ("policy", "pending", "allowed"),
    [("block_pending", 2, False), ("confirm_pending", 2, True), ("quarantine", 2, True), ("block_pending", 0, True)],
)
def test_export_policy_is_explicit(policy, pending, allowed):
    assert decide_export(policy, pending=pending).allowed is allowed


def test_write_pgn_rotates_recoverable_backups(tmp_path: Path):
    target = tmp_path / "game.pgn"
    for value in ("one", "two", "three", "four"):
        write_pgn(target, value)
    assert target.read_text(encoding="utf-8").strip() == "four"
    assert target.with_suffix(".pgn.bak").read_text(encoding="utf-8").strip() == "three"
    assert target.with_suffix(".pgn.bak.1").read_text(encoding="utf-8").strip() == "two"
    assert target.with_suffix(".pgn.bak.2").read_text(encoding="utf-8").strip() == "one"
