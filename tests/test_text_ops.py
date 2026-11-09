"""Fase 3: gestos de edicao estrutural sobre o texto bruto."""

from __future__ import annotations

import unittest

import _isolation  # noqa: F401
import chess

from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser, iter_moves
from pgn_live_editor.core.paste_cleanup import CleanupOptions, clean_pdf_text
from pgn_live_editor.core.text_ops import (
    apply_edits,
    check_balance,
    insert_comment,
    move_variation,
    paragraph_to_comment,
    promote_variation,
    reanchor_variation,
    renumber_document,
    renumber_movetext,
    unwrap_at,
    wrap_as_comment,
    wrap_as_variation,
)
from pgn_live_editor.core.tokenizer import PGNTokenizer


def parse(text: str, locale: str = "pt"):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    return TolerantParser(locale=locale).parse(PGNTokenizer().tokenize(normalized))


def move_named(ast, san: str):
    """O lance de SAN `san`, onde ele estiver -- linha principal ou variante."""
    return next(move for move in iter_moves(ast.moves) if move.display_san == san)


def run(text: str, operation) -> str:
    result = operation(text, parse(text))
    return apply_edits(text, result.edits) if result.ok else text


class WrapAsCommentTests(unittest.TestCase):
    def test_selection_becomes_a_comment(self):
        text = "1.e4 e5 as brancas ficam melhor 2.Nf3"
        start = text.index("as brancas")
        end = text.index(" 2.Nf3")

        result = wrap_as_comment(text, start, end)

        self.assertEqual("1.e4 e5 {as brancas ficam melhor} 2.Nf3", apply_edits(text, result.edits))

    def test_edges_of_the_selection_are_trimmed(self):
        text = "1.e4 e5   comentário   2.Nf3"
        result = wrap_as_comment(text, text.index("  comentário"), text.index("  2.Nf3") + 2)

        self.assertEqual("1.e4 e5   {comentário}   2.Nf3", apply_edits(text, result.edits))

    def test_inner_braces_become_parentheses_because_pgn_does_not_nest_them(self):
        text = "1.e4 texto {com chave} dentro 2.Nf3"
        result = wrap_as_comment(text, text.index("texto"), text.index(" 2.Nf3"))

        applied = apply_edits(text, result.edits)
        self.assertEqual("1.e4 {texto (com chave) dentro} 2.Nf3", applied)
        self.assertEqual(1, applied.count("{"))

    def test_line_breaks_and_pdf_space_entities_are_collapsed_in_comment(self):
        text = "1.e4 If the stronger side has any pawn other than an a- or h-pawn,\n the position is always winning automatically. There is only\n&#x20;one exception!"
        result = wrap_as_comment(text, text.index("If"), len(text))

        self.assertEqual(
            "1.e4 {If the stronger side has any pawn other than an a- or h-pawn, the position is always winning automatically. There is only one exception!}",
            apply_edits(text, result.edits),
        )

    def test_empty_selection_is_refused_with_an_explanation(self):
        result = wrap_as_comment("1.e4 e5", 3, 3)

        self.assertFalse(result.ok)
        self.assertIn("Selecione", result.message)

    def test_already_a_comment_is_left_alone(self):
        text = "1.e4 {já e comentário} 2.Nf3"
        result = wrap_as_comment(text, text.index("{"), text.index("}") + 1)

        self.assertFalse(result.ok)


