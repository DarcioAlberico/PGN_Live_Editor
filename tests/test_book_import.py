"""Conversao de texto de livro impresso, tabela de NAG e arvore de variantes.

Os casos vem quase todos de uma pagina real -- *The Najdorf Bg5 Revisited*,
Lukasz Jarmula, Thinkers Publishing 2021 -- e o gabarito de estrutura e o
PGN que o proprio livro distribui. Nao e teste sintetico: cada regra aqui
existe porque uma pagina de verdade a exigiu.
"""

from __future__ import annotations

import io
import unittest

import _isolation  # noqa: F401
import chess.pgn

from pgn_live_editor.core.book_import import (
    BookImportOptions,
    convert_book_text,
    join_wrapped_lines,
    label_lineage,
)
from pgn_live_editor.core.move_tree import render_game_movetext
from pgn_live_editor.core.nag_table import (
    NAG_BY_CODE,
    NAG_BY_GLYPH,
    NAG_TABLE,
    canonical_code,
    map_book_symbols,
)
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.outline import build_outline, flatten_outline, render_outline_text
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str, locale: str = "en"):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized))


def movetext(text: str, locale: str = "en") -> str:
    return render_game_movetext(parse(text, locale), canonical_nags=True)


class NagTableTests(unittest.TestCase):
    def test_the_table_has_no_duplicate_codes_or_glyphs(self):
        codes = [nag.code for nag in NAG_TABLE]
        glyphs = [nag.glyph for nag in NAG_TABLE]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(glyphs), len(set(glyphs)))

    def test_every_code_resolves_back_to_its_entry(self):
        for nag in NAG_TABLE:
            self.assertIs(nag, NAG_BY_CODE[nag.code])
            self.assertIs(nag, NAG_BY_GLYPH[nag.glyph])
            if nag.black_code:
                self.assertIs(nag, NAG_BY_CODE[nag.black_code])

    def test_the_symbol_font_of_the_book_maps_to_canonical_glyphs(self):
        text, count = map_book_symbols("1.e4² c5³ 2.Nf3µ d6± 3.d4+– cxd4–+ 4.Nxd4© Nf6‚ 5.Nc3ƒ a6„ 6.Bg5™")
        self.assertEqual("1.e4⩲ c5⩱ 2.Nf3∓ d6± 3.d4+- cxd4-+ 4.Nxd4© Nf6→ 5.Nc3↑ a6⇆ 6.Bg5□", text)
        # `±` e `©` ja sao os glifos canonicos: passam intactos e nao contam.
        self.assertEqual(9, count)

    def test_the_prefix_only_symbol_leaves_footnote_marks_alone(self):
        """`¹` antes do lance e "melhor e"; depois do lance e nota de rodape."""
        self.assertEqual(("⌓18.Rd1", 1), map_book_symbols("¹18.Rd1"))
        self.assertEqual(("Nf3¹", 0), map_book_symbols("Nf3¹"))

    def test_plus_minus_is_decisive_and_plus_slash_minus_is_not(self):
        """`±` e "claramente melhor"; `+-` e "ganha". Sao NAG diferentes."""
        self.assertEqual("$18", canonical_code("+-"))
        self.assertEqual("$19", canonical_code("-+"))
        self.assertEqual("$16", canonical_code("±"))
        self.assertEqual("$17", canonical_code("∓"))

    def test_side_dependent_symbols_pick_the_number_of_the_right_player(self):
        # Compensacao e de quem jogou; zugzwang e de quem esta na vez.
        self.assertIn("2. Nf3 $44 d6 $45", movetext("1.e4 c5 2.Nf3© d6© *"))
        self.assertIn("1. e4 $23 e5 $22", movetext("1.e4⨀ e5⨀ *"))


