import unittest

import chess

from pgn_live_editor.core.move_tree import (
    build_move_entries,
    find_entry_for_insertion_position,
    render_game_html,
    render_game_movetext,
    render_game_pgn,
    resolve_selection_key,
)
from pgn_live_editor.core.normalizer import LiveTextNormalizer
from pgn_live_editor.core.parser import TolerantParser
from pgn_live_editor.core.pgn_headers import extract_pgn_headers, insert_or_replace_headers
from pgn_live_editor.core.text_insertion import build_board_move_insertion
from pgn_live_editor.core.tokenizer import PGNTokenizer
from pgn_live_editor.core.variation_builder import build_variation_text


class CorePipelineTests(unittest.TestCase):
    def test_normalized_tokens_map_back_to_raw_spans(self):
        """O normalizador nao toca na prosa; quem resolve `Cf3` e o parser, com o tabuleiro."""
        raw_text = "1.e4 e5 mate 2.Cf3 Cc6"

        normalized = LiveTextNormalizer(locale="pt").normalize_with_mapping(raw_text)
        tokens = PGNTokenizer().tokenize(normalized)

        # `mate` e uma palavra de prosa aqui: nao vira mais '#'.
        # `Cf3`/`Cc6` chegam intactos ao tokenizador, como candidatos a lance.
        self.assertEqual(normalized.text, "1. e4 e5 mate 2. Cf3 Cc6")

        interesting = [(token.type, token.value, raw_text[token.start_index : token.end_index]) for token in tokens]
        self.assertEqual(
            interesting,
            [
                ("MOVE_NUMBER", "1.", "1."),
                ("SAN_MOVE", "e4", "e4"),
                ("SAN_MOVE", "e5", "e5"),
                ("TEXT", "mate", "mate"),
                ("MOVE_NUMBER", "2.", "2."),
                ("MOVE_CANDIDATE", "Cf3", "Cf3"),
                ("MOVE_CANDIDATE", "Cc6", "Cc6"),
            ],
        )

    def test_locale_candidates_are_resolved_against_the_board(self):
        """O span continua apontando para o texto bruto original (`Cf3`)."""
        raw_text = "1.e4 e5 2.Cf3 Cc6 3.Bb5"

        normalized = LiveTextNormalizer(locale="pt").normalize_with_mapping(raw_text)
        ast = TolerantParser(locale="pt").parse(PGNTokenizer().tokenize(normalized))

        self.assertEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"], [move.display_san for move in ast.moves])
        self.assertTrue(all(move.is_valid for move in ast.moves))

        knight = ast.moves[2]
        self.assertEqual("Cf3", knight.san)
        self.assertEqual("Nf3", knight.corrected_san)
        self.assertEqual("2.Cf3", raw_text[knight.start_index : knight.end_index])
        self.assertEqual([], [issue for issue in ast.issues if issue.severity != "info"])

    def test_normalizer_preserves_comment_text(self):
        raw_text = "{1. Abertura Escocesa (C45) Schachtage} 1.e4 e5 2.Nf3 {Defesa Philidor (2...d6) e cheque}"

        normalized = LiveTextNormalizer(locale="pt").normalize_with_mapping(raw_text)

        self.assertIn("{1. Abertura Escocesa (C45) Schachtage}", normalized.text)
        self.assertIn("{Defesa Philidor (2...d6) e cheque}", normalized.text)
        self.assertIn("1. e4 e5 2. Nf3", normalized.text)

    def test_tokenizer_recognizes_attached_move_annotations(self):
        # `+-` e `-+` sao `$18`/`$19` ("ganha"), nao `±`/`∓` ("claramente
        # melhor"): sao simbolos diferentes na chave de todo livro, e a forma
        # ASCII de `±` e `+/-`. Trocar um pelo outro rebaixa a avaliacao.
        text = "1.e4! e5?! 2.Nf3!! Nc6∞ 3.Bb5!? a6?? 4.Ba4+- Nf6-+ 5.O-O+/= Be7=/+ 6.d4+/- Nd7-/+"

        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
        tokens = PGNTokenizer().tokenize(normalized)

        self.assertEqual(
            [(token.type, token.value) for token in tokens],
            [
                ("MOVE_NUMBER", "1."),
                ("SAN_MOVE", "e4"),
                ("NAG", "!"),
                ("SAN_MOVE", "e5"),
                ("NAG", "?!"),
                ("MOVE_NUMBER", "2."),
                ("SAN_MOVE", "Nf3"),
                ("NAG", "!!"),
                ("SAN_MOVE", "Nc6"),
                ("NAG", "∞"),
                ("MOVE_NUMBER", "3."),
                ("SAN_MOVE", "Bb5"),
                ("NAG", "!?"),
                ("SAN_MOVE", "a6"),
                ("NAG", "??"),
                ("MOVE_NUMBER", "4."),
                ("SAN_MOVE", "Ba4"),
                ("NAG", "+-"),
                ("SAN_MOVE", "Nf6"),
                ("NAG", "-+"),
                ("MOVE_NUMBER", "5."),
                ("SAN_MOVE", "O-O"),
                ("NAG", "⩲"),
                ("SAN_MOVE", "Be7"),
                ("NAG", "⩱"),
                ("MOVE_NUMBER", "6."),
                ("SAN_MOVE", "d4"),
                ("NAG", "±"),
                ("SAN_MOVE", "Nd7"),
                ("NAG", "∓"),
            ],
        )

    def test_tokenizer_recognizes_the_symbol_font_of_printed_books(self):
        """`²³µ+–` da fonte de simbolos do livro, no texto cru vindo do PDF.

        Esta e a forma em que o texto **chega**: o NFKC do normalizador
        transformaria `²` em `2` e `³` em `3`, entao a conversao tem de
        acontecer antes dele.
        """
        text = "1.e4² c5³ 2.Nf3µ d6± 3.d4+– cxd4–+ 4.Nxd4© Nf6‚ 5.Nc3ƒ a6„"

        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
        tokens = PGNTokenizer().tokenize(normalized)

        self.assertEqual(
            [token.value for token in tokens if token.type == "NAG"],
            ["⩲", "⩱", "∓", "±", "+-", "-+", "©", "→", "↑", "⇆"],
        )
        self.assertEqual([token.type for token in tokens if token.type == "MOVE_CANDIDATE"], [])

    def test_parser_attaches_direct_annotations_to_moves(self):
        text = "1.e4! e5?! 2.Nf3!! Nc6∞ 3.Bb5!? a6??"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(ast.issues, [])
        self.assertEqual(ast.moves[0].nags, ["!"])
        self.assertEqual(ast.moves[1].nags, ["?!"])
        self.assertEqual(ast.moves[2].nags, ["!!"])
        self.assertEqual(ast.moves[3].nags, ["∞"])
        self.assertEqual(ast.moves[4].nags, ["!?"])
        self.assertEqual(ast.moves[5].nags, ["??"])
        self.assertEqual(rendered, "1. e4 ! e5 ?! 2. Nf3 !! Nc6 ∞ 3. Bb5 !? a6 ?? *")

    def test_variation_can_branch_from_current_position(self):
        text = "1. e4 e5 2. Nf3 (2... Nc6 3. Bb5 a6) 2... d6"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)

        nf3 = ast.moves[2]
        self.assertEqual(nf3.san, "Nf3")
        self.assertEqual(nf3.variation_anchor_after, [True])
        self.assertTrue(nf3.variations[0][0].is_valid)
        self.assertTrue(nf3.variations[0][1].is_valid)
        self.assertEqual(ast.issues, [])

    def test_black_move_numbers_keep_ellipsis_and_ambiguous_variation_stays_legal(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Nc3 Nf6 4.d4 exd4 5.Nxd4 Bb4 6.Nxc6 bxc6 7.Bd3 d5 (7... 0-0 8.0-0 d5 9.exd5 cxd5)"

        normalized = LiveTextNormalizer(locale="pt").normalize_with_mapping(text)
        self.assertIn("7... O-O", normalized.text)
        self.assertNotIn("7. ..", normalized.text)

        tokens = PGNTokenizer().tokenize(normalized)
        ast = TolerantParser().parse(tokens)

        bd3 = ast.moves[12]
        self.assertEqual(bd3.san, "Bd3")
        self.assertEqual(bd3.variation_anchor_after, [True])

        variation_moves = bd3.variations[0]
        self.assertEqual([move.display_san for move in variation_moves], ["O-O", "O-O", "d5", "exd5", "cxd5"])
        self.assertTrue(all(move.is_valid for move in variation_moves))
        self.assertEqual(ast.issues, [])

    def test_move_tree_renders_clickable_variation_preview(self):
        text = "1. e4 e5 2. Nf3 (2... Nc6 3. Bb5 a6) 2... d6 3. d4 exd4"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        entries = build_move_entries(ast)

        variation_entry = next(entry for entry in entries if entry.node.san == "Nc6" and entry.depth == 1)
        restored_entry = resolve_selection_key(entries, variation_entry.selection_key)
        html = render_game_html(ast, entries, variation_entry.node_id)

        self.assertIsNotNone(restored_entry)
        self.assertEqual(restored_entry.node_id, variation_entry.node_id)
        self.assertEqual([move.san for move in variation_entry.path], ["e4", "e5", "Nf3", "Nc6"])
        self.assertIn("href='move:", html)
        self.assertIn(f"name='{variation_entry.node_id}'", html)
        self.assertIn("move current", html)

    def test_adjacent_text_tokens_merge_into_single_comment(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 loose text here"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)

        self.assertEqual(ast.moves[-1].comments_after, ["loose text here"])

    def test_backward_reference_inside_variation_becomes_comment(self):
        text = (
            "1.e4 e5 2.Nf3 Nc6 3.Nc3 Nf6 4.d4 exd4 5.Nxd4 Bb4 6.Nxc6 bxc6 "
            "7.Bd3 d5 (7... 0-0 8.0-0 d5 9.exd5 cxd5 {em geral} 8...Re8 seguido por ...d6)"
        )

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)

        self.assertEqual(ast.issues, [])
        variation_tail = ast.moves[12].variations[0][-1]
        self.assertEqual(variation_tail.display_san, "cxd5")
        self.assertEqual(variation_tail.comments_after, ["em geral 8... Re8 seguido por ...d6"])

    def test_subvariation_is_reanchored_to_the_correct_position(self):
        text = (
            "1.e4 e5 2.Nf3 Nc6 3.Nc3 Nf6 "
            "(4.Bc4 {contra a qual} 4...Nxe4 {e uma boa resposta. Por exemplo:} "
            "5.Nxe4 d5 {ou} (5.Bxf7+ Kxf7 6.Nxd4 d5) {em ambos os casos com boa partida.})"
        )

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)

        self.assertEqual(ast.issues, [])

        outer_variation = ast.moves[-1].variations[0]
        black_reply = outer_variation[1]
        self.assertEqual(black_reply.san, "Nxe4")
        self.assertEqual(black_reply.variation_anchor_after, [True])

        subvariation = black_reply.variations[0]
        self.assertEqual([move.display_san for move in subvariation], ["Bxf7+", "Kxf7", "Nxd4", "d5"])
        self.assertEqual(subvariation[0].comments_before, ["ou"])
        self.assertEqual(outer_variation[-1].comments_after, ["em ambos os casos com boa partida."])

    def test_reanchored_variation_keeps_mainline_comment_when_variation_has_its_own_intro(self):
        text = "1.e4 e5 2.Nf3 {comentário principal} ({intro da variante} 2.Bc4 {linha secundaria}) 2...Nc6"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(ast.moves[2].comments_after, ["comentário principal"])
        self.assertEqual(ast.moves[1].variations[0][0].comments_before, ["intro da variante"])
        self.assertEqual(
            rendered,
            "1. e4 e5 2. Nf3 {comentário principal} ( {intro da variante} 2. Bc4 {linha secundaria} ) 2... Nc6 *",
        )

    def test_render_game_movetext_preserves_user_comment_text(self):
        text = "{Abertura Escocesa (C45) Schachtage} 1.e4 e5 2.Nf3 {cheque e capítulo}"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(rendered, "{Abertura Escocesa (C45) Schachtage} 1. e4 e5 2. Nf3 {cheque e capítulo} *")

    def test_nested_sibling_variations_render_before_comment_of_main_variation_move(self):
        text = (
            "1.e4 e5 2.Nf3 Nc6 3.d4 exd4 4.Nxd4 4...Bc5 "
            "({A resposta} 4...Nf6 ({A partida 6 ilustra a opção} 4...g6) 5.Be3)"
        )

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(
            rendered,
            "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 4... Bc5 ( {A resposta} 4... Nf6 ( {A partida 6 ilustra a opção} 4... g6 ) 5. Be3 ) *",
        )

    def test_leading_nested_sibling_variations_are_hoisted_to_parent_level(self):
        text = (
            "1.e4 e5 2.Nf3 Nc6 3.d4 exd4 4.Nxd4 4...Bc5 "
            "({A resposta} 4...Nf6 ({A partida 6 ilustra a opção} 4...g6) "
            "({A troca} 4...Nxd4 5.Qxd4) 5.Be3)"
        )

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(
            rendered,
            "1. e4 e5 2. Nf3 Nc6 3. d4 exd4 4. Nxd4 4... Bc5 ( {A resposta} 4... Nf6 "
            "( {A partida 6 ilustra a opção} 4... g6 ) ( {A troca} 4... Nxd4 5. Qxd4 ) 5. Be3 ) *",
        )

    def test_render_game_movetext_preserves_comments_and_variations(self):
        text = (
            "1.e4 e5 2.Nf3 Nc6 3.Nc3 Nf6 "
            "(4.Bc4 {contra a qual} 4...Nxe4 {e uma boa resposta. Por exemplo:} "
            "5.Nxe4 d5 {ou} (5.Bxf7+ Kxf7 6.Nxd4 d5) {em ambos os casos com boa partida.})"
        )

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="pt").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(
            rendered,
            # `5... d5` depois do fecha-parenteses: o numero e reafirmado para
            # que o PGN nao dependa de o leitor contar lances (defeito D13).
            "1. e4 e5 2. Nf3 Nc6 3. Nc3 Nf6 ( 4. Bc4 {contra a qual} 4... Nxe4 {e uma boa resposta. Por exemplo:} "
            "5. Nxe4 ( {ou} 5. Bxf7+ Kxf7 6. Nxd4 d5 ) 5... d5 {em ambos os casos com boa partida.} ) *",
        )

    def test_render_game_pgn_includes_standard_headers(self):
        text = "1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 1-0"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_pgn(ast)

        self.assertTrue(rendered.startswith('[Event "PGN Live Editor Export"]\n[Site "?"]\n[Date "????.??.??"]'))
        self.assertIn('[White "?"]', rendered)
        self.assertIn('[Black "?"]', rendered)
        self.assertIn('[Result "1-0"]', rendered)
        self.assertTrue(rendered.endswith("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0"))

    def test_render_game_pgn_converts_symbolic_annotations_to_numeric_nags(self):
        text = "1.e4! e5?! 2.Nf3!! Nc6∞ 3.Bb5!? a6?? 4.Ba4+- Nf6-+ 5.O-O+/= Be7=/+ *"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_pgn(ast)

        self.assertIn(
            "1. e4 $1 e5 $6 2. Nf3 $3 Nc6 $13 3. Bb5 $5 a6 $4 4. Ba4 $18 Nf6 $19 5. O-O $14 Be7 $15 *", rendered
        )

    def test_render_game_pgn_picks_the_side_of_the_symbols_that_have_two_numbers(self):
        """`©`, `→`, `⇆` mudam de numero conforme quem jogou; `⨀` conforme quem joga."""
        text = "1.e4→ c5→ 2.Nf3© d6© 3.d4⇆ cxd4⇆ *"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)

        self.assertIn("1. e4 $40 c5 $41 2. Nf3 $44 d6 $45 3. d4 $132 cxd4 $133 *", render_game_pgn(ast))

    def test_render_game_pgn_converts_only_move_and_zugzwang_symbols(self):
        text = "1.e4⨀ e5⨀ 2.Nf3□ Nc6 *"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_pgn(ast)

        self.assertIn("1. e4 $23 e5 $22 2. Nf3 $7 Nc6 *", rendered)

    def test_asterisk_result_round_trips_without_becoming_comment(self):
        text = "1.e4 e5 *"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        rendered = render_game_movetext(ast)

        self.assertEqual(ast.result, "*")
        self.assertEqual(ast.moves[-1].comments_after, [])
        self.assertEqual(rendered, "1. e4 e5 *")

    def test_insertion_position_after_whitespace_resolves_to_previous_move(self):
        text = "1.e4 e5 2.Nf3"

        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
        tokens = PGNTokenizer().tokenize(normalized)
        ast = TolerantParser().parse(tokens)
        entries = build_move_entries(ast)

        cursor_position = normalized.text.index("2.") - 1
        entry = find_entry_for_insertion_position(entries, cursor_position)

        self.assertIsNotNone(entry)
        self.assertEqual(entry.node.display_san, "e5")

    def test_insertion_position_before_first_move_returns_none(self):
        text = "1.e4 e5"

        normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
        tokens = PGNTokenizer().tokenize(normalized)
        ast = TolerantParser().parse(tokens)
        entries = build_move_entries(ast)

        self.assertIsNone(find_entry_for_insertion_position(entries, 0))

    def test_board_move_insertion_adds_move_number_when_needed(self):
        inserted = build_board_move_insertion("", 0, 0, "e4", chess.WHITE, 1)
        self.assertEqual(inserted, "1. e4")

    def test_board_move_insertion_reuses_existing_move_number_marker(self):
        inserted = build_board_move_insertion("1. ", 3, 3, "e4", chess.WHITE, 1)
        self.assertEqual(inserted, "e4")

    def test_board_move_insertion_adds_black_move_number_after_white_move(self):
        inserted = build_board_move_insertion("1. e4", 5, 5, "e5", chess.BLACK, 1)
        self.assertEqual(inserted, " 1... e5")

    def test_variation_builder_formats_line_from_white_to_move(self):
        rendered = build_variation_text(chess.STARTING_FEN, ["e4", "e5", "Nf3"])
        self.assertEqual(rendered, "( 1. e4 e5 2. Nf3 )")

    def test_variation_builder_formats_line_from_black_to_move(self):
        board = chess.Board()
        board.push_san("e4")
        rendered = build_variation_text(board.fen(), ["e5", "Nf3", "Nc6"])
        self.assertEqual(rendered, "( 1... e5 2. Nf3 Nc6 )")

    def test_extract_pgn_headers_returns_body_and_offset(self):
        raw_text = '[Event "Teste"]\n[White "Alice"]\n\n1.e4 e5'

        headers, body, offset = extract_pgn_headers(raw_text)

        self.assertEqual(headers, {"Event": "Teste", "White": "Alice"})
        self.assertEqual(body, "1.e4 e5")
        self.assertEqual(offset, len('[Event "Teste"]\n[White "Alice"]\n\n'))

    def test_insert_or_replace_headers_preserves_existing_values(self):
        updated = insert_or_replace_headers('[Event "Teste"]\n\n1.e4 e5')

        self.assertIn('[Event "Teste"]', updated)
        self.assertIn('[White "?"]', updated)
        self.assertTrue(updated.endswith("1.e4 e5"))

    def test_render_game_pgn_uses_headers_from_ast(self):
        text = "1.e4 e5"

        tokens = PGNTokenizer().tokenize(LiveTextNormalizer(locale="en").normalize_with_mapping(text))
        ast = TolerantParser().parse(tokens)
        ast.headers = [("Event", "Minha Partida"), ("White", "Alice"), ("Black", "Bob")]
        rendered = render_game_pgn(ast)

        self.assertIn('[Event "Minha Partida"]', rendered)
        self.assertIn('[White "Alice"]', rendered)
        self.assertIn('[Black "Bob"]', rendered)
        self.assertIn('[Result "*"]', rendered)


if __name__ == "__main__":
    unittest.main()