class InsertCommentTests(unittest.TestCase):
    """Colar prosa como comentario do lance escolhido, antes ou depois dele."""

    def paste(self, text: str, san: str, body: str, *, before: bool) -> str:
        result = insert_comment(text, move_named(parse(text), san), body, before=before)
        return apply_edits(text, result.edits) if result.ok else text

    def test_the_comment_lands_after_the_move(self):
        self.assertEqual(
            "1.e4 {prosa do livro} e5 2.Nf3",
            self.paste("1.e4 e5 2.Nf3", "e4", "prosa do livro", before=False),
        )

    def test_the_comment_lands_before_the_move_number(self):
        """`{c} 2.Nf3`, e nao `2. {c} Nf3`: o numero e do lance."""
        self.assertEqual(
            "1.e4 e5 {prosa do livro} 2.Nf3",
            self.paste("1.e4 e5 2.Nf3", "Nf3", "prosa do livro", before=True),
        )

    def test_before_and_after_are_different_sides_of_a_variation(self):
        """A razao de existirem os dois lados. Entre `1.e4` e `1...e5` ha mais
        de um lugar possivel, e o comentario que fala de `e5` não pode acabar
        dentro da variante de `e4`."""
        text = "1.e4 ( 1.d4 d5 ) e5"

        self.assertEqual(
            "1.e4 {sobre e4} ( 1.d4 d5 ) e5",
            self.paste(text, "e4", "sobre e4", before=False),
        )
        self.assertEqual(
            "1.e4 ( 1.d4 d5 ) {sobre e5} e5",
            self.paste(text, "e5", "sobre e5", before=True),
        )

    def test_the_evaluation_symbol_stays_glued_to_the_move(self):
        """`1.e4! {c}`, e nao `1.e4 {c} !`: o `!` e do lance."""
        self.assertEqual(
            "1.e4! {duvidoso} e5",
            self.paste("1.e4! e5", "e4", "duvidoso", before=False),
        )

    def test_a_book_symbol_also_stays_glued(self):
        self.assertEqual(
            "1.e4 e5 2.Nf3 ⩲ {brancas melhor} Nc6",
            self.paste("1.e4 e5 2.Nf3 ⩲ Nc6", "Nf3", "brancas melhor", before=False),
        )

    def test_pasting_after_a_move_that_already_has_a_comment_joins_the_two(self):
        """Um comentario por lance: o livro não escreve dois colados, e juntar
        preserva as setas e casas coloridas que ja moram no comentario."""
        self.assertEqual(
            "1.e4 {[%cal Ge2e4] o que já tinha, e o que veio agora} e5",
            self.paste("1.e4 {[%cal Ge2e4] o que já tinha,} e5", "e4", "e o que veio agora", before=False),
        )

    def test_pasting_before_a_move_does_not_touch_the_comment_of_the_previous_one(self):
        """Ali o vizinho a esquerda e o comentario do lance **anterior**."""
        self.assertEqual(
            "1.e4 {sobre e4} {sobre e5} e5",
            self.paste("1.e4 {sobre e4} e5", "e5", "sobre e5", before=True),
        )

    def test_after_a_move_that_a_variation_replaces_the_prose_introduces_the_variation(self):
        """Não e escolha deste gesto, e sim a convenção do PGN da editora, que o
        parser reproduz: `7. Qd2 ( {Placing the queen on d2 …} 7. Be2 … )`."""
        applied = self.paste("1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 ) d6", "Nf3", "prosa", before=False)

        self.assertEqual("1.e4 c5 2.Nf3 {prosa} ( 2.Nc3 Nc6 ) d6", applied)
        self.assertEqual(["prosa"], move_named(parse(applied), "Nc3").comments_before)

    def test_a_pasted_page_becomes_one_line(self):
        """Um comentario não atravessa linha em branco -- passando dela, deixa
        de ser comentario para o tokenizador."""
        applied = self.paste("1.e4 e5", "e4", "primeiro parágrafo\n\nsegundo parágrafo\n", before=False)

        self.assertEqual("1.e4 {primeiro parágrafo segundo parágrafo} e5", applied)

    def test_text_copied_with_the_braces_does_not_nest(self):
        applied = self.paste("1.e4 e5", "e4", "{já veio entre chaves}", before=False)

        self.assertEqual("1.e4 {já veio entre chaves} e5", applied)
        self.assertEqual(1, applied.count("{"))

    def test_inner_braces_become_parentheses(self):
        applied = self.paste("1.e4 e5", "e4", "prosa {com chave} dentro", before=False)

        self.assertEqual("1.e4 {prosa (com chave) dentro} e5", applied)

    def test_nothing_to_paste_is_refused_with_an_explanation(self):
        result = insert_comment("1.e4 e5", move_named(parse("1.e4 e5"), "e4"), "   \n ", before=False)

        self.assertFalse(result.ok)
        self.assertIn("Não ha texto", result.message)

    def test_the_message_names_the_move_that_received_the_comment(self):
        ast = parse("1.e4 e5 2.Nf3")
        after = insert_comment("1.e4 e5 2.Nf3", move_named(ast, "e5"), "prosa", before=False)
        before = insert_comment("1.e4 e5 2.Nf3", move_named(ast, "Nf3"), "prosa", before=True)

        self.assertEqual("Comentário colado depois de 1... e5.", after.message)
        self.assertEqual("Comentário colado antes de 2. Nf3.", before.message)

    def test_the_new_comment_is_left_selected(self):
        text = "1.e4 e5"
        result = insert_comment(text, move_named(parse(text), "e4"), "prosa", before=False)
        applied = apply_edits(text, result.edits)

        self.assertEqual("{prosa}", applied[result.select_start : result.select_end])

    def test_the_result_parses_as_the_comment_of_the_chosen_move(self):
        applied = self.paste("1.e4 e5 2.Nf3 Nc6", "e5", "as pretas respondem no centro", before=False)
        reparsed = parse(applied)

        self.assertEqual(["as pretas respondem no centro"], move_named(reparsed, "e5").comments_after)
        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.display_san for move in reparsed.moves])


