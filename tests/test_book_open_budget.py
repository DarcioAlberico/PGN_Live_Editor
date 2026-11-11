"""Abrir um livro grande sem analisar tudo na thread da interface (R-401).

Abrir um projeto de 1 MB levava 10,5 s: `load_document_text` analisava as
4 400 partidas antes de deixar editar. E o cache de resumos tinha o mesmo
limite (64) do cache de arvores: num livro maior que isso, a lista de
partidas e o realce perdiam quase todas as partidas logo depois.
"""

from __future__ import annotations

import time
import unittest

import _isolation  # noqa: F401
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.services.parse_worker import AsyncParser, ParseConfig

_app = QApplication.instance() or QApplication([])

GAME = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 8. c3 O-O *"


def book(games: int) -> str:
    return "".join(f'[Event "Partida {n}"]\n\n{GAME}\n\n' for n in range(games))


def wait_until(condition, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class BookFillTests(unittest.TestCase):
    def setUp(self):
        self.parser = AsyncParser(ParseConfig.build(locale="en"))
        self.addCleanup(self.parser.shutdown)
        self.delivered = []
        self.parser.analysis_ready_signal.connect(self.delivered.append)

    def test_the_synchronous_analysis_covers_only_the_game_under_the_cursor(self):
        text = book(300)

        started = time.perf_counter()
        analysis = self.parser.parse_now(text, 0)
        elapsed = time.perf_counter() - started

        self.assertEqual(300, analysis.game_count)
        self.assertEqual([0], sorted(analysis.digests))
        self.assertLess(elapsed, 0.5, f"{elapsed:.2f} s")

    def test_the_rest_of_the_book_is_filled_in_the_background(self):
        text = book(300)
        self.parser.parse_now(text, 0)

        self.assertTrue(wait_until(lambda: any(len(a.digests) == 300 for a in self.delivered)))
        # E a analise sincrona seguinte ja sabe de todas.
        self.assertEqual(300, len(self.parser.parse_now(text, len(text) - 5).digests))

    def test_typing_interrupts_the_fill_and_it_resumes(self):
        text = book(300)
        self.parser.parse_now(text, 0)
        edited = text.replace("8. c3 O-O *", "8. c3 O-O 9. h3 *", 1)
        self.parser.schedule(edited, 10)
        self.parser._dispatch()

        self.assertTrue(wait_until(lambda: any(a.raw_text == edited and len(a.digests) == 300 for a in self.delivered)))

    def test_more_games_than_the_tree_cache_keep_their_summaries(self):
        text = book(200)
        self.parser.pipeline.analyze_all(text)

        self.assertEqual(200, len(self.parser.parse_now(text, 0).digests))


if __name__ == "__main__":
    unittest.main()
