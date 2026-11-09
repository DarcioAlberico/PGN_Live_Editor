"""A partida como arvore de lances, no painel ao lado do texto (SPEC 4.8)."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pgn_live_editor.core.normalizer import LiveTextNormalizer  # noqa: E402
from pgn_live_editor.core.outline import build_outline, flatten_outline  # noqa: E402
from pgn_live_editor.core.parser import TolerantParser  # noqa: E402
from pgn_live_editor.core.pgn_tree import (  # noqa: E402
    build_pgn_tree,
    count_moves,
    count_variations,
    find_move_at,
)
from pgn_live_editor.core.tokenizer import PGNTokenizer  # noqa: E402
from pgn_live_editor.ui.editor_panel import EditorPanel  # noqa: E402
from pgn_live_editor.ui.theme import color_for_depth  # noqa: E402

_app = QApplication.instance() or QApplication([])

#: `2.Nc3` e alternativa a `2.Nf3`; `2...e6` a `2...Nc6`; `3.d4` a `3.Bb5`.
BOOK = "1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 ( 2...e6 3.g3 ) 3.Bb5 ) 2...d6 3.d4 ( 3.Bb5+ Bd7 )"


def parse(text: str):
    normalized = LiveTextNormalizer(locale="en").normalize_with_mapping(text)
    return TolerantParser(locale="en").parse(PGNTokenizer().tokenize(normalized))


def labels_of(items) -> list[str]:
    return [item.label for item in items]


class TreeShapeTests(unittest.TestCase):
    def setUp(self):
        self.items = build_pgn_tree(parse(BOOK))

    def test_the_main_line_is_the_top_level(self):
        """Com numero em todo lance: aqui cada um e uma linha da árvore, e
        `c5` sozinho numa linha nao diz onde esta."""
        self.assertEqual(["1. e4", "1... c5", "2. Nf3", "2... d6", "3. d4"], labels_of(self.items))

    def test_a_variation_hangs_from_the_move_it_replaces(self):
        """Nao do lance de onde a posicao parte. A AST guarda `7.Qd2 ( 7.Be2 )`
        no lance **anterior** a `7.Qd2`; quem abre a arvore atras da
        alternativa a `2.Nf3` procura debaixo de `2.Nf3`."""
        by_label = {item.label: item for item in self.items}
        self.assertEqual(["B"], [v.label for v in by_label["2. Nf3"].variations])
        self.assertEqual(["C"], [v.label for v in by_label["3. d4"].variations])
        self.assertEqual([], by_label["1... c5"].variations)

    def test_a_variation_inside_a_variation_keeps_nesting(self):
        outer = next(v for item in self.items for v in item.variations if v.label == "B")
        inner = [v for move in outer.moves for v in move.variations]

        self.assertEqual(["B2"], [v.label for v in inner])
        self.assertEqual("2... Nc6", next(m.label for m in outer.moves if m.variations))
        self.assertEqual(["2... e6", "3. g3"], labels_of(inner[0].moves))

    def test_the_labels_are_the_same_as_the_outline(self):
        """Duas arvores da mesma partida chamando a mesma variante por nomes
        diferentes seriam piores do que uma arvore so."""
        from_outline = [line.label for line in flatten_outline(build_outline(parse(BOOK))) if line.label]
        from_tree: list[str] = []

        def visit(items):
            for item in items:
                for variation in item.variations:
                    from_tree.append(variation.label)
                    visit(variation.moves)

        visit(self.items)
        self.assertEqual(sorted(from_outline), sorted(from_tree))

    def test_it_counts_moves_and_variations_of_the_whole_tree(self):
        self.assertEqual(3, count_variations(self.items))
        self.assertEqual(len(list(_all_moves(self.items))), count_moves(self.items))

    def test_the_node_id_matches_the_one_the_preview_uses(self):
        from pgn_live_editor.core.move_tree import build_move_entries

        entries = {entry.node_id for entry in build_move_entries(parse(BOOK))}
        self.assertTrue({item.node_id for item in _all_moves(self.items)} <= entries)

    def test_an_empty_game_gives_an_empty_tree(self):
        self.assertEqual([], build_pgn_tree(parse("")))

    def test_the_evaluation_and_the_comment_travel_with_the_move(self):
        items = build_pgn_tree(parse("1.e4 {abertura de rei} c5! 2.Nf3"))
        by_label = {item.label: item for item in items}

        self.assertEqual("abertura de rei", by_label["1. e4"].comment)
        self.assertEqual("!", by_label["1... c5"].nags)
        self.assertEqual("1... c5 !", by_label["1... c5"].title)


class CursorSyncTests(unittest.TestCase):
    def test_the_position_of_the_cursor_finds_the_move(self):
        items = build_pgn_tree(parse(BOOK))

        found = find_move_at(items, BOOK.index("2.Nc3") + 1)
        self.assertIsNotNone(found)
        self.assertEqual("2. Nc3", found.label)

    def test_the_deepest_move_wins(self):
        items = build_pgn_tree(parse(BOOK))

        found = find_move_at(items, BOOK.index("3.g3") + 1)
        self.assertEqual("3. g3", found.label)
        self.assertEqual(2, found.depth)

    def test_a_position_outside_any_move_finds_nothing(self):
        self.assertIsNone(find_move_at(build_pgn_tree(parse(BOOK)), 10_000))


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)
        self.panel.configure_recognition("en", {}, [])
        self.panel.load_document_text(BOOK)
        self.tree = self.panel.pgn_tree

    def test_the_tree_lives_inside_the_editor(self):
        self.assertIs(self.panel, self.tree.parentWidget().parentWidget())
        self.assertTrue(self.tree.isVisibleTo(self.panel))

    def test_it_shows_the_moves_of_the_text_in_the_editor(self):
        self.assertEqual(5, self.tree.tree.topLevelItemCount())
        self.assertIn("3 variante(s)", self.tree.lbl_summary.text())

    def test_each_level_keeps_the_colour_of_the_editor(self):
        root = self.tree.tree.topLevelItem(2)  # 2. Nf3
        variation = root.child(0)

        self.assertEqual(color_for_depth(self.tree.theme, 0), root.foreground(0).color().name())
        self.assertEqual(color_for_depth(self.tree.theme, 1), variation.foreground(0).color().name())
        self.assertEqual(color_for_depth(self.tree.theme, 1), variation.child(0).foreground(0).color().name())

    def test_clicking_a_move_takes_the_cursor_to_it(self):
        variation = self.tree.tree.topLevelItem(2).child(0)
        move = variation.child(1)  # 2... Nc6

        self.tree._on_item_clicked(move, 0)

        cursor = self.panel.text_edit.textCursor()
        self.assertEqual("Nc6", self.panel.document_text()[cursor.selectionStart() : cursor.selectionEnd()])

    def test_moving_the_cursor_selects_the_move(self):
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(BOOK.index("3.g3") + 1)
        self.panel.text_edit.setTextCursor(cursor)

        self.assertEqual("3. g3", self.tree.tree.currentItem().text(0))

    def test_selecting_from_the_tree_does_not_bounce_back(self):
        """Clicar move o cursor, e mover o cursor seleciona -- os dois juntos
        dariam um laco se um pudesse disparar o outro."""
        self.tree._syncing = True
        received: list[tuple[int, int]] = []
        self.tree.move_selected_signal.connect(lambda start, end: received.append((start, end)))

        self.tree._on_item_clicked(self.tree.tree.topLevelItem(0), 0)

        self.assertEqual([], received)

    def test_it_can_be_hidden(self):
        self.assertFalse(self.panel.toggle_pgn_tree())
        self.assertFalse(self.tree.isVisibleTo(self.panel))
        self.assertTrue(self.panel.toggle_pgn_tree())

    def test_it_follows_the_theme(self):
        self.panel.set_theme("claro")
        root = self.tree.tree.topLevelItem(0)
        self.assertEqual(color_for_depth("claro", 0), root.foreground(0).color().name())


class TreeStateTests(unittest.TestCase):
    """A arvore nasce de novo a cada tecla, mas não se comporta como se nascesse.

    Reconstruir e mais barato do que descobrir o que mudou; uma arvore que
    voltasse ao estado inicial a cada reanalise recolheria a variante que se
    esta lendo no meio de uma frase.
    """

    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)
        self.panel.configure_recognition("en", {}, [])
        self.panel.load_document_text(BOOK)
        self.tree = self.panel.pgn_tree

    def variation_of(self, label: str):
        return next(item for item, _level in self.tree._iter_items() if item.text(0).startswith(f"{label})"))

    def retype(self, text: str):
        self.panel.text_edit.setPlainText(text)
        self.panel.reparse_now()

    def test_what_the_user_opened_survives_the_next_keystroke(self):
        """Uma sub-variante nasce fechada: abri-la e uma escolha, e nao pode
        custar uma escolha por tecla digitada."""
        self.variation_of("B2").setExpanded(True)

        self.retype(f"{BOOK} cxd4")

        self.assertTrue(self.variation_of("B2").isExpanded())

    def test_what_the_user_closed_stays_closed(self):
        self.variation_of("B").setExpanded(False)

        self.retype(f"{BOOK} cxd4")

        self.assertFalse(self.variation_of("B").isExpanded())

    def test_a_branch_that_was_just_typed_opens_by_the_default_rule(self):
        """Ela nao tem estado guardado -- e nao ter estado nao e o mesmo que
        ter sido fechada."""
        self.tree._expand_to(-1)  # tudo recolhido

        self.retype(f"{BOOK} cxd4 4.Nxd4 ( 4.Qxd4 Nc6 )")

        self.assertTrue(self.variation_of("D").isExpanded())
        self.assertFalse(self.variation_of("B").isExpanded())

    def test_the_selected_move_survives_the_typing_of_prose(self):
        """Digitar prosa nao desfaz a escolha do lance. O cursor nao esta em
        lance nenhum, e nesse caso a arvore nao tem por que trocar de linha --
        e e justamente com um lance escolhido que se vai buscar o parágrafo."""
        self.panel.load_document_text(f"{BOOK} prosa solta do autor")
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(len(self.panel.document_text()))
        self.panel.text_edit.setTextCursor(cursor)
        self.tree.tree.setCurrentItem(self.tree.tree.topLevelItem(2))  # 2. Nf3

        self.panel.text_edit.textCursor().insertText(" e mais um pouco")
        self.panel.reparse_now()

        self.assertEqual("2. Nf3", self.tree.tree.currentItem().text(0))


class CommentColumnTests(unittest.TestCase):
    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)
        self.panel.configure_recognition("en", {}, [])
        self.tree = self.panel.pgn_tree

    def test_the_column_shows_the_comment_and_the_tip_says_on_which_side(self):
        self.panel.load_document_text("{introdução do capítulo} 1.e4 {abertura de rei} c5")
        first, second = self.tree.tree.topLevelItem(0), self.tree.tree.topLevelItem(1)

        self.assertEqual("introdução do capítulo abertura de rei", first.text(1))
        self.assertEqual(
            "Antes do lance: introdução do capítulo\nDepois do lance: abertura de rei",
            first.toolTip(1),
        )
        self.assertEqual("", second.text(1))
        self.assertEqual("", second.toolTip(1))

    def test_a_collapsed_variation_shows_the_prose_it_opens_with(self):
        self.panel.load_document_text("1.e4 c5 2.Nf3 ( {a alternativa do livro} 2.Nc3 Nc6 )")
        variation = self.tree.tree.topLevelItem(2).child(0)

        self.assertEqual("a alternativa do livro", variation.text(1))


class CommentPasteTests(unittest.TestCase):
    """Colar a prosa da area de transferencia no lance escolhido na arvore."""

    def setUp(self):
        self.panel = EditorPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)
        self.panel.configure_recognition("en", {}, [])
        self.panel.load_document_text("1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 ) d6")
        self.tree = self.panel.pgn_tree

    def item(self, label: str):
        return next(item for item, _level in self.tree._iter_items() if item.text(0) == label)

    def test_the_tree_paste_reaches_the_text(self):
        QGuiApplication.clipboard().setText("o lance de sempre")

        self.tree._request_comment(before=False, item=self.item("1. e4"))

        self.assertIn("1.e4 {o lance de sempre} c5", self.panel.document_text())

    def test_pasting_before_a_move_lands_after_the_variation_that_precedes_it(self):
        """O que separa os dois lados do gesto: `2...d6` vem depois de um
        parentese, e o comentario que fala dele não pode cair dentro dele."""
        QGuiApplication.clipboard().setText("a continuação principal")

        self.tree._request_comment(before=True, item=self.item("2... d6"))

        self.assertIn(") {a continuação principal} d6", self.panel.document_text())

    def test_the_head_of_a_variation_means_its_first_move(self):
        """ "Antes desta variante" e antes do primeiro lance dela."""
        head = self.item("B) 2. Nc3 Nc6")

        self.assertEqual(self.tree.selected_node_id(self.item("2. Nc3")), self.tree.selected_node_id(head))

    def test_pasting_on_the_head_of_a_variation_lands_inside_it(self):
        QGuiApplication.clipboard().setText("a alternativa do livro")

        self.tree._request_comment(before=True, item=self.item("B) 2. Nc3 Nc6"))

        self.assertIn("( {a alternativa do livro} 2.Nc3", self.panel.document_text())

    def test_the_panel_tells_the_window_what_happened(self):
        QGuiApplication.clipboard().setText("prosa")
        messages: list[str] = []
        self.panel.status_message_signal.connect(messages.append)

        self.tree._request_comment(before=False, item=self.item("1. e4"))

        self.assertEqual(["Comentário colado depois de 1. e4."], messages)

    def test_without_a_chosen_move_the_gesture_explains_itself(self):
        QGuiApplication.clipboard().setText("prosa")
        self.panel.load_document_text("um parágrafo sem lance nenhum")

        message = self.panel.action_paste_comment(before=False)

        self.assertIn("Escolha o lance", message)
        self.assertEqual("um parágrafo sem lance nenhum", self.panel.document_text())

    def test_the_move_under_the_cursor_is_the_default_target(self):
        cursor = self.panel.text_edit.textCursor()
        cursor.setPosition(self.panel.document_text().index("d6") + 1)
        self.panel.text_edit.setTextCursor(cursor)

        self.panel.paste_comment_on_move("sobre d6", before=False)

        self.assertIn("d6 {sobre d6}", self.panel.document_text())

    def test_the_gesture_is_a_single_undo(self):
        self.panel.paste_comment_on_move("prosa", before=False, node_id=self.tree.selected_node_id(self.item("1. e4")))
        before_undo = self.panel.document_text()

        self.panel.text_edit.undo()

        self.assertIn("{prosa}", before_undo)
        self.assertEqual("1.e4 c5 2.Nf3 ( 2.Nc3 Nc6 ) d6", self.panel.document_text())


def _all_moves(items):
    for item in items:
        yield item
        for variation in item.variations:
            yield from _all_moves(variation.moves)


if __name__ == "__main__":
    unittest.main()
