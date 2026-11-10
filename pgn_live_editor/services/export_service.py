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
from html import escape
from pathlib import Path
from typing import Literal

import chess
import chess.pgn

from ..core.move_tree import build_move_entries, render_game_html, render_game_pgn
from ..core.parser import GameAST, MoveNode
from .durable_io import write_durable_text
from .observability import measure

ExportPolicy = Literal["block_pending", "confirm_pending", "quarantine"]


@dataclass(frozen=True)
class ExportDecision:
    allowed: bool
    policy: ExportPolicy
    pending: int
    message: str


def decide_export(policy: ExportPolicy, *, pending: int) -> ExportDecision:
    """Aplica a política de pendências sem depender de widgets Qt."""
    if policy not in {"block_pending", "confirm_pending", "quarantine"}:
        raise ValueError(f"política de exportação inválida: {policy}")
    if pending <= 0:
        return ExportDecision(True, policy, 0, "não há pendências")
    if policy == "block_pending":
        return ExportDecision(False, policy, pending, "exportação bloqueada enquanto houver pendências")
    if policy == "quarantine":
        return ExportDecision(True, policy, pending, "exportação permitida como quarentena")
    return ExportDecision(True, policy, pending, "exportação requer confirmação explícita")


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


def validate_pgn_headers(headers: dict[str, str], start_fen: str = chess.STARTING_FEN) -> list[str]:
    """Valida coerência dos headers antes da escrita do PGN."""
    problems: list[str] = []
    result = headers.get("Result", "*")
    if result not in {"1-0", "0-1", "1/2-1/2", "*"}:
        problems.append(f'Result inválido: "{result}"')
    fen = headers.get("FEN", "").strip()
    if fen:
        try:
            chess.Board(fen)
        except ValueError as exc:
            problems.append(f"FEN inválida: {exc}")
        if headers.get("SetUp") not in {None, "1"}:
            problems.append('FEN presente exige SetUp "1".')
    elif headers.get("SetUp") == "1" and start_fen == chess.STARTING_FEN:
        problems.append('SetUp "1" sem uma FEN inicial explícita.')
    return problems


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
    with measure("export_pgn", expected_nodes=sum(1 for _ in _iter_all_moves(ast.moves))) as metrics:
        with measure("render_pgn", expected_nodes=sum(1 for _ in _iter_all_moves(ast.moves))):
            pgn_text = render_game_pgn(ast)
        report = validate_roundtrip(ast, pgn_text, start_fen)
        metrics.update(actual_nodes=report.actual_nodes, ok=int(report.ok))
        return pgn_text, report


PGN_BACKUPS = 3


def write_pgn(path: Path, pgn_text: str) -> None:
    """Grava PGN preservando as versões anteriores (`.bak`, `.bak.1`, `.bak.2`)."""
    with measure("write_pgn", characters=len(pgn_text)):
        if not pgn_text.endswith("\n"):
            pgn_text += "\n"
        write_durable_text(Path(path), pgn_text, backups=PGN_BACKUPS)


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


def export_audit_text(report: RoundTripReport, *, pending_diagrams: int = 0, policy: str = "confirmado") -> str:
    """Gera um laudo humano legível da exportação realizada."""
    lines = [
        "PGN Live Editor — relatório de exportação",
        "==========================================",
        f"Resultado round-trip: {'APROVADO' if report.ok else 'COM DIVERGÊNCIAS'}",
        f"Nós esperados: {report.expected_nodes}",
        f"Nós relidos: {report.actual_nodes}",
        f"Lances inválidos: {len(report.invalid_moves)}",
        f"Pendências de diagramas: {pending_diagrams}",
        f"Política: {policy}",
    ]
    if report.problems:
        lines.extend(["", "Problemas:"])
        lines.extend(f"- {problem}" for problem in report.problems)
    return "\n".join(lines) + "\n"


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


def build_html(games: Sequence[tuple[GameAST, str]]) -> str:
    """Exporta o livro como página navegável, preservando variantes e NAGs."""
    sections: list[str] = []
    for index, (ast, _start_fen) in enumerate(games):
        rendered = render_game_html(ast, build_move_entries(ast))
        body_start = rendered.find("<body>")
        body_end = rendered.rfind("</body>")
        content = rendered[body_start + 6 : body_end] if body_start >= 0 and body_end > body_start else rendered
        headers = dict(ast.headers)
        title = " — ".join(part for part in (headers.get("White"), headers.get("Black")) if part)
        title = title or headers.get("Event", f"Partida {index + 1}")
        sections.append(f"<article><h2>{escape(title)}</h2>{content}</article>")

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PGN Live Editor — exportação</title>
<style>
body {{ margin: 0; background: #101216; color: #e9edf3; font-family: system-ui, sans-serif; }}
main {{ max-width: 1100px; margin: auto; padding: 24px; }}
article {{ background: #181b21; border: 1px solid #2b313c; border-radius: 10px; margin: 0 0 20px; overflow: hidden; }}
h2 {{ font-size: 18px; margin: 0; padding: 14px 16px; border-bottom: 1px solid #2b313c; }}
.wrap {{ padding: 14px 16px 20px; line-height: 1.65; font: 14px Consolas, monospace; }}
.move {{ color: #d6e2ff; text-decoration: none; border-radius: 6px; padding: 1px 4px; }}
.move:hover {{ background: #273244; }} .comment {{ color: #8fa2b9; font-style: italic; }}
.variation {{ color: #c7d0dc; }} .nag {{ color: #f0c05a; font-weight: 700; }}
.invalid {{ color: #ff8c8c; }} .corrected {{ color: #9ce1a5; }} .result {{ color: #8ee0ff; font-weight: 700; }}
</style></head><body><main>{"".join(sections)}</main></body></html>
"""


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
