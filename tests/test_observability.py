from pgn_live_editor.services.observability import summarize_metrics


def test_metric_summary_is_aggregated_without_document_content():
    summary = summarize_metrics(
        [
            {"operation": "parse", "duration_ms": 10, "characters": 100},
            {"operation": "parse", "duration_ms": 30, "characters": 200},
            {"operation": "export_pgn", "duration_ms": 4},
        ]
    )
    assert summary["parse"] == {"count": 2.0, "average_ms": 20.0, "max_ms": 30.0}
    assert "characters" not in summary["parse"]
