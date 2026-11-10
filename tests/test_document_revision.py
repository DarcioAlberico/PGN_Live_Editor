"""Resultado assincrono so vale para a revisao do documento que o pediu (R-301).

S-12/U-06: o resultado carregava so um `request_id`, e `parse_now`/`configure`
nao o mudavam; uma analise em voo, feita com a configuracao antiga, chegava
depois -- e o alerta que o usuario tinha acabado de ignorar voltava.

S-03 (resto): uma excecao no trabalho em segundo plano deixava `_in_flight`
preso, e a analise ao vivo parava para sempre.

U-09: com os offsets de partida velhos (o texto mudou, a analise ainda nao
chegou), mover o cursor disparava uma analise sincrona completa: 9 a cada 18
teclas no fim de uma partida.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

import _isolation  # noqa: F401
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.services.parse_worker import AsyncParser, ParseConfig, ParsePipeline

TEXT = "1. e4 e5 2. Xq9 Nc6"


def wait_until(app: QApplication, condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class AsyncParserRevisionTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.parser = AsyncParser(ParseConfig.build(locale="en"))
        self.addCleanup(self.parser.shutdown)
        self.delivered = []
        self.parser.analysis_ready_signal.connect(self.delivered.append)

    def test_a_late_result_after_a_synchronous_parse_is_dropped(self):
        slow = ParsePipeline.analyze

        def slowly(pipeline, raw_text, cursor_position=0):
            time.sleep(0.3)
            return slow(pipeline, raw_text, cursor_position)

        with mock.patch.object(ParsePipeline, "analyze", slowly):
            self.parser.schedule(TEXT)
            self.parser._dispatch()
            # O usuario ignora o alerta de `Xq9`: nova configuracao e analise na hora.
            self.parser.configure(ParseConfig.build(locale="en", ignored_tokens=["Xq9"]))
            fresh = self.parser.parse_now(TEXT)
            wait_until(self.app, lambda: not self.parser._in_flight)

        self.assertNotIn("Xq9", [issue.raw_text for issue in fresh.issues])
        self.assertEqual([], self.delivered, "a analise velha nao pode ser entregue")

    def test_a_current_result_is_delivered_with_its_generation(self):
        self.parser.schedule(TEXT)
        self.parser._dispatch()

        self.assertTrue(wait_until(self.app, lambda: bool(self.delivered)))
        self.assertEqual(self.parser.generation, self.delivered[0].generation)

    def test_an_exception_in_the_worker_does_not_stop_live_analysis(self):
        real = ParsePipeline.analyze
        calls = {"n": 0}

        def flaky(pipeline, raw_text, cursor_position=0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("defeito de proposito")
            return real(pipeline, raw_text, cursor_position)

        with mock.patch.object(ParsePipeline, "analyze", flaky):
            self.parser.schedule(TEXT)
            self.parser._dispatch()
            self.assertTrue(wait_until(self.app, lambda: not self.parser._in_flight), "ficou preso")
            self.parser.schedule(TEXT + " 3. Bb5")
            self.parser._dispatch()
            self.assertTrue(wait_until(self.app, lambda: bool(self.delivered)))


class EditorCursorTests(unittest.TestCase):
    def test_moving_the_cursor_over_stale_offsets_does_not_reparse_synchronously(self):
        from pgn_live_editor.ui.editor_panel import EditorPanel

        self.app = QApplication.instance() or QApplication([])
        panel = EditorPanel()
        self.addCleanup(panel.deleteLater)
        panel.load_document_text('[Event "A"]\n\n1. e4 e5 2. Nf3\n\n[Event "B"]\n\n1. d4 d5 *\n')
        cursor = panel.text_edit.textCursor()
        cursor.setPosition(panel.document_text().index("2. Nf3") + len("2. Nf3"))
        panel.text_edit.setTextCursor(cursor)

        with mock.patch.object(panel.async_parser, "parse_now", wraps=panel.async_parser.parse_now) as parse_now:
            for char in " Nc6 3. Bb5 a6 4. Ba4":
                panel.text_edit.textCursor().insertText(char)
                panel.text_edit.moveCursor(QTextCursor.MoveOperation.Right)

        self.assertLessEqual(parse_now.call_count, 1)


if __name__ == "__main__":
    unittest.main()
