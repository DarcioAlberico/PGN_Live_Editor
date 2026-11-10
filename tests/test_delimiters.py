"""Um so lugar decide o que esta dentro de um comentario ou de uma variante (R-201).

Antes havia quatro regras, nenhuma igual a `COMMENT_RE` do normalizador -- a que
o parser usa (N-07): um comentario valido de duas linhas era acusado de aberto;
um `)` dentro de comentario fechava a variante; um `{` aberto atravessava linhas
em branco e engolia a `}` de outro comentario; `[D]` dentro de um comentario era
reescrito como `{[#]}` e quebrava as chaves. E a conversao de colchetes do livro
fechava a variante no `]` de um `[%cal]` (L-16).
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.book_import import convert_book_text
from pgn_live_editor.core.delimiters import comment_spans
from pgn_live_editor.core.fen_tools import normalize_diagram_marks
from pgn_live_editor.core.text_ops import check_balance, find_enclosing_delimiter, scope_end, unwrap_at


def apply(text: str, result) -> str:
    for edit in sorted(result.edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


class CommentSpanTests(unittest.TestCase):
    def test_it_agrees_with_the_parser_rule(self):
        # Sem a linha em branco, a regra do parser leria `{aberto...` ate a `}`
        # de `{fechado}` (comentario de duas linhas) -- e e com ela que concordamos.
        text = "1. e4 {duas\nlinhas} e5 {aberto ate o fim da linha\n\n2. Nf3 {fechado}"

        spans = comment_spans(text)

        self.assertEqual(
            ["{duas\nlinhas}", "{aberto ate o fim da linha", "{fechado}"], [text[s:e] for s, e, _ in spans]
        )
        self.assertEqual([True, False, True], [closed for *_, closed in spans])

    def test_a_comment_never_crosses_a_blank_line(self):
        text = "{abre\n\nfecha}"

        (span,) = [s for s in comment_spans(text) if text[s[0]] == "{"]

        self.assertEqual("{abre", text[span[0] : span[1]])


class N07Tests(unittest.TestCase):
    def test_a_valid_two_line_comment_is_balanced(self):
        self.assertEqual([], check_balance("1. e4 {um comentario\nem duas linhas} e5"))

    def test_a_parenthesis_inside_a_comment_does_not_end_the_scope(self):
        text = "1. e4 (1... c5 {veja\n) aqui} 2. Nf3) e5"

        end = scope_end(text, text.index("(") + 1)

        self.assertEqual(text.rindex(")"), end)

    def test_an_unclosed_brace_does_not_swallow_a_later_comment(self):
        text = "1. e4 (1... c5 {aberto\n\n2. Nf3) {real} e5"
        position = text.index("c5")

        open_index, close_index, kind = find_enclosing_delimiter(text, position)

        self.assertEqual(("()", text.index("("), text.index(")")), (kind, open_index, close_index))
        self.assertIn("{real}", apply(text, unwrap_at(text, position)))

    def test_a_diagram_mark_inside_a_comment_is_left_alone(self):
        text = "1. e4 {veja o [D] acima} e5 [D] 2. Nf3"

        fixed = apply(text, normalize_diagram_marks(text))

        self.assertEqual("1. e4 {veja o [D] acima} e5 {[#]} 2. Nf3", fixed)


class L16Tests(unittest.TestCase):
    def test_a_drawing_command_inside_a_bracket_variation_survives(self):
        converted = convert_book_text("1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 [3...Nf6 [%cal Ge4] 4.O-O] 4.Ba4").text

        self.assertIn("[%cal Ge4]", converted)
        self.assertEqual(converted.count("("), converted.count(")"), converted)

    def test_brackets_inside_a_comment_are_not_turned_into_a_variation(self):
        converted = convert_book_text("1.e4 e5 {compare [1...c5]} 2.Nf3").text

        self.assertIn("{compare [1...c5]}", converted)


if __name__ == "__main__":
    unittest.main()
