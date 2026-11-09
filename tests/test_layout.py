"""Disposicao do movetext em arvore indentada (SPEC 3.7) e cor por nivel."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.core.layout import (  # noqa: E402
    content_of,
    flatten_variations,
    indent_variations,
)
from pgn_live_editor.core.text_ops import apply_edits  # noqa: E402
from pgn_live_editor.services.parse_worker import (  # noqa: E402
    ParseConfig,
    ParsePipeline,
    highlight_spans,
)
from pgn_live_editor.ui.editor_panel import EditorPanel  # noqa: E402
from pgn_live_editor.ui.theme import DEPTH_COLORS, color_for_depth  # noqa: E402

_app = QApplication.instance() or QApplication([])

NESTED = "1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 ( 2...e6 3.g3 ) 3.Bb5 ) 2...d6 3.d4"


def indented(text: str) -> str:
    return apply_edits(text, indent_variations(text).edits)


def flattened(text: str) -> str:
    return apply_edits(text, flatten_variations(text).edits)


class TreeLayoutTests(unittest.TestCase):
    def test_each_variation_gets_its_own_indented_line(self):
        self.assertEqual(
            [
                "1.e4 c5 2.Nf3",
                "    ( 2.Nc3 Nc6",
                "        ( 2...e6 3.g3 )",
                "    3.Bb5 )",
                "2...d6 3.d4",
            ],
            indented(NESTED).splitlines(),
        )

    def test_the_closing_paren_lines_up_with_the_one_that_opened_it(self):
        lines = indented("1.e4 ( 1.d4 ( 1...d5 ) 1...Nf6 )").splitlines()
        opener = next(line for line in lines if line.strip().startswith("( 1.d4"))
        closer = next(line for line in lines if line.strip() == "1...Nf6 )")
        self.assertEqual(len(opener) - len(opener.lstrip()), len(closer) - len(closer.lstrip()))

    def test_nothing_but_whitespace_changes(self):
        """A invariante do modulo. Vale para o texto de livro tambem, que tem
        comentario, avaliacao e cabecalho no meio."""
        source = (
            '[Event "Teste"]\n[White "A"]\n\n1.e4 {abertura de rei} c5 2.Nf3 ( 2.Nc3 {fechada} Nc6 ) 2...d6 $14 3.d4 *'
        )
        tree = indented(source)
        self.assertNotEqual(source, tree)
        self.assertEqual(content_of(source), content_of(tree))
        self.assertEqual(content_of(source), content_of(flattened(tree)))

    def test_the_header_block_is_left_alone(self):
        source = '[Event "Teste"]\n[Site "?"]\n\n1.e4 ( 1.d4 d5 ) c5'
        self.assertTrue(indented(source).startswith('[Event "Teste"]\n[Site "?"]\n\n'))

    def test_a_comment_keeps_its_spacing(self):
        source = "1.e4 {duas   palavras e uma\nquebra} c5 ( 1...e5 )"
        self.assertIn("{duas   palavras e uma\nquebra}", indented(source))

    def test_applying_it_twice_changes_nothing(self):
        tree = indented(NESTED)
        self.assertEqual([], indent_variations(tree).edits)
        self.assertEqual("O texto já esta disposto em árvore.", indent_variations(tree).message)

    def test_flattening_undoes_it(self):
        self.assertEqual(NESTED, flattened(indented(NESTED)))

    def test_an_unclosed_delimiter_stops_the_gesture_instead_of_guessing(self):
        """Juntar linhas em volta de um `{` aberto muda o que o comentario
        engole. Nao ha leitura certa: o gesto se recusa e diz onde esta."""
        result = indent_variations("1.e4 {comentario sem fim\nc5 2.Nf3")
        self.assertEqual([], result.edits)
        self.assertIn("delimitador sem fechamento", result.message)

    def test_a_blank_line_between_games_survives(self):
        source = "1.e4 c5 *\n\n1.d4 d5 *"
        self.assertIn("\n\n", indented(source))

    def test_each_game_of_the_book_is_laid_out(self):
        source = '[Event "A"]\n\n1.e4 ( 1.d4 d5 ) c5 *\n\n[Event "B"]\n\n1.c4 ( 1.Nf3 Nf6 ) e5 *'
        lines = indented(source).splitlines()
        self.assertEqual(2, sum(1 for line in lines if line.startswith("    (")))


class DepthColorTests(unittest.TestCase):
    def test_the_spans_carry_the_depth_and_not_a_yes_or_no(self):
        analysis = ParsePipeline(ParseConfig.build(locale="en")).analyze(NESTED, 0)
        depths = sorted({depth for _s, _e, _kind, depth in highlight_spans(analysis.ast)})
        self.assertEqual([0, 1, 2], depths)

    def test_every_level_of_a_book_line_gets_its_own_colour(self):
        colors = [color_for_depth("escuro", depth) for depth in range(4)]
        self.assertEqual(len(colors), len(set(colors)))

    def test_beyond_the_palette_the_colour_repeats_instead_of_failing(self):
        for theme, palette in DEPTH_COLORS.items():
            with self.subTest(tema=theme):
                self.assertEqual(palette[-1], color_for_depth(theme, 99))
                self.assertEqual(palette[0], color_for_depth(theme, -1))

    def _painted_colors(self, panel: EditorPanel) -> dict[int, str]:
        """Cor com que o realce **de fato** pintou cada profundidade."""
        formats = {
            (start, end): text_format
            for ranges in panel.highlighter._ranges_by_block.values()
            for start, end, text_format in ranges
        }
        painted: dict[int, str] = {}
        for start, end, kind, depth in panel.analysis.highlight_spans:
            if kind == "valid" and (start, end) in formats:
                painted[depth] = formats[(start, end)].foreground().color().name()
        return painted

    def _panel(self) -> EditorPanel:
        panel = EditorPanel()
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.shutdown)
        panel.configure_recognition("en", {}, [])
        panel.load_document_text(NESTED)
        return panel

    def test_the_editor_paints_each_depth_differently(self):
        painted = self._painted_colors(self._panel())

        self.assertEqual({0, 1, 2}, set(painted))
        self.assertEqual(3, len(set(painted.values())), f"cores repetidas: {painted}")
        self.assertEqual(color_for_depth("escuro", 0), painted[0])
        self.assertEqual(color_for_depth("escuro", 2), painted[2])

    def test_review_mode_drops_the_depth_colour(self):
        """Na dupla visao a pergunta e outra -- o que o programa mexeu. A cor
        de profundidade ali so competiria com a resposta."""
        panel = self._panel()
        panel.set_review_mode(True)

        painted = self._painted_colors(panel)
        self.assertEqual({0, 1, 2}, set(painted))
        self.assertEqual(1, len(set(painted.values())), "confirmado deve apagar por igual")


if __name__ == "__main__":
    unittest.main()
