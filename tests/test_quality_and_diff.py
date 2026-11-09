import _isolation  # noqa: F401

from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.project_diff import compare_projects
from pgn_live_editor.core.quality_report import validate_game
from pgn_live_editor.core.tokenizer import PGNTokenizer
from pgn_live_editor.services.project_service import Project, ProjectState


def test_quality_report_detects_pending_or_invalid_moves():
    ast = TolerantParser().parse(PGNTokenizer().tokenize("1.e4 Xf6"))
    findings = validate_game(ast, 2)
    assert findings
    assert all(item.game == 2 for item in findings)


def test_project_diff_reports_text_and_decision_changes():
    old = Project(raw_text="1.e4", state=ProjectState(recognition_records=[{"source_id": "a", "action": "pending"}]))
    new = Project(
        raw_text="1.e4 e5", state=ProjectState(recognition_records=[{"source_id": "a", "action": "accepted"}])
    )
    diff = compare_projects(old, new)
    assert diff.changed
    assert diff.text_changed
    assert diff.decisions_added == 1