class NoveltySuffixTests(unittest.TestCase):
    """SPEC 2.2: sufixo de livro colado no lance, sem quebrar o lance."""

    def test_the_novelty_mark_becomes_a_nag(self):
        self.assertIn("2. Nf3 $146 d6", movetext("1.e4 c5 2.Nf3N d6 *"))

    def test_it_survives_a_capture_a_check_and_a_castle(self):
        for line, expected in (
            ("1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4N Nf6 *", "4. Nxd4 $146"),
            ("1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Bb5+N Nc6 *", "4. Bb5+ $146"),
            ("1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6 5.O-ON Be7 *", "5. O-O $146"),
        ):
            with self.subTest(esperado=expected):
                self.assertIn(expected, movetext(line))

    def test_a_knight_is_still_a_knight(self):
        """A espiada-atras e a regra inteira: sem ela, todo `Nc6` viraria
        novidade e a partida perderia um lance a cada dois."""
        rendered = movetext("1.e4 e5 2.Nf3 Nc6 3.Bb5 Nf6 4.O-O Nxe4 *")
        self.assertNotIn("$146", rendered)
        self.assertIn("4. O-O Nxe4", rendered)

    def test_a_standalone_n_is_not_a_novelty(self):
        """`N` solto pode ser qualquer coisa -- inclusive prosa. Sem lance
        colado nele, o programa não adivinha."""
        self.assertNotIn("$146", movetext("1.e4 c5 2.Nf3 N d6 *"))

    def test_it_combines_with_the_ordinary_annotations(self):
        self.assertIn("3. Bb5 $146 $1", movetext("1.e4 e5 2.Nf3 Nc6 3.Bb5N! a6 *"))

    def test_the_palette_offers_it_in_the_numeric_form(self):
        """Um `N` inserido solto no meio da notacao seria lido como cavalo."""
        novelty = NAG_BY_CODE["$146"]
        self.assertFalse(novelty.safe_glyph)
        self.assertEqual("$146", novelty.insert_text)


class BookSymbolsSurviveNormalizationTests(unittest.TestCase):
    def test_the_evaluation_glyphs_are_not_eaten_by_nfkc(self):
        """O NFKC transforma `²` em `2`. Se a conversao viesse depois dele,
        `Rad8³` chegaria ao tabuleiro como `Rad83` -- lance nenhum."""
        ast = parse("1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6³ *")
        self.assertEqual([], [issue for issue in ast.issues if issue.severity == "error"])
        self.assertEqual(["⩱"], ast.moves[-1].nags)

    def test_the_symbols_do_not_break_the_move_before_them(self):
        for glyph in ("²", "³", "µ", "±", "+–", "–+", "©", "‚", "ƒ", "„"):
            with self.subTest(glifo=glyph):
                ast = parse(f"1.e4 c5 2.Nf3{glyph} *")
                self.assertEqual([], [issue for issue in ast.issues if issue.severity == "error"])
                self.assertEqual(3, len(ast.moves))


class WrappedLineTests(unittest.TestCase):
    def test_a_short_line_ends_the_paragraph_and_a_full_one_does_not(self):
        wide = "x" * 96
        lines = [f"{wide} continua", "aqui termina.", "7...Be7", "7...h6"]
        self.assertEqual(
            [f"{wide} continua aqui termina.", "7...Be7", "7...h6"],
            join_wrapped_lines(lines),
        )

    def test_joining_does_not_snowball(self):
        """Duas linhas cheias juntas dao o dobro da largura; sem cuidado, a
        partir dai tudo "bate na margem" e o documento vira um paragrafo so.

        As tres primeiras sao um paragrafo (as duas cheias continuam, a curta
        fecha); `8.0-0-0` fica de fora, que e o ponto."""
        wide = "y" * 96
        joined = join_wrapped_lines([wide, wide, "fim curto.", "8.0-0-0"])
        self.assertEqual(2, len(joined))
        self.assertEqual("8.0-0-0", joined[-1])
        self.assertTrue(joined[0].endswith("fim curto."))

    def test_a_label_always_opens_a_paragraph(self):
        wide = "z" * 96
        self.assertEqual([wide, "B) 9.Be2!"], join_wrapped_lines([wide, "B) 9.Be2!"]))

    def test_an_open_bracket_holds_the_paragraph_together(self):
        """A linha cabe na margem e acaba em `=`: os dois sinais de fim de
        paragrafo. Mas o `]` esta na linha de baixo, e isso manda mais."""
        lines = ["15.Bxf6 [15.Bb7 Rb8=".ljust(70), "repeats the position.] 15...Rxc6".ljust(70)]
        joined = join_wrapped_lines(lines)
        self.assertEqual(1, len(joined))
        self.assertIn("repeats the position.] 15...Rxc6", joined[0])

    def test_hand_typed_text_is_never_joined(self):
        """Sem coluna impressa não há margem a consultar: adivinhar seria pior."""
        self.assertEqual(["1.e4 c5", "2.Nf3 d6"], join_wrapped_lines(["1.e4 c5", "2.Nf3 d6"]))


