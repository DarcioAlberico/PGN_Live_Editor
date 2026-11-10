"""Citacao de lances de outra linha: vira comentario, e so ela (N-02 / R-204).

Um numero de lance menor que o atual ("Compare 1. d4 d5 ...") abre uma citacao.
Antes ela ia ate o proximo `)` ou o resultado -- e engolia, em silencio, os
lances de verdade que viessem depois.
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.issues import CITATION_COLLECTED
from pgn_live_editor.core.move_tree import render_game_movetext
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


class CitationTests(unittest.TestCase):
    def test_the_citation_ends_where_the_game_resumes(self):
        ast = parse("1. e4 e5 2. Nf3 Nc6 Compare 1. d4 d5 in another game. 3. Bb5 a6 4. Ba4 Nf6 1-0")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"], [m.san for m in ast.moves])
        self.assertEqual(["Compare 1. d4 d5 in another game."], ast.moves[3].comments_after)

    def test_the_collected_citation_is_shown_for_review(self):
        text = "1. e4 e5 2. Nf3 Nc6 Compare 1. d4 d5 in another game. 3. Bb5"

        ast = parse(text)

        (issue,) = [i for i in ast.issues if i.code == CITATION_COLLECTED]
        self.assertEqual("info", issue.severity)
        self.assertEqual("1. d4 d5 in another game.", text[issue.raw_start : issue.raw_end])

    def test_a_number_of_the_cited_line_that_is_not_legal_here_stays_in_the_citation(self):
        # `3. Nf6` e da linha citada (pretas jogando com numero de brancas):
        # ilegal na partida, entao nao e a partida voltando.
        ast = parse("1. e4 e5 2. Nf3 Nc6 as in 1. d4 Nf6 2. c4 e6 3. Nf6 later 3. Bb5 a6")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"], [m.san for m in ast.moves])

    def test_the_citation_still_stops_at_a_closing_parenthesis(self):
        ast = parse("1. e4 e5 2. Nf3 (2. Bc4 Bc5 cf. 1. d4 d5) Nc6 3. Bb5")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"], [m.san for m in ast.moves])

    def test_braces_inside_the_citation_do_not_break_the_comment(self):
        ast = parse("1. e4 e5 2. Nf3 Nc6 cf 1. d4 x}y 3. Bb5")

        movetext = render_game_movetext(ast)

        self.assertEqual(movetext.count("{"), movetext.count("}"), movetext)
        self.assertIn("Bb5", movetext)


if __name__ == "__main__":
    unittest.main()
