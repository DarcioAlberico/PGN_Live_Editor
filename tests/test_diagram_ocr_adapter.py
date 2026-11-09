from pathlib import Path

from pgn_live_editor.services.diagram_ocr import (
    NATIVE_SOURCE_ROOT,
    DiagramOcrAdapter,
    DiagramRecognition,
    configured_source_root,
)


def test_default_source_root_is_inside_this_project():
    assert configured_source_root() == NATIVE_SOURCE_ROOT


def test_source_root_can_be_explicitly_overridden(tmp_path: Path):
    assert configured_source_root(tmp_path) == tmp_path


def test_recognition_creates_pgn_headers_and_flags_review():
    result = DiagramRecognition(
        index=1,
        placement="8/8/8/8/8/8/4K3/4k3",
        side_to_move="b",
        fen="8/8/8/8/8/8/4K3/4k3 b - - 0 1",
        min_confidence=0.72,
        mean_confidence=0.94,
        uncertain_squares=(12,),
        rotation=180,
        orientation_ambiguous=False,
        is_legal=False,
        problems=("reis adjacentes",),
        bbox_pdf=(1.0, 2.0, 3.0, 4.0),
        detection_source="embedded-image",
        source_root="ChessVisionOFF_Puro",
    )
    assert result.needs_review
    assert result.pgn_headers() == {
        "SetUp": "1",
        "FEN": result.fen,
        "DiagramOCR": "1",
        "DiagramOCRConfidence": "0.7200",
        "DiagramOCRReview": "1",
    }


def test_recognition_record_contains_source_and_review_state():
    result = DiagramRecognition(
        index=3,
        placement="8/8/8/8/8/8/4K3/7k",
        side_to_move="w",
        fen="8/8/8/8/8/8/4K3/7k w - - 0 1",
        min_confidence=0.99,
        mean_confidence=0.995,
        uncertain_squares=(),
        rotation=0,
        orientation_ambiguous=False,
        is_legal=True,
        problems=(),
        bbox_pdf=(10.0, 20.0, 30.0, 40.0),
        detection_source="embedded-image",
        source_root="ChessVisionOFF_Puro",
    )
    record = result.to_record(pdf_path="livro.pdf", page=8)
    assert record["page"] == 8
    assert record["diagram"] == 3
    assert record["bbox_pdf"] == [10.0, 20.0, 30.0, 40.0]
    assert record["review"] == "accepted"


def test_adapter_uses_native_package_without_external_root():
    available, message = DiagramOcrAdapter().available()
    assert isinstance(available, bool)
    if not available:
        assert "OCR nativo" in message
