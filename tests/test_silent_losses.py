"""O que sumia sem aviso agora fica no texto e aparece no painel (R-209).

N-12: NAG sem lance antes dele era descartado; `(` sem `)` ate o fim era
fechado em silencio. Achados no caminho, do mesmo tipo: parenteses so com
prosa ou so com comentario sumiam inteiros, e um trecho so de prosa virava `*`
sem dizer nada.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.issues import DANGLING_NAG, EMPTY_VARIATION, PROSE_WITHOUT_MOVES, UNCLOSED_VARIATION
from pgn_live_editor.core.move_tree import render_game_movetext
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


def codes(ast) -> list[str]:
    return [issue.code for issue in ast.issues]


class SilentLossTests(unittest.TestCase):
    def test_a_nag_with_no_move_before_it_is_kept_and_flagged(self):
        ast = parse("1. e4 e5 (! 1... c5) 2. Nf3")

        self.assertIn(DANGLING_NAG, codes(ast))
        self.assertIn("!", render_game_movetext(ast))

    def test_an_unclosed_variation_is_flagged_where_it_opens(self):
        text = "1. e4 e5 (1... c5 2. Nf3"

        ast = parse(text)

        (issue,) = [i for i in ast.issues if i.code == UNCLOSED_VARIATION]
        self.assertEqual("(", text[issue.raw_start : issue.raw_end])
        self.assertEqual("error", issue.severity)

    def test_parentheses_with_only_prose_become_a_comment(self):
        ast = parse("1. e4 (just a remark) e5")

        self.assertIn("just a remark", render_game_movetext(ast))
        self.assertIn(EMPTY_VARIATION, codes(ast))
        self.assertEqual(["e4", "e5"], [m.san for m in ast.moves])

    def test_parentheses_with_only_a_comment_keep_it(self):
        ast = parse("1. e4 e5 ( {a comment only} ) 2. Nf3")

        self.assertIn("a comment only", render_game_movetext(ast))

    def test_prose_without_any_move_is_reported(self):
        ast = parse("Only prose, no moves at all.")

        self.assertEqual([PROSE_WITHOUT_MOVES], codes(ast))

    def test_a_normal_game_has_none_of_these_alerts(self):
        ast = parse("1. e4 e5 (1... c5 2. Nf3) 2. Nf3 Nc6! {bom} 3. Bb5 1-0")

        self.assertEqual(
            [], [c for c in codes(ast) if c in (DANGLING_NAG, UNCLOSED_VARIATION, EMPTY_VARIATION, PROSE_WITHOUT_MOVES)]
        )


if __name__ == "__main__":
    unittest.main()