class LabelTests(unittest.TestCase):
    def test_the_lineage_of_a_label(self):
        self.assertEqual(["A"], label_lineage("A"))
        self.assertEqual(["C", "C2"], label_lineage("C2"))
        self.assertEqual(["B", "B1", "B1.2"], label_lineage("B1.2"))

    def test_the_first_sibling_continues_and_the_others_become_variations(self):
        """A regra central: em PGN a variante e alternativa ao lance anterior,
        entao as irmas entram depois do **primeiro lance** de `A)`, nao no fim."""
        page = "\n".join(
            [
                "7...h6",
                "A) 8.Be3 Ng4 9.0-0-0",
                "B) 8.Bh4 Nxe4",
                "C) 8.Bxf6 Qxf6",
            ]
        )
        converted = convert_book_text(page).text
        self.assertEqual("7...h6 8.Be3 ( 8.Bh4 Nxe4 ) ( 8.Bxf6 Qxf6 ) Ng4 9.0-0-0", converted)

    def test_a_sub_label_nests_inside_its_parent(self):
        page = "\n".join(
            [
                "9...Nc6",
                "C) 8.Bxf6 Qxf6",
                "C1) 10.Nb3 b5",
                "C2) 10.Nxc6 bxc6",
            ]
        )
        converted = convert_book_text(page).text
        self.assertEqual("9...Nc6 8.Bxf6 Qxf6 10.Nb3 ( 10.Nxc6 bxc6 ) b5", converted)


class BracketTests(unittest.TestCase):
    def test_a_bracket_becomes_a_variation(self):
        converted = convert_book_text("11.Nce2 Qb6 [11...Nxe4? 12.Qxb4!] 12.e5").text
        self.assertEqual("11.Nce2 Qb6 ( 11...Nxe4? 12.Qxb4! ) 12.e5", converted)

    def test_a_semicolon_inside_a_bracket_separates_sisters(self):
        converted = convert_book_text("15.Bh6 g6 [15...Nh5? 16.exd5; 15...Ne8? 16.Rxe8] 16.Bxf8").text
        self.assertEqual("15.Bh6 g6 ( 15...Nh5? 16.exd5 ) ( 15...Ne8? 16.Rxe8 ) 16.Bxf8", converted)

    def test_pgn_headers_and_drawing_commands_are_not_brackets(self):
        source = '[Event "Teste"]\n1.e4 {[%cal Ge2e4]} c5'
        self.assertIn('[Event "Teste"]', convert_book_text(source).text)
        self.assertIn("[%cal Ge2e4]", convert_book_text(source).text)


class ProseTests(unittest.TestCase):
    def test_prose_becomes_a_comment_instead_of_disappearing(self):
        converted = convert_book_text("8.Bh4? falls into an elementary trap: 8...Nxe4").text
        self.assertEqual("8.Bh4? {falls into an elementary trap:} 8...Nxe4", converted)

    def test_a_square_named_in_a_sentence_stays_prose(self):
        converted = convert_book_text("16.Nbd4 Admitting that the knight is badly placed on b3. 16...Qb6").text
        self.assertEqual("16.Nbd4 {Admitting that the knight is badly placed on b3.} 16...Qb6", converted)

    def test_a_line_cited_inside_a_sentence_stays_prose(self):
        """ "transposes to the 6.Be3 e6 variation" nao e uma linha a jogar."""
        converted = convert_book_text("9.f3 transposes to the 6.Be3 e6 variation, which suits Black.").text
        self.assertEqual("9.f3 {transposes to the 6.Be3 e6 variation, which suits Black.}", converted)

    def test_a_connector_before_a_real_line_does_not_demote_it(self):
        """`[After 16.Bg2?! e5!]` tem prosa dos dois lados e mesmo assim e linha."""
        converted = convert_book_text("16.Nbd4 [After 16.Bg2?! e5! White is worse.] 16...Qb6").text
        self.assertEqual("16.Nbd4 ( {After} 16.Bg2?! e5! {White is worse.} ) 16...Qb6", converted)

    def test_a_line_that_ends_in_moves_is_not_a_citation(self):
        converted = convert_book_text("8.Bh4 is met by the simple 8...Nxe4").text
        self.assertEqual("8.Bh4 {is met by the simple} 8...Nxe4", converted)


