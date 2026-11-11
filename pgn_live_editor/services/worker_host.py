"""Um encanamento so para todo trabalho em segundo plano (SPEC_V3 §4.3, R-303).

Analise, motor, OCR de diagrama, OCR do PDF inteiro e OCR de pagina tinham
cada um a sua copia de thread + fila + id de pedido -- e cada copia com os seus
defeitos: `deleteLater` numa thread viva ao fechar (S-08), cancelar sem matar
o subprocesso e conclusao velha zerando `busy` (S-09), excecao no trabalho
travando a fila (S-03). O contrato fica aqui, uma vez:

- uma thread por host, criada no primeiro pedido;
- no maximo um pedido rodando e um esperando; o novo substitui o que esperava
  e pede ao que esta rodando que pare (`context.cancelled`);
- so o resultado do pedido **corrente** e entregue, e so ele zera `busy`;
- `cancel()` avisa o trabalho; um subprocesso iniciado por `run_process` e
  terminado (e morto 2 s depois, se preciso);
- excecao do trabalho vira `WorkerFailed`, e o host continua aceitando pedidos;
- `shutdown()` nunca chama `deleteLater` numa thread que ainda roda.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import traceback as traceback_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, QThread
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtCore import pyqtSlot as Slot

logger = logging.getLogger(__name__)

#: Quanto do stderr do filho entra na mensagem de erro.
STDERR_TAIL_BYTES = 4096
KILL_AFTER_S = 2.0


@dataclass(frozen=True)
class WorkerFailed:
    request_id: int
    message: str
    traceback: str = ""


class CancelToken:
    """Seguro entre threads: a UI cancela, o trabalho consulta ou e avisado."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            _call_quietly(callback)

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Chama `callback` no cancelamento -- ou ja, se ele ja aconteceu."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(callback)
                return
        _call_quietly(callback)


def _call_quietly(callback: Callable[[], None]) -> None:
    try:
        callback()
    except Exception:  # noqa: BLE001 - cancelar nunca pode derrubar quem cancela
        logger.exception("Falha ao cancelar trabalho")


class JobContext:
    """O que o trabalho recebe alem do pedido: cancelamento e progresso."""

    def __init__(self, request_id: int, token: CancelToken, progress: Callable[[object], None]):
        self.request_id = request_id
        self._token = token
        self._progress = progress

    @property
    def cancelled(self) -> bool:
        return self._token.cancelled

    def on_cancel(self, callback: Callable[[], None]) -> None:
        self._token.on_cancel(callback)

    def progress(self, value: object) -> None:
        self._progress(value)


Job = Callable[[Any, JobContext], Any]


class _Runner(QObject):
    """Vive na thread de trabalho. Nunca toca em widget."""

    done_signal = Signal(int, object, object)
    progress_signal = Signal(int, object)

    def __init__(self, job: Job):
        super().__init__()
        self._job = job

    @Slot(int, object, object)
    def run(self, request_id: int, payload: object, token: CancelToken) -> None:
        context = JobContext(request_id, token, lambda value: self.progress_signal.emit(request_id, value))
        try:
            result = self._job(payload, context)
        except Exception as exc:  # noqa: BLE001 - vira WorkerFailed; a fila nunca fica presa
            logger.exception("Trabalho em segundo plano falhou")
            failure = WorkerFailed(request_id, str(exc) or type(exc).__name__, traceback_module.format_exc())
            self.done_signal.emit(request_id, None, failure)
            return
        self.done_signal.emit(request_id, result, None)


