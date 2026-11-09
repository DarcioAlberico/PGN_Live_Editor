"""Isolamento do ambiente para a suite inteira.

Tres pontos, e os tres foram descobertos por falha real:

**Diretorio de dados.** Sem o desvio abaixo, os testes leem `app.db` da maquina
de quem roda: preferencias, dicionario global, caminho do motor, arquivos
recentes. Um `locale` gravado num dia derruba um teste no outro, e derruba **so
ali** -- o CI, que roda num runner limpo, continua verde e o defeito parece
coisa do desenvolvedor. Pior ainda, os testes que gravam preferencia estariam
mexendo no perfil de verdade do usuario.

**Plataforma do Qt.** `offscreen` antes de qualquer `import` de PyQt6, para que
a suite rode sem tela. Os arquivos de interface tambem o fazem por conta
propria, para continuarem executaveis por `python -m unittest`.

**Destruicao das janelas.** `deleteLater` nao destroi nada: ele **agenda** a
destruicao para a proxima volta do laco de eventos. Um teste de interface que
nunca gira o laco termina deixando a janela viva, e a suite vai acumulando
janelas mortas-vivas ate que algum teste adiante chame `QTest.qWait` -- e ai
uma duzia delas e destruida de uma vez, no meio de outro teste, com os timers
e as threads de todas elas disparando juntos. O processo morria com corrupcao
de heap (`0xc0000374`), sem excecao e sem traceback, num teste que nao tinha
nada a ver com a causa.

Purgar a fila depois de **cada** teste faz cada janela morrer dentro do teste
que a criou, que e onde ela devia ter morrido.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.services.project_service import DATA_DIR_ENV_VAR

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# `tests/` nao e pacote: os arquivos de teste se importam pelo nome
# (`from pdf_fixture import ...`), e o pytest so poe a pasta no `sys.path`
# quando o `rootdir` coincide. Explicito e mais barato que descobrir de novo.
sys.path.insert(0, str(Path(__file__).resolve().parent))

_data_dir = tempfile.TemporaryDirectory(prefix="pgn-live-editor-tests-")
os.environ[DATA_DIR_ENV_VAR] = _data_dir.name


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    _data_dir.cleanup()


@pytest.fixture(autouse=True)
def flush_qt_deletions():
    """Executa os `deleteLater` pendentes no fim de cada teste."""
    yield
    application = QApplication.instance()
    if application is None:
        return
    # Duas voltas: a primeira destroi os objetos agendados, a segunda entrega
    # o que a destruicao deles agendou por sua vez (um filho que pede a propria
    # remocao no destrutor do pai, por exemplo).
    for _ in range(2):
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