class FurnitureTests(unittest.TestCase):
    def test_page_furniture_is_dropped(self):
        page = "\n".join(["Position after: 7.Qd2", "7...Be7", "Chapter 1", "10", "Show/Hide Solution", "8.0-0-0"])
        report = convert_book_text(page)
        self.assertNotIn("Position after", report.text)
        self.assertNotIn("Chapter 1", report.text)
        self.assertNotIn("Show/Hide", report.text)

    def test_the_diagram_caption_can_become_a_marker_instead(self):
        page = "7...Be7\nPosition after: 7...Be7\n8.0-0-0"
        report = convert_book_text(page, BookImportOptions(diagram_marks="comment"))
        self.assertIn("{[#]}", report.text)

    def test_each_exercise_becomes_its_own_game(self):
        page = "\n".join(["3", "Chapter 1", "571", "□ 1.?", "Show/Hide Solution", "1.Bh6!‚", "A cold shower."])
        report = convert_book_text(page)
        self.assertIn("=== Exercício 3 — Chapter 1 ===", report.text)
        self.assertIn("brancas jogam", report.text)
        self.assertEqual(1, report.stats["exercises"])


class MoveNumberRegressionTests(unittest.TestCase):
    def test_a_repeated_move_number_opens_a_variation(self):
        """`8.0-0-0` e depois `8.f4!?` nao e o nono lance -- e outro oitavo."""
        report = convert_book_text("8.0-0-0\n8.f4!? b5 9.e5 dxe5")
        self.assertEqual("8.0-0-0 ( 8.f4!? b5 9.e5 dxe5 )", report.text)
        self.assertEqual(1, report.stats["regressions"])
        self.assertTrue(report.notes)

    def test_black_continues_after_white_at_the_same_number(self):
        report = convert_book_text("8.0-0-0\n8...b5 9.Bd3")
        self.assertEqual("8.0-0-0 8...b5 9.Bd3", report.text)
        self.assertEqual(0, report.stats["regressions"])

    def test_a_move_number_inside_a_comment_does_not_count(self):
        """Se contasse, "Placing the queen on d2 in the 6.Bg5 Najdorf" abriria
        uma variante no lance 6 -- e o paragrafo e prosa da primeira palavra."""
        report = convert_book_text("7.Qd2\nPlacing the queen on d2 in the 6.Bg5 Najdorf is less good here.")
        self.assertEqual(0, report.stats["regressions"])
        self.assertNotIn("(", report.text)


