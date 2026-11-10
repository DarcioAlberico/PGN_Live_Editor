"""Teste-guia da fase 16 (R-212 do docs/ROADMAP_V3.md).

A promessa central do programa, escrita como propriedade e nao como exemplo:

1. **nenhum lance some**: numa pagina de livro com prosa entre os lances, todo
   lance jogado chega a linha principal, na ordem;
2. **nenhum caractere de prosa muda**: a frase do autor chega ao comentario
   como estava, pontuacao inclusive;
3. **normalizar duas vezes = normalizar uma vez**.

As partidas sao legais e aleatorias (python-chess); a prosa sai de um
vocabulario sem nada com cara de lance. Os exemplos fixos sao os achados da
auditoria que motivaram a fase (N-02, N-04, N-09).
"""

from __future__ import annotations

import random
import re
import unittest

import _isolation  # noqa: F401
import chess
from hypothesis import given, settings
from hypothesis import strategies as st

from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer

WORDS = [
    "this", "good", "strong", "idea", "white", "black", "plan", "with", "pressure",
    "position", "better", "clearly", "ideas", "initiative", "endgame", "weakness",
]  # fmt: skip
SETTINGS = settings(max_examples=200, deadline=None, derandomize=True)


def random_game(seed: int, plies: int) -> list[str]:
    rng = random.Random(seed)
    board = chess.Board()
    sans: list[str] = []
    for _ in range(plies):
        moves = list(board.legal_moves)
        if not moves:
            break
        move = rng.choice(moves)
        sans.append(board.san(move))
        board.push(move)
    return sans


def sentence(rng: random.Random) -> str:
    words = [rng.choice(WORDS) for _ in range(rng.randint(2, 6))]
    words[0] = words[0].capitalize()
    return " ".join(words) + rng.choice([".", ".", "!", "?", "..."])


def book_page(sans: list[str], seed: int, prose_rate: float) -> tuple[str, list[str]]:
    """Movetext no estilo de livro: prosa solta (sem chaves) depois de lances pretos."""
    rng = random.Random(seed + 1)
    parts: list[str] = []
    prose: list[str] = []
    for index, san in enumerate(sans):
        number = index // 2 + 1
        if index % 2 == 0:
            parts.append(f"{number}. {san}")
        else:
            parts.append(san)
            if rng.random() < prose_rate and index + 1 < len(sans):
                text = " ".join(sentence(rng) for _ in range(rng.randint(1, 2)))
                prose.append(text)
                parts.append(text)
    return " ".join(parts), prose


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


def all_comments(ast) -> str:
    chunks = []
    for move in ast.moves:
        chunks.extend(move.comments_before)
        chunks.extend(move.comments_after)
    return " ".join(chunks)


def squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class BookPageProperties(unittest.TestCase):
    @SETTINGS
    @given(seed=st.integers(0, 10_000), plies=st.integers(2, 40), prose_rate=st.floats(0.0, 0.6))
    def test_every_played_move_reaches_the_mainline_in_order(self, seed, plies, prose_rate):
        sans = random_game(seed, plies)
        text, _prose = book_page(sans, seed, prose_rate)

        ast = parse(text)

        self.assertEqual(sans, [move.san for move in ast.moves], text)

    @SETTINGS
    @given(seed=st.integers(0, 10_000), plies=st.integers(4, 40))
    def test_the_author_prose_reaches_the_comments_unchanged(self, seed, plies):
        sans = random_game(seed, plies)
        text, prose = book_page(sans, seed, prose_rate=0.5)

        comments = squeeze(all_comments(parse(text)))

        for sentence_text in prose:
            self.assertIn(squeeze(sentence_text), comments, text)

    @SETTINGS
    @given(seed=st.integers(0, 10_000), plies=st.integers(2, 30), prose_rate=st.floats(0.0, 0.6))
    def test_normalizing_twice_is_normalizing_once(self, seed, plies, prose_rate):
        text, _prose = book_page(random_game(seed, plies), seed, prose_rate)
        normalizer = LiveTextNormalizer(locale="en")

        once = normalizer.normalize_with_mapping(text).text

        self.assertEqual(once, normalizer.normalize_with_mapping(once).text)


class AuditExamples(unittest.TestCase):
    """Os achados da auditoria que motivaram a fase, como exemplos fixos."""

    def test_n02_a_reference_to_an_earlier_move_does_not_swallow_the_mainline(self):
        ast = parse("1. e4 e5 2. Nf3 Nc6 Compare 1. d4 d5 in another game. 3. Bb5 a6 4. Ba4 Nf6 1-0")

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"], [m.san for m in ast.moves])
        self.assertIn("Compare 1. d4 d5 in another game.", squeeze(all_comments(ast)))

    def test_n04_sentence_punctuation_survives(self):
        ast = parse("1. e4 e5 This is good. Black is fine. 2. Nf3")

        self.assertIn("This is good. Black is fine.", squeeze(all_comments(ast)))

    def test_n09_a_draw_with_the_half_sign_is_a_result(self):
        self.assertEqual("1/2-1/2", parse("1. e4 e5 ½-½").result)


if __name__ == "__main__":
    unittest.main()
