"""Gestos de edicao sabem onde o lance termina (R-202 do docs/ROADMAP_V3.md).

"O fim do lance" era calculado de um jeito por gesto, quase sempre como o fim
da SAN -- e o NAG e o comentario do lance ficavam do lado errado:

- renumerar reescrevia do numero ate a SAN, apagando o que estava no meio (N-05);
- promover variante levava o NAG e o comentario da ancora para a linha rebaixada (N-06);
- paragrafo-para-comentario engolia o NAG; anotacoes nao achavam o comentario
  depois de um NAG e nao conseguiam limpa-lo (N-08);
- renumerar uma colagem desistia com `0-0` e com `e4!` (N-14).
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.annotations import VisualAnnotations
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser, iter_moves
from pgn_live_editor.core.text_ops import (
    paragraph_to_comment,
    promote_variation,
    renumber_document,
    renumber_movetext,
    set_move_annotations,
)
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


def apply(text: str, result) -> str:
    for edit in sorted(result.edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


def move(ast, san: str):
    return next(m for m in iter_moves(ast.moves) if m.san == san)


class N05RenumberTests(unittest.TestCase):
    def test_a_comment_between_the_number_and_the_move_survives(self):
        text = "1. e4 e5 7. {good idea} Nf3"

        fixed = apply(text, renumber_document(text, parse(text)))

        self.assertEqual("1. e4 e5 2. {good idea} Nf3", fixed)

    def test_prose_between_the_number_and_the_move_survives(self):
        text = "1. e4 e5 9. Now Nf3"

        fixed = apply(text, renumber_document(text, parse(text)))

        self.assertIn("Now Nf3", fixed)
        self.assertTrue(fixed.startswith("1. e4 e5 2."))


class N06PromoteTests(unittest.TestCase):
    def test_the_anchor_keeps_its_nag_and_comment(self):
        text = "1. e4 e5! {solid} 2. Nf3 (2. Bc4 Nf6) 2... Nc6"
        ast = parse(text)
        owner = move(ast, "e5")

        promoted = apply(text, promote_variation(text, ast, owner, 0))

        self.assertTrue(promoted.startswith("1. e4 e5! {solid}"), promoted)
        self.assertIn("2. Bc4 Nf6", promoted)
        self.assertIn("(2. Nf3", promoted)


class ReanchorTests(unittest.TestCase):
    def test_moving_a_variation_after_a_move_goes_past_its_nag_and_comment(self):
        from pgn_live_editor.core.text_ops import reanchor_variation

        text = "1. e4 e5! {solid} 2. Nf3 Nc6 (2... d6 3. d4) 3. Bb5"
        ast = parse(text)

        moved = apply(text, reanchor_variation(text, move(ast, "Nc6"), 0, move(ast, "e5"), anchor_after=True))

        self.assertIn("e5! {solid}", moved)
        self.assertNotIn("e5 (", moved)

    def test_hanging_a_variation_after_an_unrecognized_move_is_refused_not_a_crash(self):
        # N-13: a posicao depois de `Xq9` nao existe; antes levantava InvalidMoveError.
        from pgn_live_editor.core.text_ops import reanchor_variation

        text = "1. e4 e5 2. Xq9 Nc6 (2... d6) 3. Bb5"
        ast = parse(text)

        result = reanchor_variation(text, move(ast, "Nc6"), 0, move(ast, "Xq9"), anchor_after=True)

        self.assertEqual([], result.edits)
        self.assertIn("Xq9", result.message)


class N08NagTests(unittest.TestCase):
    def test_paragraph_to_comment_leaves_the_nag_on_the_move(self):
        text = "1. e4! Some prose here 1... e5"

        result = apply(text, paragraph_to_comment(text, text.index("prose"), parse(text)))

        self.assertIn("e4!", result)
        self.assertIn("{Some prose here}", result)

    def test_annotations_find_the_comment_after_a_nag_and_can_clear_it(self):
        text = "1. e4! {[%csl Ge4]} e5"
        ast = parse(text)

        cleared = apply(text, set_move_annotations(text, move(ast, "e4"), VisualAnnotations([], [])))

        self.assertNotIn("%csl", cleared)
        self.assertIn("e4!", cleared)


class N14RenumberPasteTests(unittest.TestCase):
    def board_after(self, *sans: str) -> chess.Board:
        board = chess.Board()
        for san in sans:
            board.push_san(san)
        return board

    def test_castling_written_with_zeros_is_renumbered(self):
        board = self.board_after("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5")

        self.assertEqual("4. O-O Nf6", renumber_movetext("12. 0-0 Nf6", board))

    def test_a_move_with_an_evaluation_sign_keeps_it(self):
        self.assertEqual("1. e4! e5", renumber_movetext("7. e4! e5", chess.Board()))


if __name__ == "__main__":
    unittest.main()
