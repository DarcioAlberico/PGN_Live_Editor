"""Validação chess-first de um livro antes da exportação."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .parser import GameAST, MoveNode, iter_moves


@dataclass(frozen=True)
class QualityFinding:
    severity: str
    code: str
    message: str
    game: int
    start: int = -1
    end: int = -1


def _moves(ast: GameAST) -> Iterable[MoveNode]:
    return iter_moves(ast.moves)


def validate_game(ast: GameAST, game_index: int = 0) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    for issue in ast.issues:
        findings.append(
            QualityFinding(issue.severity, issue.code, issue.message, game_index, issue.raw_start, issue.raw_end)
        )
    for move in _moves(ast):
        if not move.is_valid:
            findings.append(
                QualityFinding(
                    "error",
                    "INVALID_MOVE",
                    f"Lance não reconhecido: {move.raw_text}",
                    game_index,
                    move.start_index,
                    move.end_index,
                )
            )
        elif move.needs_review:
            findings.append(
                QualityFinding(
                    "warning",
                    "PENDING_MOVE",
                    f"Lance pendente de revisão: {move.raw_text}",
                    game_index,
                    move.start_index,
                    move.end_index,
                )
            )
    return findings


def validate_book(analyses: Iterable[tuple[int, GameAST]]) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    for game_index, ast in analyses:
        findings.extend(validate_game(ast, game_index))
    return findings


def format_quality_report(findings: list[QualityFinding]) -> str:
    if not findings:
        return "Relatório de qualidade\n\nNenhum problema encontrado."
    errors = sum(item.severity == "error" for item in findings)
    warnings = sum(item.severity == "warning" for item in findings)
    lines = ["Relatório de qualidade", "", f"{errors} erro(s), {warnings} aviso(s)", ""]
    for item in findings:
        lines.append(f"[{item.severity.upper()}] Partida {item.game + 1} · {item.code}: {item.message}")
    return "\n".join(lines)
