"""Corpus de regressao: trabalhos reais devem sobreviver a um ciclo completo.

Cada arquivo de `Trabalhos Salvos/` e reaberto pelo pipeline do editor, exportado
de novo e conferido contra o original. Este teste trava a fidelidade ja
alcancada (Partida_13: 82 nos, Partida_14: 97 nos) para que nenhuma mudanca
futura no normalizador, no tokenizador ou no parser a quebre em silencio.
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path

import chess
import chess.pgn

from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.pgn_headers import extract_pgn_headers
from pgn_live_editor.core.tokenizer import PGNTokenizer
from pgn_live_editor.services.export_service import build_pgn, validate_roundtrip

CORPUS_DIR = Path(__file__).resolve().parent.parent / "Trabalhos Salvos"


def parse_document(raw_text: str, locale: str = "pt"):
    headers, body, offset = extract_pgn_headers(raw_text)
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(body, base_offset=offset)
    tokens = PGNTokenizer().tokenize(normalized)
    start_fen = headers.get("FEN", chess.STARTING_FEN)
    ast = TolerantParser().parse(tokens, start_fen=start_fen)
    ast.headers = list(headers.items())
    return ast, start_fen


def count_nodes(game: chess.pgn.Game | None) -> int:
    if game is None:
        return 0
    total = 0
    stack = list(game.variations)
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.variations)
    return total


class CorpusRoundTripTests(unittest.TestCase):
    """Reabrir + reexportar nao pode perder nem um lance."""

    def corpus_files(self) -> list[Path]:
        if not CORPUS_DIR.is_dir():
            return []
        return sorted(CORPUS_DIR.glob("*.pgn"))

    def test_corpus_is_available(self):
        self.assertTrue(self.corpus_files(), f"Nenhum PGN de referencia em {CORPUS_DIR}")

    def test_saved_work_survives_reopen_and_reexport(self):
        for path in self.corpus_files():
            with self.subTest(arquivo=path.name):
                source = path.read_text(encoding="utf-8")

                original = chess.pgn.read_game(io.StringIO(source))
                self.assertIsNotNone(original, "arquivo de referencia ilegivel")
                original_nodes = count_nodes(original)
                original_mainline = [move.uci() for move in original.mainline_moves()]

                ast, start_fen = parse_document(source)
                pgn_text, report = build_pgn(ast, start_fen)

                reexported = chess.pgn.read_game(io.StringIO(pgn_text))
                self.assertIsNotNone(reexported)

                self.assertEqual([], list(reexported.errors), "python-chess acusou erro no PGN reexportado")
                self.assertEqual(
                    original_mainline,
                    [move.uci() for move in reexported.mainline_moves()],
                    "a linha principal mudou depois de reabrir e reexportar",
                )
                self.assertEqual(
                    original_nodes,
                    count_nodes(reexported),
                    "o total de lances (com variantes) mudou depois de reabrir e reexportar",
                )
                self.assertTrue(report.ok, f"round-trip reprovado: {report.summary()}")

    def test_corpus_parses_without_unrecognized_moves(self):
        for path in self.corpus_files():
            with self.subTest(arquivo=path.name):
                ast, _start_fen = parse_document(path.read_text(encoding="utf-8"))
                errors = [issue for issue in ast.issues if issue[-1] == "error"]
                self.assertEqual([], errors, f"{path.name} passou a gerar erros de analise")


class RoundTripValidatorTests(unittest.TestCase):
    def test_validator_flags_a_truncated_pgn(self):
        ast, start_fen = parse_document("1.e4 e5 2.Nf3 Nc6 3.Bb5 a6")
        truncated = '[Event "x"]\n[Site "?"]\n[Date "????.??.??"]\n[Round "?"]\n[White "?"]\n[Black "?"]\n[Result "*"]\n\n1. e4 e5 *'

        report = validate_roundtrip(ast, truncated, start_fen)

        self.assertFalse(report.ok)
        self.assertEqual(6, report.expected_nodes)
        self.assertEqual(2, report.actual_nodes)
        self.assertTrue(any("divergente" in problem for problem in report.problems))

    def test_validator_approves_a_faithful_pgn(self):
        ast, start_fen = parse_document("1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 (3... Nf6 4.O-O) 4.Ba4 1-0")
        pgn_text, report = build_pgn(ast, start_fen)

        self.assertTrue(report.ok, report.summary())
        self.assertIn("1-0", pgn_text)


if __name__ == "__main__":
    unittest.main()
