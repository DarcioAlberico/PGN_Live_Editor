"""Gravacao duravel: falhar no meio nunca deixa o usuario sem arquivo (S-02 / R-101).

A rotacao antiga renomeava o projeto para `.bak` **antes** de por o novo no
lugar. Se a troca final falhasse (antivirus, OneDrive, arquivo aberto em outro
programa), nao sobrava projeto nenhum no caminho -- e o backup mais antigo ja
tinha sido descartado pela rotacao.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.services import durable_io
from pgn_live_editor.services.durable_io import DurableWriteError, write_durable, write_durable_text


class DurableWriteTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="durable-"))
        self.path = self.directory / "livro.plez"

    def files(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(self.directory.iterdir())}

    def failing_final_replace(self, error: OSError, times: int | None = None):
        """`os.replace` falha so na troca final (temporario -> destino)."""
        real_replace = os.replace
        calls = {"n": 0}

        def replace(source, target):
            if Path(target) == self.path and Path(source).name.endswith(".tmp"):
                calls["n"] += 1
                if times is None or calls["n"] <= times:
                    raise error
            return real_replace(source, target)

        return mock.patch.object(durable_io.os, "replace", side_effect=replace)

    def test_a_new_file_is_written(self):
        write_durable_text(self.path, "versao 1")

        self.assertEqual({"livro.plez": "versao 1"}, self.files())

    def test_each_save_pushes_the_previous_versions_down_the_backups(self):
        for version in range(1, 6):
            write_durable_text(self.path, f"versao {version}", backups=3)

        self.assertEqual(
            {
                "livro.plez": "versao 5",
                "livro.plez.bak": "versao 4",
                "livro.plez.bak.1": "versao 3",
                "livro.plez.bak.2": "versao 2",
            },
            self.files(),
        )

    def test_a_failed_final_swap_leaves_the_project_and_the_backups_as_they_were(self):
        write_durable_text(self.path, "versao 1", backups=3)
        write_durable_text(self.path, "versao 2", backups=3)
        before = self.files()

        with self.failing_final_replace(OSError("disco cheio")), self.assertRaises(DurableWriteError):
            write_durable_text(self.path, "versao 3", backups=3)

        self.assertEqual(before, self.files())

    def test_a_file_locked_by_another_program_fails_clearly_and_rotates_nothing(self):
        write_durable_text(self.path, "versao 1", backups=3)
        write_durable_text(self.path, "versao 2", backups=3)
        before = self.files()

        with (
            self.failing_final_replace(PermissionError(13, "arquivo em uso")),
            mock.patch.object(durable_io.time, "sleep"),
            self.assertRaises(DurableWriteError) as raised,
        ):
            write_durable_text(self.path, "versao 3", backups=3)

        self.assertIn("livro.plez", str(raised.exception))
        self.assertEqual(before, self.files())

    def test_a_short_lock_is_waited_out(self):
        write_durable_text(self.path, "versao 1", backups=3)

        with (
            self.failing_final_replace(PermissionError(13, "indexador"), times=2),
            mock.patch.object(durable_io.time, "sleep"),
        ):
            write_durable_text(self.path, "versao 2", backups=3)

        self.assertEqual({"livro.plez": "versao 2", "livro.plez.bak": "versao 1"}, self.files())

    def test_saving_the_same_content_twice_gives_the_same_bytes(self):
        write_durable(self.path, b"\x00\x01dados", backups=0)
        first = self.path.read_bytes()
        write_durable(self.path, b"\x00\x01dados", backups=0)

        self.assertEqual(first, self.path.read_bytes())
        self.assertEqual(["livro.plez"], [p.name for p in self.directory.iterdir()])

    def test_text_is_utf8_with_unix_newlines(self):
        write_durable_text(self.path, "Sem título\nlinha 2\n")

        self.assertEqual("Sem título\nlinha 2\n".encode(), self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
