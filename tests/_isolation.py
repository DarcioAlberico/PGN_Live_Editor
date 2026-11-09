"""Isolamento do ambiente de teste, valido para qualquer runner.

Todo arquivo de teste importa este modulo antes de qualquer coisa do
programa. Antes ele morava so no `conftest.py`, que o `unittest` nao carrega
(Q-08 da docs/AUDITORIA_2026-11.md): quem rodava `unittest discover` usava o
perfil de verdade do usuario.

Faz duas coisas, uma vez por processo:

- desvia o diretorio de dados (`PGN_LIVE_EDITOR_HOME`) para uma pasta
  temporaria, apagada ao sair -- preferencias, dicionario global, motor e
  recentes nunca sao os da maquina;
- poe o Qt em `offscreen`, para a suite rodar sem tela.

A purga das janelas depois de cada teste continua no `conftest.py`, porque
depende de fixture do pytest: o runner suportado e `uv run pytest`.
"""

from __future__ import annotations

import atexit
import os
import tempfile

# Mesmo nome de `services.project_service.DATA_DIR_ENV_VAR`; repetido aqui de
# proposito para nao importar o programa antes de isolar o ambiente.
DATA_DIR_ENV_VAR = "PGN_LIVE_EDITOR_HOME"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if not os.environ.get("_PGN_LIVE_EDITOR_TESTS_ISOLATED"):
    _data_dir = tempfile.TemporaryDirectory(prefix="pgn-live-editor-tests-")
    atexit.register(_data_dir.cleanup)
    os.environ[DATA_DIR_ENV_VAR] = _data_dir.name
    os.environ["_PGN_LIVE_EDITOR_TESTS_ISOLATED"] = "1"
