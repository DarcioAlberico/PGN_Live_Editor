"""Correcoes de uma linha da auditoria de 2026-11 (R-006 do docs/ROADMAP_V3.md).

Cada teste reproduz um achado confirmado da docs/AUDITORIA_2026-11.md; o id
esta no nome da classe.
"""

from __future__ import annotations

import time
import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.layout import flatten_variations
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.outline import _letter
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.text_insertion import build_board_move_insertion
from pgn_live_editor.core.tokenizer import PGNTokenizer
from pgn_live_editor.services import observability


def parse(text: str, locale: str = "en"):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized))


class N01DescriptiveBookScaleTests(unittest.TestCase):
    """N-01: numero sem ponto custava uma busca sobre todo o texto anterior."""

    def test_forty_kilobytes_of_descriptive_notation_normalize_quickly(self):
        page = "1 P-K4 P-K4 2 N-KB3 N-QB3 3 B-N5 P-QR3 4 B-R4 N-B3 5 O-O B-K2 "
        text = page * 640  # ~40 KB; levava 12 s antes da correcao

        started = time.perf_counter()
        LiveTextNormalizer(locale="en").normalize_with_mapping(text)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.5, f"{elapsed:.1f} s para 40 KB")

    def test_a_diagram_number_far_into_the_text_is_still_not_a_move(self):
        text = "1 P-K4 P-K4 " * 200 + "see diagram 12 for the position"

        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text).text

        self.assertIn("diagram 12 for", normalized)


class N09HalfPointResultTests(unittest.TestCase):
    """N-09: o NFKC transformava `½` em `1⁄2` e o empate virava comentario."""

    def test_a_draw_written_with_the_half_sign_is_a_result(self):
        ast = parse("1. e4 e5 2. Nf3 Nc6 ½-½")

        self.assertEqual("1/2-1/2", ast.result)
        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.san for move in ast.moves])


class L17WindowsLineEndingTests(unittest.TestCase):
    """L-17: com CRLF a linha em branco nao era vista e paragrafos se fundiam."""

    def test_flattening_keeps_paragraphs_with_crlf(self):
        text = "1.e4 c5 2.Nf3 d6\r\n\r\n3.d4 cxd4"

        flattened = text
        for edit in sorted(flatten_variations(text).edits, key=lambda e: e.start, reverse=True):
            flattened = flattened[: edit.start] + edit.replacement + flattened[edit.end :]

        self.assertRegex(flattened, r"d6\r?\n[ \t\r]*\n3\.d4")


class N15InsertionContextTests(unittest.TestCase):
    """N-15: `endswith("2.")` casava com `Game 12.`."""

    def test_a_number_that_only_ends_like_the_move_number_does_not_count(self):
        text = "Game 12."

        inserted = build_board_move_insertion(text, len(text), len(text), "e4", chess.WHITE, 2)

        self.assertEqual(" 2. e4", inserted)

    def test_the_real_move_number_is_not_repeated(self):
        text = "1. e4 e5 2."

        inserted = build_board_move_insertion(text, len(text), len(text), "Nf3", chess.WHITE, 2)

        self.assertEqual(" Nf3", inserted)


class L09OutlineLetterTests(unittest.TestCase):
    """L-09: `_letter(702)` levantava IndexError."""

    def test_letters_follow_spreadsheet_columns(self):
        self.assertEqual(["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"], [_letter(i) for i in (0, 25, 26, 51, 52, 701, 702)])


class S04MetricsTests(unittest.TestCase):
    """S-04: cada medida relia e reescrevia o metrics.jsonl inteiro."""

    def setUp(self):
        self.path = observability.metrics_path()
        self.path.unlink(missing_ok=True)
        for rotated in self.path.parent.glob(self.path.name + ".*"):
            rotated.unlink()

    def test_recording_a_metric_does_not_read_the_file_back(self):
        self.path.write_text(
            '{"operation": "x", "duration_ms": 1}\n' * observability.MAX_METRIC_LINES, encoding="utf-8"
        )

        started = time.perf_counter()
        for _ in range(200):
            observability.record_metric("parse", 1.0)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5, f"{elapsed * 5:.1f} ms por metrica")

    def test_a_corrupt_metrics_file_never_breaks_the_measured_operation(self):
        self.path.write_bytes(b"\xff\xfe nao e utf-8\n" * 10)

        with observability.measure("parse") as counters:
            counters["characters"] = 3

        self.assertIsInstance(observability.read_metrics(), list)

    def test_the_file_stays_bounded(self):
        for _ in range(observability.MAX_METRIC_LINES * 3):
            observability.record_metric("parse", 1.0)

        total = sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in [self.path, *self.path.parent.glob(self.path.name + ".*")]
            if path.exists()
        )
        self.assertLessEqual(total, 2 * observability.MAX_METRIC_LINES + 1)
        self.assertTrue(observability.read_metrics())


if __name__ == "__main__":
    unittest.main()
