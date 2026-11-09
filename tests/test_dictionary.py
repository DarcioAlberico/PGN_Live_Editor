"""Fase 5: o dicionario de OCR com escopo e camadas (SPEC 5.2)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401
import chess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pgn_live_editor.core.candidates import CandidateResolver  # noqa: E402
from pgn_live_editor.core.normalizer import LiveTextNormalizer  # noqa: E402
from pgn_live_editor.core.parser import TolerantParser  # noqa: E402
from pgn_live_editor.core.substitutions import (  # noqa: E402
    LAYER_GLOBAL,
    LAYER_PROJECT,
    SCOPE_FREE,
    SCOPE_PREFIX,
    SCOPE_TOKEN,
    Dictionary,
    Substitution,
    merge_layers,
)
from pgn_live_editor.core.tokenizer import PGNTokenizer  # noqa: E402
from pgn_live_editor.services.dictionary_service import (  # noqa: E402
    LEARN_AFTER_ACCEPTANCES,
    DictionaryService,
)


def parse(text: str, dictionary: Dictionary | None = None, locale: str = "pt"):
    normalized = LiveTextNormalizer(locale=locale).normalize_with_mapping(text)
    tokens = PGNTokenizer().tokenize(normalized)
    return TolerantParser(locale=locale, dictionary=dictionary).parse(tokens)


class ScopeTests(unittest.TestCase):
    """O escopo diz **onde dentro do token** a regra pega."""

    def test_token_scope_matches_the_whole_token_only(self):
        entry = Substitution(pattern="Bh5", replacement="Bb5", scope=SCOPE_TOKEN)

        self.assertEqual("Bb5", entry.apply("Bh5"))
        self.assertIsNone(entry.apply("Bh5+"))
        self.assertIsNone(entry.apply("xBh5"))

    def test_prefix_scope_matches_the_start_and_keeps_the_rest(self):
        entry = Substitution(pattern="4J", replacement="N", scope=SCOPE_PREFIX)

        self.assertEqual("Nf3", entry.apply("4Jf3"))
        self.assertIsNone(entry.apply("x4Jf3"))
        # Prefixo sozinho nao vira lance: sobraria so a letra da peca.
        self.assertIsNone(entry.apply("4J"))

    def test_free_scope_matches_anywhere(self):
        entry = Substitution(pattern="1", replacement="l", scope=SCOPE_FREE)

        self.assertEqual("Bl5", entry.apply("B15"))
        self.assertIsNone(entry.apply("Bb5"))


class LayerTests(unittest.TestCase):
    """Embutido -> meu -> deste projeto; a mais especifica vence."""

    def test_the_most_specific_layer_wins(self):
        base = Dictionary([Substitution(pattern="tD", replacement="N", scope=SCOPE_PREFIX)])
        user = Dictionary([Substitution(pattern="tD", replacement="R", scope=SCOPE_PREFIX)])
        project = Dictionary([Substitution(pattern="tD", replacement="Q", scope=SCOPE_PREFIX)])

        merged = merge_layers(base, user, project)

        self.assertEqual(1, len(merged))
        self.assertEqual({"tD": "Q"}, merged.prefix_map("pt"))

    def test_the_same_pattern_in_two_scopes_are_two_rules(self):
        merged = merge_layers(
            Dictionary([Substitution(pattern="Cf3", replacement="Nf3", scope=SCOPE_TOKEN)]),
            Dictionary([Substitution(pattern="Cf3", replacement="Nf3!", scope=SCOPE_PREFIX)]),
        )

        self.assertEqual(2, len(merged))

    def test_a_rule_of_another_locale_is_not_consulted(self):
        dictionary = Dictionary([Substitution(pattern="Sf3", replacement="Nf3", locale="de")])

        self.assertEqual({}, dictionary.token_map("pt"))
        self.assertEqual({"Sf3": "Nf3"}, dictionary.token_map("de"))

    def test_the_config_form_survives_a_round_trip(self):
        """E o que atravessa a fronteira de thread e entra na chave do cache."""
        dictionary = Dictionary(
            [
                Substitution(pattern="tD", replacement="N", scope=SCOPE_PREFIX, locale="pt"),
                Substitution(pattern="Bh5", replacement="Bb5", scope=SCOPE_TOKEN, locale="pt"),
            ]
        )

        restored = Dictionary.from_config(dictionary.as_config())

        self.assertEqual(dictionary.prefix_map("pt"), restored.prefix_map("pt"))
        self.assertEqual(dictionary.token_map("pt"), restored.token_map("pt"))


class ResolverIntegrationTests(unittest.TestCase):
    def test_a_learned_token_is_applied_without_asking_again(self):
        dictionary = Dictionary([Substitution(pattern="Bh5", replacement="Bb5", locale="pt")])

        ast = parse("1.e4 e5 2.Nf3 Nc6 3.Bh5", dictionary)

        self.assertEqual("Bb5", ast.moves[-1].display_san)
        self.assertFalse(ast.moves[-1].needs_review)

    def test_a_free_substitution_is_tried_but_still_needs_a_legal_move(self):
        dictionary = Dictionary([Substitution(pattern="1", replacement="b", scope=SCOPE_FREE, locale="pt")])

        ast = parse("1.e4 e5 2.Nf3 Nc6 3.B15", dictionary)

        self.assertEqual("Bb5", ast.moves[-1].display_san)

    def test_a_substitution_that_gives_an_illegal_move_is_refused(self):
        dictionary = Dictionary([Substitution(pattern="Bh5", replacement="Qa8", locale="pt")])

        ast = parse("1.e4 e5 2.Nf3 Nc6 3.Bh5", dictionary)

        self.assertNotEqual("Qa8", ast.moves[-1].display_san)

    def test_learning_writes_the_narrowest_scope(self):
        resolver = CandidateResolver(locale="pt")
        resolver.learn("4)f3", "Nf3")

        entry = list(resolver.dictionary)[0]
        self.assertEqual(SCOPE_TOKEN, entry.scope)
        self.assertEqual("pt", entry.locale)
        self.assertEqual("Nf3", resolver.dictionary.token_map("pt")["4)f3"])

    def test_prose_still_survives_a_dictionary_full_of_rules(self):
        dictionary = Dictionary(
            [
                Substitution(pattern="C", replacement="N", scope=SCOPE_FREE, locale="pt"),
                Substitution(pattern="ch", replacement="+", scope=SCOPE_FREE, locale="pt"),
            ]
        )

        ast = parse("1.e4 e5 Cada lance conta e as chances crescem 2.Nf3", dictionary)

        self.assertEqual(["e4", "e5", "Nf3"], [move.display_san for move in ast.moves])
        self.assertIn("Cada lance conta e as chances crescem", " ".join(ast.moves[1].comments_after))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch(
            "pgn_live_editor.services.dictionary_service.app_data_dir",
            return_value=Path(self._temp.name),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = DictionaryService(locale="pt")

    def test_the_embedded_layer_is_there_from_the_start(self):
        self.assertIn("4J", self.service.base.prefix_map("pt"))
        self.assertEqual(0, len(self.service.project))

    def test_a_global_rule_survives_a_new_service(self):
        self.service.learn("4)", "Q", SCOPE_PREFIX, LAYER_GLOBAL)

        self.assertEqual("Q", DictionaryService(locale="pt").user.prefix_map("pt")["4)"])

    def test_a_project_rule_does_not_leak_into_the_global_layer(self):
        self.service.learn("Bh5", "Bb5", SCOPE_TOKEN, LAYER_PROJECT)

        self.assertEqual({}, DictionaryService(locale="pt").user.token_map("pt"))
        self.assertEqual({"Bh5": "Bb5"}, self.service.merged().token_map("pt"))

    def test_promoting_a_rule_moves_it_between_layers(self):
        entry = self.service.learn("Bh5", "Bb5", SCOPE_TOKEN, LAYER_PROJECT)

        self.service.move_to_global(entry.key)

        self.assertEqual({}, self.service.project.token_map("pt"))
        self.assertEqual({"Bh5": "Bb5"}, self.service.user.token_map("pt"))
        self.assertEqual({"Bh5": "Bb5"}, DictionaryService(locale="pt").user.token_map("pt"))

    def test_forgetting_a_project_rule_uncovers_the_embedded_one(self):
        entry = self.service.learn("4J", "Q", SCOPE_PREFIX, LAYER_PROJECT)
        self.assertEqual("Q", self.service.merged().prefix_map("pt")["4J"])

        self.service.forget(entry.key, LAYER_PROJECT)

        self.assertEqual("N", self.service.merged().prefix_map("pt")["4J"])

    def test_the_third_acceptance_is_the_one_that_offers_to_learn(self):
        counters: dict[str, int] = {}
        offers = []
        for _ in range(4):
            times = DictionaryService.register_acceptance(counters, "Bh5", "Bb5")
            offers.append(DictionaryService.should_offer_to_learn(times))

        self.assertEqual([False, False, True, False], offers)
        self.assertEqual(LEARN_AFTER_ACCEPTANCES, 3)

    def test_different_corrections_are_counted_apart(self):
        counters: dict[str, int] = {}
        DictionaryService.register_acceptance(counters, "Bh5", "Bb5")
        DictionaryService.register_acceptance(counters, "Bh5", "Bc4")

        self.assertEqual(2, len(counters))

    def test_the_live_preview_says_where_each_rule_touches_the_text(self):
        self.service.learn("Bh5", "Bb5", SCOPE_TOKEN, LAYER_PROJECT)
        self.service.learn("5", "4", SCOPE_FREE, LAYER_PROJECT)

        uses = self.service.preview("1.e4 e5 2.Nf3 Nc6 3.Bh5 Bh5 B15")

        self.assertEqual(2, uses[(SCOPE_TOKEN, "Bh5")].count)
        # A razao de existir a coluna "Alcance": a regra estreita pega dois
        # tokens, a livre pega o dobro -- e e isso que o dialogo mostra antes
        # de o usuario gravar.
        self.assertEqual(["e5", "Bh5", "Bh5", "B15"], uses[(SCOPE_FREE, "5")].tokens)

    def test_the_preview_never_counts_a_move_number(self):
        """`1.` e numero de lance, nao lance: dicionario nenhum chega ali."""
        self.service.learn("1", "l", SCOPE_FREE, LAYER_PROJECT)

        uses = self.service.preview("1.e4 e5 2.Nf3 B15")

        self.assertEqual(["B15"], uses[(SCOPE_FREE, "1")].tokens)

    def test_the_preview_of_an_empty_document_is_empty(self):
        self.service.learn("Bh5", "Bb5", SCOPE_TOKEN, LAYER_PROJECT)

        uses = self.service.preview("   ")

        self.assertEqual(0, uses[(SCOPE_TOKEN, "Bh5")].count)


class ChessSanityTests(unittest.TestCase):
    """Um lance so entra se for legal, venha de onde vier."""

    def test_the_dictionary_never_overrides_legality(self):
        board = chess.Board()
        resolver = CandidateResolver(
            locale="pt",
            dictionary=Dictionary([Substitution(pattern="xx", replacement="Qh8", locale="pt")]),
        )

        self.assertEqual([], resolver.resolve("xx", board))


if __name__ == "__main__":
    unittest.main()
