"""Linha em branco e sempre fim de paragrafo; resultado nunca e rodape (R-208).

L-03: `join_wrapped_lines` jogava fora as linhas em branco e decidia so pela
margem; se a linha anterior era a mais larga do trecho, o paragrafo seguinte
-- uma regressao `8.f4 ...` -- era colado nela e virava comentario.

N-11: na limpeza de colagem, uma linha so com espacos nao quebrava paragrafo,
e o `1-0` de tres partidas era apagado como "cabecalho repetido".
"""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.book_import import join_wrapped_lines
from pgn_live_editor.core.paste_cleanup import clean_pdf_text

WIDE = "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.f4 Be7 8.Qf3 Qc7 9.O-O-O Nbd7"


class BookParagraphTests(unittest.TestCase):
    def test_a_blank_line_after_the_widest_line_still_breaks_the_paragraph(self):
        lines = [WIDE, "", "8.f4 Qc7 9.Qf3 Nbd7"]

        self.assertEqual([WIDE, "8.f4 Qc7 9.Qf3 Nbd7"], join_wrapped_lines(lines))

    def test_double_spaced_pdf_text_still_joins_across_single_blank_lines(self):
        # PDF que poe linha em branco entre todas as linhas (o corpus do Najdorf
        # e assim): uma linha em branco sozinha nao separa nada; duas separam.
        second = "10.g4 b5 11.g5 Nd7 12.f5 Nc5 13.h4 b4 14.Nce2 Bb7 15.Ng3 d5 16.exd5 Nxd5 17.Bh3"
        lines = [WIDE, "", second, "", "17...Qb6", "", "", "Novo paragrafo aqui."]

        self.assertEqual([f"{WIDE} {second} 17...Qb6", "Novo paragrafo aqui."], join_wrapped_lines(lines))

    def test_a_line_that_reaches_the_margin_without_a_blank_line_is_still_joined(self):
        lines = [WIDE, "10.g4 b5"]

        self.assertEqual([f"{WIDE} 10.g4 b5"], join_wrapped_lines(lines))


class PasteCleanupTests(unittest.TestCase):
    def test_a_line_with_only_spaces_is_a_paragraph_break(self):
        cleaned = clean_pdf_text("Primeiro paragrafo.\n   \nSegundo paragrafo.").text

        self.assertEqual("Primeiro paragrafo.\n\nSegundo paragrafo.", cleaned)

    def test_results_and_move_lines_are_never_removed_as_running_headers(self):
        text = "\n".join(
            [
                "Capitulo 3", "1.e4 e5 2.Nf3 Nc6", "1-0", "",
                "Capitulo 3", "1.e4 e5 2.Nf3 Nc6", "1-0", "",
                "Capitulo 3", "1.e4 e5 2.Nf3 Nc6", "1-0",
            ]
        )  # fmt: skip

        cleaned = clean_pdf_text(text).text

        self.assertEqual(3, cleaned.count("1-0"))
        self.assertEqual(3, cleaned.count("2.Nf3 Nc6"))
        self.assertNotIn("Capitulo 3", cleaned)


if __name__ == "__main__":
    unittest.main()
