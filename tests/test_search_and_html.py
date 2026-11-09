import _isolation  # noqa: F401

from pgn_live_editor.core.search import search_fen, search_san_sequence, search_text
from pgn_live_editor.services.export_service import build_html
from pgn_live_editor.services.parse_worker import ParsePipeline, build_games_for_export

BOOK = '[Event "Busca"]\n\n1. e4 e5 2. Nf3 Nc6 *\n'


def test_text_search_is_case_insensitive_and_returns_spans():
    hits = search_text("Abertura\nABERTURA", "abertura")
    assert [(hit.start, hit.end) for hit in hits] == [(0, 8), (9, 17)]


def test_san_and_fen_search_use_move_spans():
    analyses = ParsePipeline().analyze_all(BOOK)
    assert search_san_sequence(analyses, "e4 e5")
    assert search_fen(analyses, "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")


def test_html_export_contains_game_title_moves_comments_and_nags():
    games = build_games_for_export(ParsePipeline().analyze_all('[White "Ana"]\n\n1. e4 $1 e5 *\n'))
    html = build_html(games)
    assert "Ana" in html
    assert "e4" in html
    assert "$1" in html
    assert html.startswith("<!doctype html>")