class WrapAsVariationTests(unittest.TestCase):
    def test_selection_becomes_a_variation_with_the_right_move_number(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Bb5 a6"
        ast = parse(text)
        start = text.index("Nc6")
        end = text.index(" 3.Bb5")

        result = wrap_as_variation(text, start, end, ast)
        applied = apply_edits(text, result.edits)

        # `Nc6` e o lance 2 das pretas: a variante precisa dizer isso.
        self.assertEqual("1.e4 e5 2.Nf3 (2... Nc6) 3.Bb5 a6", applied)

    def test_move_number_is_not_duplicated_when_the_selection_already_has_one(self):
        text = "1.e4 e5 2.Nf3 2... Nc6 3.Bb5"
        ast = parse(text)
        start = text.index("2... Nc6")
        end = start + len("2... Nc6")

        applied = apply_edits(text, wrap_as_variation(text, start, end, ast).edits)

        self.assertEqual("1.e4 e5 2.Nf3 (2... Nc6) 3.Bb5", applied)

    def test_following_mainline_move_recovers_its_number(self):
        text = "1.e4 e5 2.Nf3 Nc6 Bb5"
        ast = parse(text)
        start = text.index("Nc6")
        end = start + len("Nc6")

        applied = apply_edits(text, wrap_as_variation(text, start, end, ast).edits)

        # Sem o `3.` o leitor teria de contar lances para saber de quem e a vez.
        self.assertEqual("1.e4 e5 2.Nf3 (2... Nc6) 3. Bb5", applied)

    def test_the_result_still_parses_as_a_variation(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Bb5 a6"
        ast = parse(text)
        start = text.index("3.Bb5")

        applied = apply_edits(text, wrap_as_variation(text, start, len(text), ast).edits)

        self.assertEqual("1.e4 e5 2.Nf3 Nc6 (3.Bb5 a6)", applied)

        reparsed = parse(applied)
        self.assertEqual(["e4", "e5", "Nf3", "Nc6"], [move.display_san for move in reparsed.moves])
        variation = next(move.variations[0] for move in reparsed.moves if move.variations)
        self.assertEqual(["Bb5", "a6"], [move.display_san for move in variation])
        self.assertEqual([], [issue for issue in reparsed.issues if issue.severity != "info"])


class UnwrapTests(unittest.TestCase):
    def test_removes_the_braces_around_the_cursor(self):
        text = "1.e4 e5 {um comentário} 2.Nf3"
        applied = apply_edits(text, unwrap_at(text, text.index("comentário")).edits)

        self.assertEqual("1.e4 e5 um comentário 2.Nf3", applied)

    def test_removes_the_parentheses_around_the_cursor(self):
        text = "1.e4 e5 2.Nf3 (2... Nc6 3.Bb5) 3.d4"
        applied = apply_edits(text, unwrap_at(text, text.index("Nc6")).edits)

        self.assertEqual("1.e4 e5 2.Nf3 2... Nc6 3.Bb5 3.d4", applied)

    def test_innermost_delimiter_wins(self):
        text = "1.e4 (2.Nf3 (3.Bb5 alvo) fim)"
        applied = apply_edits(text, unwrap_at(text, text.index("alvo")).edits)

        self.assertEqual("1.e4 (2.Nf3 3.Bb5 alvo fim)", applied)

    def test_cursor_outside_any_delimiter_is_refused(self):
        result = unwrap_at("1.e4 e5 2.Nf3", 3)

        self.assertFalse(result.ok)
        self.assertIn("não esta dentro", result.message)


class ParagraphToCommentTests(unittest.TestCase):
    def test_loose_prose_between_moves_becomes_a_comment(self):
        text = "1.e4 e5 2.Nf3 recurso tatico fortissimo 3.Bb5 a6"
        position = text.index("tatico")

        applied = run(text, lambda t, ast: paragraph_to_comment(t, position, ast))

        self.assertEqual("1.e4 e5 2.Nf3 {recurso tatico fortissimo} 3.Bb5 a6", applied)

    def test_prose_at_the_end_of_the_document(self):
        text = "1.e4 e5 2.Nf3 excelente desenvolvimento"
        applied = run(text, lambda t, ast: paragraph_to_comment(t, t.index("excelente"), ast))

        self.assertEqual("1.e4 e5 2.Nf3 {excelente desenvolvimento}", applied)

    def test_does_not_cross_an_existing_comment(self):
        text = "1.e4 {já comentado} solto 2.Nf3"
        applied = run(text, lambda t, ast: paragraph_to_comment(t, t.index("solto"), ast))

        self.assertEqual("1.e4 {já comentado} {solto} 2.Nf3", applied)


class RenumberTests(unittest.TestCase):
    def test_orphan_dot_from_ocr_is_repaired(self):
        text = "1.e4 e5 2.Nf3 10. ... Nc6"
        applied = run(text, renumber_document)

        self.assertNotIn("10", applied)
        self.assertEqual("1. e4 e5 2. Nf3 2... Nc6", applied)

    def test_missing_numbers_are_added_and_wrong_ones_fixed(self):
        text = "1.e4 e5 7.Nf3 Nc6 3.Bb5"
        applied = run(text, renumber_document)

        self.assertEqual("1. e4 e5 2. Nf3 Nc6 3. Bb5", applied)

    def test_black_move_after_a_variation_gets_its_number_back(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4) Nc6"
        applied = run(text, renumber_document)

        self.assertIn("2... Nc6", applied)

    def test_numbering_inside_variations_is_recalculated(self):
        text = "1.e4 e5 2.Nf3 (9.Bc4 Nc6) 2... Nf6"
        applied = run(text, renumber_document)

        self.assertIn("(2. Bc4 Nc6)", applied)

    def test_already_correct_text_is_left_alone(self):
        result = renumber_document("1. e4 e5 2. Nf3 Nc6", parse("1. e4 e5 2. Nf3 Nc6"))

        self.assertFalse(result.ok)
        self.assertIn("já esta correta", result.message)

    def test_renumbering_stops_at_an_unrecognized_move(self):
        """Depois de um lance perdido o tabuleiro nao vale: nada de inventar numeros."""
        text = "1.e4 e5 2.Nf3 Xf6z 9.Bb5 12.a6"
        ast = parse(text)
        applied = run(text, renumber_document)

        self.assertFalse(ast.moves[3].is_valid)
        # O comeco, que e confiavel, foi corrigido...
        self.assertTrue(applied.startswith("1. e4 e5 2. Nf3"))
        # ...e o resto ficou intocado, para o alerta pedir a correcao real.
        self.assertIn("9.Bb5", applied)
        self.assertIn("12.a6", applied)

    def test_renumbering_is_idempotent(self):
        text = "1.e4 e5 7.Nf3 Nc6 3.Bb5 (9.Bc4 d6)"
        once = run(text, renumber_document)
        twice = run(once, renumber_document)

        self.assertEqual(once, twice)


def owner_of_first_variation(ast):
    """O parser ancora a variante numa posicao, entao o dono nem sempre e o
    lance que a variante substitui. Achamos quem realmente a carrega."""
    from pgn_live_editor.core.parser import iter_moves

    return next(move for move in iter_moves(ast.moves) if move.variations)


class PromoteVariationTests(unittest.TestCase):
    def test_variation_and_mainline_swap_places(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nc6) 2... Nc6 3.Bb5"
        ast = parse(text)

        result = promote_variation(text, ast, owner_of_first_variation(ast), 0)
        applied = apply_edits(text, result.edits)

        self.assertTrue(result.ok, result.message)
        self.assertEqual("1.e4 e5 2.Bc4 Nc6 (2.Nf3 2... Nc6 3.Bb5)", applied)

    def test_the_promoted_line_becomes_the_mainline_when_reparsed(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nc6) 2... Nc6 3.Bb5"
        ast = parse(text)
        applied = apply_edits(text, promote_variation(text, ast, owner_of_first_variation(ast), 0).edits)

        reparsed = parse(applied)
        self.assertEqual(["e4", "e5", "Bc4", "Nc6"], [move.display_san for move in reparsed.moves])
        self.assertEqual([], [issue for issue in reparsed.issues if issue.severity != "info"])

    def test_promoting_twice_returns_to_the_original_line(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nc6) 2... Nc6 3.Bb5"
        ast = parse(text)
        once = apply_edits(text, promote_variation(text, ast, owner_of_first_variation(ast), 0).edits)

        ast_once = parse(once)
        twice = apply_edits(once, promote_variation(once, ast_once, owner_of_first_variation(ast_once), 0).edits)

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"], [move.display_san for move in parse(twice).moves])

    def test_result_token_is_not_dragged_into_the_variation(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nc6) 2... Nc6 1-0"
        ast = parse(text)
        applied = apply_edits(text, promote_variation(text, ast, owner_of_first_variation(ast), 0).edits)

        self.assertTrue(applied.rstrip().endswith("1-0"), applied)
        self.assertEqual("1-0", parse(applied).result)


class BalanceTests(unittest.TestCase):
    def test_balanced_text_has_no_problems(self):
        self.assertEqual([], check_balance("1.e4 {ok} (2.Nf3 (3.Bb5))"))

    def test_unclosed_paren_is_reported(self):
        problems = check_balance("1.e4 (2.Nf3 Nc6")

        self.assertEqual(1, len(problems))
        self.assertIn("sem ')'", problems[0].message)

    def test_extra_closing_paren_is_reported(self):
        problems = check_balance("1.e4 2.Nf3) Nc6")

        self.assertEqual(1, len(problems))
        self.assertIn("sem '('", problems[0].message)

    def test_unclosed_brace_is_reported(self):
        problems = check_balance("1.e4 {comentário aberto\n2.Nf3")

        self.assertEqual(1, len(problems))
        self.assertIn("nunca fechado", problems[0].message)


class PasteCleanupTests(unittest.TestCase):
    def test_hyphenated_word_is_reassembled(self):
        report = clean_pdf_text("uma ma-\nnobra decisiva", CleanupOptions(join_wrapped_lines=False))

        self.assertEqual("uma manobra decisiva", report.text)
        self.assertEqual(1, report.joined_hyphens)

    def test_compound_names_are_not_glued(self):
        report = clean_pdf_text("a variante Ruy-\nLopez classica", CleanupOptions(join_wrapped_lines=False))

        self.assertIn("Ruy-", report.text)

    def test_page_numbers_are_removed(self):
        report = clean_pdf_text("1.e4 e5\n42\n2.Nf3 Nc6\n- 43 -\n3.Bb5", CleanupOptions(join_wrapped_lines=False))

        self.assertNotIn("42", report.text)
        self.assertNotIn("- 43 -", report.text)
        self.assertIn("2.Nf3", report.text)

    def test_repeated_running_headers_are_removed(self):
        source = "\n".join(
            [
                "ABERTURAS SEMI-ABERTAS",
                "1.e4 c5",
                "ABERTURAS SEMI-ABERTAS",
                "2.Nf3 d6",
                "ABERTURAS SEMI-ABERTAS",
                "3.d4",
            ]
        )
        report = clean_pdf_text(source, CleanupOptions(join_wrapped_lines=False))

        self.assertNotIn("ABERTURAS", report.text)
        self.assertIn("3.d4", report.text)

    def test_paragraph_breaks_survive_the_line_join(self):
        report = clean_pdf_text("primeira linha\nquebrada aqui\n\nsegundo parágrafo")

        self.assertEqual("primeira linha quebrada aqui\n\nsegundo parágrafo", report.text)

    def test_footnote_marks_glued_to_moves_are_removed(self):
        report = clean_pdf_text("1.e4 Nf3¹ e5", CleanupOptions(join_wrapped_lines=False))

        self.assertEqual("1.e4 Nf3 e5", report.text)

    def test_soft_hyphen_and_zero_width_characters_are_dropped(self):
        report = clean_pdf_text("posi­cao com​ ruido", CleanupOptions(join_wrapped_lines=False))

        # A limpeza tira o hífen condicional e o caractere invisivel; acento ela
        # nao poe -- o texto sai como o OCR entregou.
        self.assertEqual("posicao com ruido", report.text)

    def test_cleanup_keeps_the_moves_parseable(self):
        source = "1.e4 c5 2.Nf3 uma ma-\nnobra conhecida\n\n17\n3.d4 cxd4"
        report = clean_pdf_text(source)
        ast = parse(report.text)

        self.assertEqual(["e4", "c5", "Nf3", "d4", "cxd4"], [move.display_san for move in ast.moves])
        self.assertIn("manobra", " ".join(ast.moves[2].comments_after))


class StartFenGesturesTests(unittest.TestCase):
    def test_renumber_respects_a_fragment_that_starts_from_a_diagram(self):
        """Exercicio de livro que comeca no lance 25: a numeracao sai da FEN."""
        fen = "8/8/8/4k3/8/8/4P3/4K3 w - - 12 25"
        text = "e4 Kf6 e5+ Kf5"
        normalized = LiveTextNormalizer(locale="pt").normalize_with_mapping(text)
        ast = TolerantParser(locale="pt").parse(PGNTokenizer().tokenize(normalized), start_fen=fen)

        applied = apply_edits(text, renumber_document(text, ast).edits)

        self.assertEqual(25, chess.Board(fen).fullmove_number)
        self.assertEqual("25. e4 Kf6 26. e5+ Kf5", applied)


if __name__ == "__main__":
    unittest.main()


class ReorganizeVariationsTests(unittest.TestCase):
    """SPEC 3.3: o que faltava para reorganizar sem recortar e colar."""

    def test_a_variation_moves_between_its_siblings(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nf6) (2.d4 exd4) Nc6"
        ast = parse(text)
        owner = next(move for move in ast.moves if len(move.variations) > 1)

        result = move_variation(text, owner, 1, -1)

        self.assertEqual("1.e4 e5 2.Nf3 (2.d4 exd4) (2.Bc4 Nf6) Nc6", apply_edits(text, result.edits))

    def test_moving_past_the_end_says_so_instead_of_doing_nothing(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nf6) Nc6"
        ast = parse(text)
        owner = next(move for move in ast.moves if move.variations)

        result = move_variation(text, owner, 0, 1)

        self.assertFalse(result.ok)
        self.assertIn("fim da lista", result.message)

    def test_a_variation_can_be_hung_on_another_move(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nf6) Nc6 3.Bb5"
        ast = parse(text)
        owner = next(move for move in ast.moves if move.variations)

        result = reanchor_variation(text, owner, 0, ast.moves[-1])

        self.assertEqual("1.e4 e5 2.Nf3 Nc6 3.Bb5 (3... Bc4 Nf6)", apply_edits(text, result.edits))

    def test_reanchoring_renumbers_the_first_move_of_the_variation(self):
        """O numero de dentro falava da posicao antiga."""
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nf6) Nc6 3.Bb5"
        ast = parse(text)
        owner = next(move for move in ast.moves if move.variations)

        result = reanchor_variation(text, owner, 0, ast.moves[0])

        self.assertIn("(1... Bc4 Nf6)", apply_edits(text, result.edits))

    def test_a_variation_cannot_be_hung_on_itself(self):
        text = "1.e4 e5 2.Nf3 (2.Bc4 Nf6) Nc6"
        ast = parse(text)
        owner = next(move for move in ast.moves if move.variations)

        self.assertFalse(reanchor_variation(text, owner, 0, owner).ok)


class SmartPasteTests(unittest.TestCase):
    """SPEC 3.2: colar uma linha copiada de outro ponto do livro."""

    def test_the_pasted_line_is_renumbered_from_the_position(self):
        board = chess.Board()
        for san in ("e4", "e5", "Nf3"):
            board.push_san(san)

        self.assertEqual("2... Nc6 3. Bb5 a6", renumber_movetext("12... Nc6 13.Bb5 a6", board))

    def test_a_line_that_does_not_fit_comes_back_untouched(self):
        """Melhor devolver como estava do que numerar errado em silencio."""
        board = chess.Board()
        board.push_san("e4")

        self.assertEqual("12... Qh8 13.Bb5", renumber_movetext("12... Qh8 13.Bb5", board))

    def test_a_white_move_gets_a_full_number(self):
        self.assertEqual("1. e4 e5", renumber_movetext("7.e4 e5", chess.Board()))

    def test_empty_text_is_left_alone(self):
        self.assertEqual("", renumber_movetext("", chess.Board()))