class WorkerHost(QObject):
    finished_signal = Signal(int, object)  # request_id, resultado
    failed_signal = Signal(object)  # WorkerFailed
    progress_signal = Signal(int, object)  # request_id, valor

    _request_signal = Signal(int, object, object)

    # Threads que nao terminaram a tempo no `shutdown`: mantidas vivas aqui,
    # nunca apagadas rodando. `drain_abandoned` espera por elas no fim.
    _abandoned: list[QThread] = []

    def __init__(self, job: Job, parent: QObject | None = None):
        super().__init__(parent)
        self._job = job
        self._thread: QThread | None = None
        self._runner: _Runner | None = None
        self._last_id = 0
        self._running: tuple[int, CancelToken] | None = None
        self._pending: tuple[int, object] | None = None
        self._current: int | None = None

    @property
    def busy(self) -> bool:
        return self._current is not None

    @property
    def current_request_id(self) -> int | None:
        return self._current

    def submit(self, payload: object) -> int:
        """Pede um trabalho; devolve o id do pedido, que volta no resultado."""
        self._last_id += 1
        request_id = self._last_id
        self._current = request_id
        if self._running is None:
            self._dispatch(request_id, payload)
        else:
            self._pending = (request_id, payload)
            self._running[1].cancel()
        return request_id

    def cancel(self) -> None:
        """Nenhum resultado dos pedidos feitos ate aqui sera entregue."""
        self._pending = None
        self._current = None
        if self._running is not None:
            self._running[1].cancel()

    def _dispatch(self, request_id: int, payload: object) -> None:
        token = CancelToken()
        self._running = (request_id, token)
        self._ensure_thread()
        self._request_signal.emit(request_id, payload, token)

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread()
        self._runner = _Runner(self._job)
        self._runner.moveToThread(self._thread)
        self._request_signal.connect(self._runner.run)
        self._runner.done_signal.connect(self._on_done)
        self._runner.progress_signal.connect(self._on_progress)
        # O trabalhador tem de morrer na thread dele: os DeferredDelete daquela
        # thread sao entregues quando o laco de eventos dela termina.
        self._thread.finished.connect(self._runner.deleteLater)
        self._thread.start()

    @Slot(int, object, object)
    def _on_done(self, request_id: int, result: object, failure: object) -> None:
        if self._running is not None and self._running[0] == request_id:
            self._running = None
        if request_id == self._current:
            self._current = None
            if isinstance(failure, WorkerFailed):
                self.failed_signal.emit(failure)
            else:
                self.finished_signal.emit(request_id, result)
        if self._running is None and self._pending is not None:
            pending_id, payload = self._pending
            self._pending = None
            self._dispatch(pending_id, payload)

    @Slot(int, object)
    def _on_progress(self, request_id: int, value: object) -> None:
        if request_id == self._current:
            self.progress_signal.emit(request_id, value)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Cancela e encerra a thread. `False` se ela nao terminou a tempo (fica guardada)."""
        self.cancel()
        thread = self._thread
        self._thread, self._runner, self._running = None, None, None
        if thread is None:
            return True
        thread.quit()
        if thread.wait(timeout_ms):
            thread.deleteLater()
            return True
        logger.warning("Thread de trabalho nao terminou em %d ms; mantida ate o fim do programa", timeout_ms)
        WorkerHost._abandoned.append(thread)
        return False

    @classmethod
    def drain_abandoned(cls, timeout_ms: int = 5000) -> bool:
        """Espera as threads abandonadas no `shutdown`. `False` se alguma ainda roda."""
        still_running = [thread for thread in cls._abandoned if not thread.wait(timeout_ms)]
        cls._abandoned = still_running
        return not still_running


def run_process(
    command: Sequence[str],
    context: Any,
    *,
    timeout: float | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Roda um subprocesso que o cancelamento consegue parar.

    `stdout` vai para DEVNULL -- os workers falam por arquivo de resultado, e
    um PIPE que ninguem le trava o filho (S-10). `stderr` e lido linha a linha
    (para progresso) e so o fim (4 KB) e guardado para a mensagem de erro.
    Retorno diferente de zero levanta `CalledProcessError` com esse fim.
    """
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )

    def stop() -> None:
        if process.poll() is not None:
            return
        process.terminate()

        def kill_if_alive() -> None:
            if process.poll() is None:
                process.kill()

        killer = threading.Timer(KILL_AFTER_S, kill_if_alive)
        killer.daemon = True
        killer.start()

    context.on_cancel(stop)
    timer = threading.Timer(timeout, stop) if timeout else None
    if timer is not None:
        timer.daemon = True
        timer.start()
    tail = ""
    try:
        assert process.stderr is not None
        for line in process.stderr:
            if on_stderr_line is not None:
                on_stderr_line(line)
            tail = (tail + line)[-STDERR_TAIL_BYTES:]
        return_code = process.wait()
    finally:
        if timer is not None:
            timer.cancel()
    tail = tail.strip()
    if return_code != 0 and not context.cancelled:
        raise subprocess.CalledProcessError(return_code, list(command), stderr=tail[-STDERR_TAIL_BYTES:])
    return subprocess.CompletedProcess(list(command), return_code, None, tail)