class OutlineTests(unittest.TestCase):
    def setUp(self):
        self.ast = parse(
            "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.Qd2 ( 7.Be2 Be7 8.0-0 ) "
            "7...Be7 ( 7...h6 8.Be3 ( 8.Bh4 Nxe4 ) ( 8.Bxf6 Qxf6 9.0-0-0 Nc6 10.Nb3 ( 10.Nxc6 bxc6 ) 10...b5 ) "
            "8...Ng4 ) 8.0-0-0"
        )
        self.lines = flatten_outline(build_outline(self.ast))

    def test_the_labels_follow_the_convention_of_the_book(self):
        """O livro chama de `A)` a linha que continua, entao a primeira
        variante e `B)` -- deslocada de um, para casar com a pagina impressa."""
        self.assertEqual(
            ["", "B", "C", "C2", "C3", "C3.2"],
            [line.label for line in self.lines],
        )

    def test_two_branch_points_on_one_line_never_share_a_label(self):
        labels = [line.label for line in self.lines]
        self.assertEqual(len(labels), len(set(labels)))

    def test_the_depth_is_the_nesting_depth(self):
        self.assertEqual([0, 1, 1, 2, 2, 3], [line.depth for line in self.lines])

    def test_each_line_knows_where_it_branches_from(self):
        by_label = {line.label: line for line in self.lines}
        self.assertEqual("7. Qd2", by_label["C"].anchor)
        self.assertEqual("7... h6", by_label["C2"].anchor)

    def test_the_span_points_at_the_first_move_of_the_line(self):
        raw = (
            "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.Qd2 ( 7.Be2 Be7 8.0-0 ) "
            "7...Be7 ( 7...h6 8.Be3 ( 8.Bh4 Nxe4 ) ( 8.Bxf6 Qxf6 9.0-0-0 Nc6 10.Nb3 ( 10.Nxc6 bxc6 ) 10...b5 ) "
            "8...Ng4 ) 8.0-0-0"
        )
        line = next(item for item in self.lines if item.label == "B")
        self.assertTrue(raw[line.start_index :].startswith("7.Be2"))

    def test_the_text_rendering_is_indented_by_depth(self):
        rendered = render_outline_text(build_outline(self.ast)).splitlines()
        self.assertTrue(rendered[0].startswith("Linha principal"))
        self.assertTrue(rendered[1].startswith("    B)"))
        self.assertTrue(rendered[-1].startswith("            C3.2)"))


class RealBookPageTests(unittest.TestCase):
    """A pagina inteira, conferida contra o PGN que o livro distribui."""

    PAGE = "\n".join(
        [
            "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6",
            "Position after: 6...e6",
            "7.Qd2",
            "7...Be7",
            "7...h6",
            "A) 8.Be3 Ng4 [8...b5 9.f3 transposes to the 6.Be3 e6 variation.] 9.0-0-0 Nxe3 10.Qxe3 Nc6",
            "B) 8.Bh4? falls into an elementary trap: 8...Nxe4!µ",
            "C) 8.Bxf6 Qxf6 9.0-0-0 Nc6",
            "9",
            "C1) 10.Nb3?! This has been the most popular move. 10...b5 11.f4 Qd8",
            "C2) 10.Nxc6 bxc6 11.f4 Qd8 12.Bc4 Be7=",
        ]
    )

    def test_the_page_produces_the_structure_the_book_publishes(self):
        converted = convert_book_text(self.PAGE).text
        produced = chess.pgn.read_game(io.StringIO(movetext(converted)))
        official = chess.pgn.read_game(
            io.StringIO(
                "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.Qd2 7...Be7 "
                "( 7...h6 8.Be3 ( 8.Bh4 $2 8...Nxe4 $1 $17 ) "
                "( 8.Bxf6 Qxf6 9.0-0-0 Nc6 10.Nb3 $6 ( 10.Nxc6 bxc6 11.f4 Qd8 12.Bc4 Be7 $10 ) 10...b5 11.f4 Qd8 ) "
                "8...Ng4 ( 8...b5 9.f3 ) 9.0-0-0 Nxe3 10.Qxe3 Nc6 ) *"
            )
        )

        self.assertEqual(
            [move.uci() for move in official.mainline_moves()],
            [move.uci() for move in produced.mainline_moves()],
        )
        self.assertEqual(self._nodes(official), self._nodes(produced))
        self.assertEqual(self._walk(official), self._walk(produced))

    def test_the_commentary_of_the_page_reaches_the_pgn(self):
        rendered = movetext(convert_book_text(self.PAGE).text)
        self.assertIn("{falls into an elementary trap:}", rendered)
        self.assertIn("{This has been the most popular move.}", rendered)

    @staticmethod
    def _nodes(game) -> int:
        total, stack = 0, list(game.variations)
        while stack:
            node = stack.pop()
            total += 1
            stack.extend(node.variations)
        return total

    @staticmethod
    def _walk(game) -> list[tuple[str, tuple[int, ...]]]:
        found: list[tuple[str, tuple[int, ...]]] = []

        def visit(node):
            for child in node.variations:
                found.append((child.move.uci(), tuple(sorted(child.nags))))
                visit(child)

        visit(game)
        return sorted(found)


if __name__ == "__main__":
    unittest.main()
