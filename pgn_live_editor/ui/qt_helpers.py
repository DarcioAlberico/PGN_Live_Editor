"""Auxiliares finos de Qt -- o que existe aqui existe por um defeito concreto."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer


def single_shot(owner: QObject, msec: int, slot: Callable[[], None]) -> QTimer:
    """Um disparo unico que **morre junto com `owner`**.

    `QTimer.singleShot` estatico nao tem dono: ele guarda o metodo ligado e
    dispara mesmo que o objeto ja tenha sido destruido do lado C++. Com um
    `QMainWindow` fechado no meio, isso nao levanta excecao -- corrompe a
    memoria do processo e derruba tudo, longe da causa. (O PyQt6 tambem nao
    oferece a sobrecarga com objeto de contexto que o Qt/C++ tem.)

    Um `QTimer` com pai e destruido com o pai, e o disparo simplesmente nao
    acontece, que e o que se queria.
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(slot)
    timer.start(msec)
    return timer
