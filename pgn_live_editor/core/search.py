"""Busca estruturada no texto bruto e nas partidas reconhecidas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import chess


@dataclass(frozen=True)
class SearchHit:
    kind: str
    query: str
    game_index: int
    start: int
    end: int
    label: str


def search_text(raw_text: str, query: str) -> list[SearchHit]:
    needle = query.strip()
    if not needle:
        return []
    hits: list[SearchHit] = []
    lower_text = raw_text.casefold()
    lower_needle = needle.casefold()
    offset = 0
    while True:
        start = lower_text.find(lower_needle, offset)
        if start < 0:
            break
        hits.append(SearchHit("texto", needle, -1, start, start + len(needle), f'Texto: "{needle}"'))
        offset = start + max(1, len(needle))
    return hits


def _mainline_moves(analysis: Any) -> list[Any]:
    return list(getattr(getattr(analysis, "ast", None), "moves", ()) or ())


def search_san_sequence(analyses: list[Any], query: str) -> list[SearchHit]:
    normalized = [token.strip() for token in query.split() if token.strip()]
    if not normalized:
        return []
    hits: list[SearchHit] = []
    wanted = [value.casefold() for value in normalized]
    for game_index, analysis in enumerate(analyses):
        moves = _mainline_moves(analysis)
        sans = [getattr(move, "display_san", getattr(move, "san", "")) for move in moves]
        for index in range(len(sans) - len(normalized) + 1):
            if [value.casefold() for value in sans[index : index + len(normalized)]] != wanted:
                continue
            first = moves[index]
            last = moves[index + len(normalized) - 1]
            hits.append(
                SearchHit(
                    "san",
                    query,
                    game_index,
                    getattr(first, "start_index", 0),
                    getattr(last, "end_index", getattr(last, "start_index", 0)),
                    f"Partida {game_index + 1}: {' '.join(sans[index : index + len(normalized)])}",
                )
            )
    return hits


def search_fen(analyses: list[Any], query: str) -> list[SearchHit]:
    needle = query.strip().casefold()
    if not needle:
        return []
    hits: list[SearchHit] = []
    for game_index, analysis in enumerate(analyses):
        try:
            board = chess.Board(getattr(analysis, "start_fen", chess.STARTING_FEN))
        except ValueError:
            continue
        moves = _mainline_moves(analysis)
        for move in moves:
            if board.fen().casefold() == needle or board.board_fen().casefold() == needle:
                start = getattr(move, "start_index", 0)
                hits.append(
                    SearchHit(
                        "fen",
                        query,
                        game_index,
                        start,
                        getattr(move, "end_index", start),
                        f"Partida {game_index + 1}: posição antes de {getattr(move, 'display_san', move.san)}",
                    )
                )
            if not getattr(move, "is_valid", False):
                break
            try:
                board.push_san(getattr(move, "display_san", move.san))
            except ValueError:
                break
        if board.fen().casefold() == needle or board.board_fen().casefold() == needle:
            if moves:
                last = moves[-1]
                hits.append(
                    SearchHit(
                        "fen",
                        query,
                        game_index,
                        last.start_index,
                        last.end_index,
                        f"Partida {game_index + 1}: posição final",
                    )
                )
    return hits
