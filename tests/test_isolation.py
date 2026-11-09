"""A suite nunca toca o perfil de verdade do usuario -- com qualquer runner.

O desvio do diretorio de dados morava so no `conftest.py`, que o `unittest`
nao carrega; o README mandava rodar com `unittest discover`. Quem seguia o
README rodava os testes contra as preferencias, o dicionario global e os
recentes reais (Q-08 da docs/AUDITORIA_2026-11.md).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401

from pgn_live_editor.services.project_service import DATA_DIR_ENV_VAR, app_data_dir

TESTS_DIR = Path(__file__).resolve().parent


class IsolationTests(unittest.TestCase):
    def test_the_data_directory_is_a_temporary_one(self):
        self.assertTrue(os.environ.get(DATA_DIR_ENV_VAR), "o desvio do diretorio de dados nao esta ativo")
        data_dir = app_data_dir().resolve()
        self.assertTrue(
            data_dir.is_relative_to(Path(tempfile.gettempdir()).resolve()),
            f"os testes estao usando {data_dir}, fora da pasta temporaria",
        )

    def test_every_test_module_asks_for_isolation(self):
        # Um arquivo de teste novo que esqueca o import roda sem desvio quando
        # executado sozinho pelo `unittest`.
        missing = [
            path.name
            for path in sorted(TESTS_DIR.glob("test_*.py"))
            if "import _isolation" not in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
