"""Decisoes humanas seguem o token pelo alinhamento, nao pela ocorrencia (R-302b).

L-04: a identidade do token era (tipo, grafia, ocorrencia). Inserir o mesmo
token antes deslocava **todas** as decisoes daquela grafia para o token errado.
"""

from __future__ import annotations

import time
import unittest

import _isolation  # noqa: F401

from pgn_live_editor.core.recognition_record import (
    ReviewAction,
    build_recognition_records,
    reconcile_with_orphans,
)

TEXT = "1. e4 Qz9 2. Nf3 Qz9 3. Bb5 Qz9"


def decided() -> list:
    """Qz9 #1 aceito como Qh5, #2 rejeitado, #3 sem decisao humana."""
    records = build_recognition_records(TEXT)
    seen = 0
    for index, record in enumerate(records):
        if record.raw_text != "Qz9":
            continue
        if seen == 0:
            records[index] = record.decide(ReviewAction.ACCEPTED, decision="Qh5", at=1.0)
        elif seen == 1:
            records[index] = record.decide(ReviewAction.REJECTED, at=2.0)
        seen += 1
    return records


def qz9_states(records) -> list[tuple[int, ReviewAction, str | None]]:
    return [(r.source.start, r.action, r.decision) for r in records if r.raw_text == "Qz9"]


class AlignmentTests(unittest.TestCase):
    def test_inserting_the_same_token_before_does_not_shift_decisions(self):
        new_text = "Qz9 " + TEXT

        current = reconcile_with_orphans(build_recognition_records(new_text), decided())[0]

        self.assertEqual(
            [
                (0, ReviewAction.PENDING, None),
                (new_text.index("Qz9", 4), ReviewAction.ACCEPTED, "Qh5"),
                (new_text.index("Qz9 3."), ReviewAction.REJECTED, None),
                (new_text.rindex("Qz9"), ReviewAction.PENDING, None),
            ],
            qz9_states(current),
        )

    def test_a_decision_goes_to_at_most_one_token(self):
        new_text = TEXT.replace("2. Nf3", "2. Nf3 Qz9 Qz9")

        current = reconcile_with_orphans(build_recognition_records(new_text), decided())[0]

        self.assertEqual(1, sum(1 for r in current if r.decision == "Qh5"))
        self.assertEqual(1, sum(1 for r in current if r.action is ReviewAction.REJECTED))

    def test_a_deleted_token_with_a_human_decision_is_an_orphan(self):
        new_text = TEXT.replace("2. Nf3 Qz9", "2. Nf3")

        current, orphans = reconcile_with_orphans(build_recognition_records(new_text), decided())

        self.assertEqual([ReviewAction.REJECTED], [o.action for o in orphans])
        self.assertNotIn(ReviewAction.REJECTED, [r.action for r in current])

    def test_tokens_without_a_human_decision_are_never_orphans(self):
        _, orphans = reconcile_with_orphans(build_recognition_records("1. d4"), build_recognition_records(TEXT))

        self.assertEqual([], orphans)


class BookScaleTests(unittest.TestCase):
    def test_replacing_a_token_everywhere_in_a_book_is_fast_and_keeps_decisions(self):
        # Com o `SequenceMatcher` sobre o livro inteiro, isto levava 20 s na thread da UI.
        book = " ".join(f"{n}. e4 Xq9 Nf3 {{nota}}" for n in range(1, 8000))
        decided_book = [
            r.decide(ReviewAction.IGNORED) if r.raw_text == "Nf3" else r for r in build_recognition_records(book)
        ]
        fresh = build_recognition_records(book.replace("Xq9", "Xq8"))

        started = time.perf_counter()
        current, orphans = reconcile_with_orphans(fresh, decided_book)
        elapsed = time.perf_counter() - started

        self.assertEqual(7999, sum(1 for r in current if r.action is ReviewAction.IGNORED))
        self.assertEqual([], orphans)
        self.assertLess(elapsed, 2.0, f"{elapsed:.1f} s")


if __name__ == "__main__":
    unittest.main()
