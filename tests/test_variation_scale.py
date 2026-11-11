"""Ancorar variante sem refazer a partida a cada variante (R-403).

N-16 (resto): cada variante recalculava a posicao depois de **todos** os
lances da linha (tabuleiro novo + `push_san` + `fen()`) e pontuava todos eles
-- ~7 ms por variante, quadratico num jogo muito anotado.
"""

from __future__ import annotations

import random
import time
import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def annotated_game(plies: int = 80, variations: int = 500, seed: int = 7) -> tuple[str, dict[int, int]]:
    """Um jogo de `plies` meios-lances com `variations` variantes legais; e quantas por meio-lance."""
    rng = random.Random(seed)
    board = chess.Board()
    mainline: list[chess.Move] = []
    while len(mainline) < plies:
        legal = list(board.legal_moves)
        if len(legal) < 2:
            board = chess.Board()
            mainline = []
            continue
        move = rng.choice(legal)
        mainline.append(move)
        board.push(move)

    per_ply: dict[int, int] = {}
    for _ in range(variations):
        ply = rng.randrange(plies)
        per_ply[ply] = per_ply.get(ply, 0) + 1

    board = chess.Board()
    parts: list[str] = []
    for ply, move in enumerate(mainline):
        number = board.fullmove_number
        prefix = f"{number}." if board.turn == chess.WHITE else f"{number}..."
        parts.append(f"{prefix} {board.san(move)}")
        for _ in range(per_ply.get(ply, 0)):
            alternatives = [m for m in board.legal_moves if m != move]
            alternative = rng.choice(alternatives)
            line = board.copy(stack=False)
            text = [f"{prefix} {line.san(alternative)}"]
            line.push(alternative)
            reply = next(iter(line.legal_moves), None)
            if reply is not None:
                text.append(line.san(reply))
            parts.append("(" + " ".join(text) + ")")
        board.push(move)
    return " ".join(parts), per_ply


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


class VariationScaleTests(unittest.TestCase):
    def test_five_hundred_variations_in_a_forty_move_game(self):
        text, per_ply = annotated_game()

        started = time.perf_counter()
        ast = parse(text)
        elapsed = time.perf_counter() - started

        self.assertEqual(80, len(ast.moves))
        # Convencao do parser: a alternativa ao lance N fica no lance N-1 (a
        # posicao de onde ela sai); a do primeiro lance fica nele mesmo.
        expected = [per_ply.get(ply + 1, 0) for ply in range(80)]
        expected[0] += per_ply.get(0, 0)
        self.assertEqual(
            expected,
            [len(move.variations) for move in ast.moves],
            "cada variante tem de continuar ancorada no lance certo",
        )
        self.assertLess(elapsed, 1.0, f"{elapsed:.2f} s")


if __name__ == "__main__":
    unittest.main()
