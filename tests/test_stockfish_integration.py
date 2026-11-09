"""Conferencia com o Stockfish de verdade, linha principal e variantes.

Os outros testes do motor usam uma busca material de brinquedo para nao
depender de binario nenhum. Este roda o motor que estiver em `engine/` (ou no
PATH, ou em `PGN_LIVE_EDITOR_ENGINE`) e **pula** quando nao ha nenhum -- numa
maquina sem Stockfish a suite continua verde.

O texto e a pagina do Panov do corpus: uma Siciliana correta, com quatro
variantes. O motor precisa percorrer as variantes e nao achar suspeita nenhuma.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import chess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pgn_live_editor.core.normalizer import LiveTextNormalizer  # noqa: E402
from pgn_live_editor.core.parser import TolerantParser  # noqa: E402
from pgn_live_editor.core.tokenizer import PGNTokenizer  # noqa: E402
from pgn_live_editor.services.engine_service import (  # noqa: E402
    EngineSession,
    EngineSettings,
    discover_engine_path,
    scan_game,
)

PANOV = Path(__file__).resolve().parent / "corpus" / "panov_siciliana_es.txt"


@unittest.skipUnless(discover_engine_path(""), "nenhum motor UCI encontrado")
class StockfishScanTests(unittest.TestCase):
    def test_a_sound_book_page_passes_mainline_and_variations(self):
        normalized = LiveTextNormalizer(locale="es").normalize_with_mapping(PANOV.read_text(encoding="utf-8"))
        ast = TolerantParser(locale="es").parse(PGNTokenizer().tokenize(normalized))
        settings = EngineSettings(movetime_ms=50, threshold_cp=300)

        with EngineSession(settings) as session:
            report = scan_game(ast, chess.STARTING_FEN, session.evaluate, settings)

        self.assertEqual([], [issue.message for issue in report.issues])
        self.assertGreater(report.scanned_variation_moves, 30, report.summary())

    def test_a_hung_queen_in_a_variation_is_caught(self):
        text = "1. e4 e5 2. Nf3 Nc6 (2... Qh4 3. Nxh4) 3. Bb5"
        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
        ast = TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))
        settings = EngineSettings(movetime_ms=50, threshold_cp=300)

        with EngineSession(settings) as session:
            report = scan_game(ast, chess.STARTING_FEN, session.evaluate, settings)

        self.assertEqual(["Qh4"], [issue.raw_text for issue in report.issues])


if __name__ == "__main__":
    unittest.main()
