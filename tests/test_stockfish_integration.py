"""Conferencia com o Stockfish de verdade, linha principal e variantes.

Os outros testes do motor usam uma busca material de brinquedo para nao
depender de binario nenhum. Este roda o motor que estiver em `engine/` (ou no
PATH, ou em `PGN_LIVE_EDITOR_ENGINE`) e **pula** quando nao ha nenhum -- numa
maquina sem Stockfish a suite continua verde.

O texto e a pagina do Panov do corpus: uma Siciliana correta, com quatro
variantes. O motor precisa percorrer as variantes e nao achar suspeita nenhuma.

**Profundidade, nao tempo.** Com `movetime` o resultado dependia da CPU: com a
maquina ocupada a busca nao passava da profundidade 8-10, e nela o motor ainda
nao enxerga a refutacao da "celada" do Panov (`8.Nxd5`) e acusava suspeita; com
a maquina livre passava. Profundidade fixa num processo de uma thread e
deterministica; 12 e a menor em que a celada fica limpa (medido em 2026-11-09,
Q-03 da docs/AUDITORIA_2026-11.md). Uma sessao so para a classe: abrir o motor
a cada teste era o que mais estourava o timeout de inicializacao.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer
from pgn_live_editor.services.engine_service import (
    EngineSession,
    EngineSettings,
    discover_engine_path,
    scan_game,
)

PANOV = Path(__file__).resolve().parent / "corpus" / "panov_siciliana_es.txt"
SETTINGS = EngineSettings(depth=12, threshold_cp=300)


def parse(text: str, locale: str):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized))


@unittest.skipUnless(discover_engine_path(""), "nenhum motor UCI encontrado")
class StockfishScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = EngineSession(SETTINGS).open()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def scan(self, text: str, locale: str):
        return scan_game(parse(text, locale), chess.STARTING_FEN, self.session.evaluate, SETTINGS)

    def test_a_sound_book_page_passes_mainline_and_variations(self):
        report = self.scan(PANOV.read_text(encoding="utf-8"), "es")

        self.assertEqual([], [issue.message for issue in report.issues])
        self.assertGreater(report.scanned_variation_moves, 30, report.summary())

    def test_a_hung_queen_in_a_variation_is_caught(self):
        report = self.scan("1. e4 e5 2. Nf3 Nc6 (2... Qh4 3. Nxh4) 3. Bb5", "en")

        self.assertEqual(["Qh4"], [issue.raw_text for issue in report.issues])


if __name__ == "__main__":
    unittest.main()
