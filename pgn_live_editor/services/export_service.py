"""Exportacao de PGN com validacao obrigatoria de round-trip.

Regra do projeto (SPEC 2.7): nenhum PGN e gravado sem antes ser relido pelo
`python-chess` e comparado com a AST que o gerou. Se a contagem de nos ou a
linha principal divergirem, o usuario e avisado antes de salvar.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.pgn

from ..core.move_tree import render_game_pgn
from ..core.parser import GameAST, MoveNode


@dataclass
class RoundTripReport:
    """Comparacao entre a AST e o PGN relido pelo python-chess."""

    ok: bool = True
    expected_nodes: int = 0
    actual_nodes: int = 0
    expected_mainline: list[str] = field(default_factory=list)
    actual_mainline: list[str] = field(default_factory=list)
    invalid_moves: list[str] = field(default_factory=list)
    reader_errors: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return f"Round-trip conferido: {self.actual_nodes} lances relidos sem erro."
        return "\n".join(f"- {problem}" for problem in self.problems)


def _iter_all_moves(moves: list[MoveNode]):
    for move in moves:
        yield move
        for variation in move.variations:
            yield from _iter_all_moves(variation)


def _mainline_uci(ast: GameAST, start_fen: str) -> list[str]:
    board = chess.Board(start_fen)
    ucis: list[str] = []
    for move in ast.moves:
        if not move.is_valid:
            break
        try:
            ucis.append(board.push_san(move.display_san).uci())
        except ValueError:
            break
    return ucis


def _count_game_nodes(game: chess.pgn.Game | None) -> int:
    if game is None:
        return 0
    total = 0
    stack = list(game.variations)
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.variations)
    return total


def validate_roundtrip(ast: GameAST, pgn_text: str, start_fen: str = chess.STARTING_FEN) -> RoundTripReport:
    """Rele o PGN gerado e confronta com a AST de origem."""
    report = RoundTripReport()

    all_moves = list(_iter_all_moves(ast.moves))
    report.expected_nodes = sum(1 for move in all_moves if move.is_valid)
    report.invalid_moves = [move.san for move in all_moves if not move.is_valid]
    report.expected_mainline = _mainline_uci(ast, start_fen)

    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        report.ok = False
        report.problems.append("O PGN gerado não pode ser lido de volta pelo python-chess.")
        return report

    report.reader_errors = [str(error) for error in game.errors]
    report.actual_nodes = _count_game_nodes(game)
    report.actual_mainline = [move.uci() for move in game.mainline_moves()]

    if report.reader_errors:
        report.ok = False
        report.problems.append(f"O leitor de PGN acusou {len(report.reader_errors)} erro(s): {report.reader_errors[0]}")

    if report.expected_nodes != report.actual_nodes:
        report.ok = False
        report.problems.append(
            f"Contagem de lances divergente: a análise entendeu {report.expected_nodes}, "
            f"o PGN relido tem {report.actual_nodes}."
        )

    if report.expected_mainline != report.actual_mainline:
        report.ok = False
        divergence = next(
            (
                index
                for index, (expected, actual) in enumerate(
                    zip(report.expected_mainline, report.actual_mainline, strict=False)
                )
                if expected != actual
            ),
            min(len(report.expected_mainline), len(report.actual_mainline)),
        )
        report.problems.append(f"A linha principal diverge a partir do lance {divergence + 1}.")

    if report.invalid_moves:
        report.ok = False
        preview = ", ".join(report.invalid_moves[:5])
        report.problems.append(f"{len(report.invalid_moves)} lance(s) não reconhecido(s) ficaram de fora: {preview}")

    return report


def build_pgn(ast: GameAST, start_fen: str = chess.STARTING_FEN) -> tuple[str, RoundTripReport]:
    """Gera o PGN e o laudo de round-trip correspondente."""
    pgn_text = render_game_pgn(ast)
    return pgn_text, validate_roundtrip(ast, pgn_text, start_fen)


def write_pgn(path: Path, pgn_text: str) -> None:
    if not pgn_text.endswith("\n"):
        pgn_text += "\n"
    path.write_text(pgn_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Livro inteiro (fase 4)
# ---------------------------------------------------------------------------


@dataclass
class BookExportReport:
    """Laudo da exportacao de varias partidas de uma vez."""

    games: int = 0
    nodes: int = 0
    failed_games: list[tuple[int, RoundTripReport]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed_games

    def summary(self) -> str:
        if self.ok:
            return f"{self.games} partida(s) exportada(s), {self.nodes} lances conferidos."

        detail = "\n".join(
            f"- partida {index + 1}: {report.problems[0] if report.problems else 'divergencia'}"
            for index, report in self.failed_games[:8]
        )
        return f"{len(self.failed_games)} de {self.games} partida(s) não conferem:\n{detail}"


def build_book_pgn(games: Sequence[tuple[GameAST, str]]) -> tuple[str, BookExportReport]:
    """Um unico PGN com todas as partidas, cada uma conferida por round-trip."""
    report = BookExportReport(games=len(games))
    texts: list[str] = []

    for index, (ast, start_fen) in enumerate(games):
        pgn_text, game_report = build_pgn(ast, start_fen)
        texts.append(pgn_text.rstrip("\n"))
        report.nodes += game_report.actual_nodes
        if not game_report.ok:
            report.failed_games.append((index, game_report))

    # Duas quebras entre partidas: e o separador que o padrao PGN exige.
    return ("\n\n".join(texts) + "\n" if texts else "", report)


# ---------------------------------------------------------------------------
# Outras saidas (SPEC 5.5)
# ---------------------------------------------------------------------------


def _move_to_dict(move: MoveNode) -> dict:
    payload: dict = {
        "san": move.display_san,
        "raw": move.raw_text,
        # O span e o que liga a analise de volta ao texto bruto -- e o dado que
        # uma ferramenta externa nao consegue reconstruir sozinha.
        "span": [move.san_start_index, move.san_end_index],
        "valid": move.is_valid,
        "confidence": round(move.confidence, 3),
    }
    if move.number:
        payload["number"] = move.number
    if move.corrected_san:
        payload["read_as"] = move.san
    if move.needs_review:
        payload["needs_review"] = True
    if move.nags:
        payload["nags"] = list(move.nags)
    if move.comments_before:
        payload["comments_before"] = list(move.comments_before)
    if move.comments_after:
        payload["comments_after"] = list(move.comments_after)
    if move.parent_fen:
        payload["fen_before"] = move.parent_fen
    if move.variations:
        payload["variations"] = [[_move_to_dict(child) for child in variation] for variation in move.variations]
    return payload


def build_structural_json(games: Sequence[tuple[GameAST, str]]) -> str:
    """A analise inteira, com spans -- para ferramenta externa (SPEC 5.5).

    E a unica saida que **nao** perde nada: o PGN descarta a confianca, o span
    no texto bruto e o que o programa leu antes de corrigir. Quem quiser
    conferir o trabalho com outro programa precisa disto, nao do PGN.
    """
    payload = {
        "format": "pgn-live-editor/structural",
        "version": 1,
        "games": [
            {
                "index": index,
                "headers": dict(ast.headers),
                "start_fen": start_fen,
                "result": ast.result,
                "moves": [_move_to_dict(move) for move in ast.moves],
                "issues": [
                    {
                        "severity": issue.severity,
                        "code": issue.code,
                        "message": issue.message,
                        "span": [issue.raw_start, issue.raw_end],
                        "raw": issue.raw_text,
                        "candidates": [
                            {
                                "san": candidate.san,
                                "confidence": candidate.confidence,
                                "source": candidate.source,
                            }
                            for candidate in issue.candidates
                        ],
                    }
                    for issue in ast.issues
                ],
            }
            for index, (ast, start_fen) in enumerate(games)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def build_normalized_text(games: Sequence[tuple[GameAST, str]]) -> str:
    """Texto limpo, uma partida por bloco -- para conferir ao lado do livro.

    Sem chaves, sem parenteses, sem cabecalho: so numero de lance e lance, com
    quebra a cada cinco lances brancos. E a forma de passar o olho pela partida
    comparando com a pagina impressa, que e como se acha o lance trocado.
    """
    blocks: list[str] = []

    for index, (ast, _start_fen) in enumerate(games):
        headers = dict(ast.headers)
        title = " - ".join(part for part in (headers.get("White"), headers.get("Black")) if part)
        header_line = f"# {index + 1}. {title or headers.get('Event', 'Partida')}"

        lines: list[str] = [header_line]
        current: list[str] = []
        for move in ast.moves:
            if move.number:
                if current and len(current) >= 10:
                    lines.append(" ".join(current))
                    current = []
                current.append(move.number)
            current.append(move.display_san)

        if current:
            lines.append(" ".join(current))
        if ast.result:
            lines.append(ast.result)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks) + "\n" if blocks else ""


def game_file_name(index: int, headers: dict[str, str], fallback_stem: str = "partida") -> str:
    """`03_Smyslov-Rudakovsky.pgn` -- nome legivel e ordenavel."""
    white = _file_safe(headers.get("White", ""))
    black = _file_safe(headers.get("Black", ""))

    if white or black:
        label = f"{white or 'x'}-{black or 'x'}"
    else:
        label = _file_safe(headers.get("Event", "")) or _file_safe(fallback_stem) or "partida"

    return f"{index + 1:02d}_{label[:60]}.pgn"


def _file_safe(value: str) -> str:
    cleaned = re.sub(r"[^\w\s.-]", "", (value or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._-")
    return "" if cleaned in {"", "?"} else cleaned
