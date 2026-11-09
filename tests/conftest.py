"""Isolamento do ambiente para a suite inteira.

Tres pontos, e os tres foram descobertos por falha real:

**Diretorio de dados.** Sem o desvio, os testes leem `app.db` da maquina
de quem roda: preferencias, dicionario global, caminho do motor, arquivos
recentes. Um `locale` gravado num dia derruba um teste no outro, e derruba **so
ali** -- o CI, que roda num runner limpo, continua verde e o defeito parece
coisa do desenvolvedor. Pior ainda, os testes que gravam preferencia estariam
mexendo no perfil de verdade do usuario. O desvio mora em `_isolation.py`,
importado tambem por cada arquivo de teste, para valer fora do pytest.

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

import sys
from pathlib import Path

# `tests/` nao e pacote: os arquivos de teste se importam pelo nome
# (`from pdf_fixture import ...`), e o pytest so poe a pasta no `sys.path`
# quando o `rootdir` coincide. Explicito e mais barato que descobrir de novo.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _isolation  # noqa: E402, F401 -- antes do Qt e do programa
import pytest  # noqa: E402
from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


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
