"""Corpus de regressao: pagina de livro em texto bruto -> PGN esperado.

E o unico teste que roda o programa **na direcao em que ele e usado**: texto
colado do PDF entra, PGN sai. Os outros testes olham pedacos do pipeline ou
conferem um PGN pronto contra ele mesmo; este pergunta a unica coisa que
interessa ao usuario -- *o livro chegou inteiro do outro lado?*

Por isso a comparacao e por **lista de lances e contagem de nos**, nao por
texto: comentario, espacamento e ordem de cabecalho nao mudam a partida, mas um
lance a menos muda. E a metrica da SPEC 6.3: zero lances perdidos em silencio.

Como acrescentar uma pagina esta em `corpus/README.md`. Não há registro para
atualizar: o runner acha os arquivos sozinho.
"""

from __future__ import annotations

import io
import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import chess.pgn

from pgn_live_editor.core.book_import import convert_book_text
from pgn_live_editor.core.paste_cleanup import clean_pdf_text
from pgn_live_editor.services.export_service import build_book_pgn
from pgn_live_editor.services.parse_worker import (
    ParseConfig,
    ParsePipeline,
    SegmentAnalysis,
    build_games_for_export,
)

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


@dataclass
class CorpusEntry:
    """Uma pagina do corpus: o texto bruto, o PGN esperado e as opcoes."""

    name: str
    raw_text: str
    expected_pgn: str
    locale: str = "pt"
    cleanup: bool = False
    #: Pagina de livro de repertorio, com rotulos `A)`/`B1)` e colchetes:
    #: passa pelo conversor de estrutura antes de ser analisada.
    book_import: bool = False
    games: int | None = None
    allow_errors: int = 0
    prose_must_survive: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, source: Path) -> CorpusEntry:
        options = {}
        options_file = source.with_suffix(".json")
        if options_file.is_file():
            options = json.loads(options_file.read_text(encoding="utf-8"))

        return cls(
            name=source.stem,
            raw_text=source.read_text(encoding="utf-8"),
            expected_pgn=source.with_suffix(".expected.pgn").read_text(encoding="utf-8"),
            locale=options.get("locale", "pt"),
            cleanup=bool(options.get("cleanup", False)),
            book_import=bool(options.get("book_import", False)),
            games=options.get("games"),
            allow_errors=int(options.get("allow_errors", 0)),
            prose_must_survive=list(options.get("prose_must_survive", [])),
        )

    def prepared_text(self) -> str:
        """O passo 2 do fluxo do README, quando a pagina precisa dele."""
        if self.book_import:
            return convert_book_text(self.raw_text).text
        return clean_pdf_text(self.raw_text).text if self.cleanup else self.raw_text

    def analyze(self) -> list[SegmentAnalysis]:
        pipeline = ParsePipeline(ParseConfig.build(locale=self.locale))
        return pipeline.analyze_all(self.prepared_text())


def read_games(pgn_text: str) -> list[chess.pgn.Game]:
    stream = io.StringIO(pgn_text)
    games: list[chess.pgn.Game] = []
    while (game := chess.pgn.read_game(stream)) is not None:
        games.append(game)
    return games


def count_nodes(game: chess.pgn.Game) -> int:
    total = 0
    stack = list(game.variations)
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.variations)
    return total


def entries() -> list[CorpusEntry]:
    if not CORPUS_DIR.is_dir():
        return []
    sources = sorted(path for path in CORPUS_DIR.glob("*.txt") if not path.name.endswith(".expected.pgn"))
    return [CorpusEntry.load(path) for path in sources]


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.entries = entries()

    def test_the_corpus_exists(self):
        self.assertTrue(self.entries, f"Nenhuma página de corpus em {CORPUS_DIR}")

    def test_every_page_produces_the_expected_game(self):
        for entry in self.entries:
            with self.subTest(pagina=entry.name):
                analyses = entry.analyze()
                produced = read_games(build_book_pgn(build_games_for_export(analyses))[0])
                expected = read_games(entry.expected_pgn)

                self.assertEqual(
                    len(expected),
                    len(produced),
                    f"{entry.name}: número de partidas diferente do esperado",
                )
                if entry.games is not None:
                    self.assertEqual(entry.games, len(produced), f"{entry.name}: contagem de partidas")

                for index, (mine, reference) in enumerate(zip(produced, expected, strict=True)):
                    self.assertEqual(
                        [move.uci() for move in reference.mainline_moves()],
                        [move.uci() for move in mine.mainline_moves()],
                        f"{entry.name}, partida {index + 1}: a linha principal mudou",
                    )
                    self.assertEqual(
                        count_nodes(reference),
                        count_nodes(mine),
                        f"{entry.name}, partida {index + 1}: o total de lances com variantes mudou",
                    )

    def test_no_page_loses_a_move_in_silence(self):
        """A metrica da SPEC 6.3. Alerta visivel pode; sumico, nao."""
        for entry in self.entries:
            with self.subTest(pagina=entry.name):
                errors = [
                    issue for analysis in entry.analyze() for issue in analysis.ast.issues if issue.severity == "error"
                ]
                self.assertLessEqual(
                    len(errors),
                    entry.allow_errors,
                    f"{entry.name}: {[(issue.code, issue.raw_text) for issue in errors]}",
                )

    def test_every_page_survives_the_round_trip(self):
        for entry in self.entries:
            with self.subTest(pagina=entry.name):
                _pgn, report = build_book_pgn(build_games_for_export(entry.analyze()))
                self.assertEqual(
                    [],
                    report.failed_games,
                    f"{entry.name}: {[problem.summary() for _index, problem in report.failed_games]}",
                )

    def test_prose_reaches_the_pgn_untouched(self):
        """D1 e D2: nenhuma palavra de portugues pode virar notacao."""
        for entry in self.entries:
            if not entry.prose_must_survive:
                continue
            with self.subTest(pagina=entry.name):
                pgn_text, _report = build_book_pgn(build_games_for_export(entry.analyze()))
                for phrase in entry.prose_must_survive:
                    self.assertIn(phrase, pgn_text, f"{entry.name}: a prosa foi alterada")


if __name__ == "__main__":
    unittest.main()
