"""Um encanamento so para todo trabalho em segundo plano (R-303, SPEC_V3 §4.3).

S-08: `shutdown()` esperava 3-5 s e chamava `deleteLater` numa thread ainda
rodando (crash ao fechar no meio de uma varredura); varredura nao cancelavel,
4 pedidos = 4 varreduras. S-09: `cancel()` do OCR nao matava o subprocesso e a
conclusao velha zerava `busy` com outro pedido rodando.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest

import _isolation  # noqa: F401
from PyQt6.QtWidgets import QApplication

from pgn_live_editor.services.worker_host import WorkerFailed, WorkerHost, run_process


def wait_until(app, condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class HostTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.finished: list = []
        self.failed: list[WorkerFailed] = []
        self.started: list = []

    def host(self, job) -> WorkerHost:
        host = WorkerHost(job)
        host.finished_signal.connect(lambda request_id, result: self.finished.append(result))
        host.failed_signal.connect(self.failed.append)
        self.addCleanup(host.shutdown)
        return host

    def test_a_result_is_delivered_and_busy_clears(self):
        host = self.host(lambda payload, context: payload * 2)

        host.submit(21)
        self.assertTrue(host.busy)

        self.assertTrue(wait_until(self.app, lambda: self.finished == [42]))
        self.assertFalse(host.busy)

    def test_rapid_requests_run_the_first_and_the_last_only(self):
        gate = threading.Event()

        def job(payload, context):
            self.started.append(payload)
            gate.wait(2)
            return payload

        host = self.host(job)
        for value in range(4):
            host.submit(value)
        gate.set()

        self.assertTrue(wait_until(self.app, lambda: self.finished == [3]))
        self.assertEqual([0, 3], self.started)

    def test_a_superseded_job_is_told_to_stop(self):
        stopped = threading.Event()

        def job(payload, context):
            if payload == "longo":
                while not context.cancelled:
                    time.sleep(0.01)
                stopped.set()
            return payload

        host = self.host(job)
        host.submit("longo")
        host.submit("curto")

        self.assertTrue(wait_until(self.app, lambda: self.finished == ["curto"]))
        self.assertTrue(stopped.is_set())

    def test_a_cancelled_result_is_never_delivered(self):
        gate = threading.Event()
        host = self.host(lambda payload, context: gate.wait(2) and payload)

        host.submit("x")
        host.cancel()
        self.assertFalse(host.busy)
        gate.set()

        wait_until(self.app, lambda: False, timeout=0.3)
        self.assertEqual([], self.finished)

    def test_an_exception_becomes_worker_failed_and_the_host_keeps_working(self):
        def job(payload, context):
            if payload == "quebra":
                raise RuntimeError("defeito de proposito")
            return payload

        host = self.host(job)
        host.submit("quebra")
        self.assertTrue(wait_until(self.app, lambda: bool(self.failed)))
        self.assertIn("defeito de proposito", self.failed[0].message)
        self.assertIn("RuntimeError", self.failed[0].traceback)

        host.submit("ok")
        self.assertTrue(wait_until(self.app, lambda: self.finished == ["ok"]))

    def test_progress_of_the_current_request_is_forwarded(self):
        progress: list = []

        def job(payload, context):
            for step in range(3):
                context.progress(step)
            return payload

        host = self.host(job)
        host.progress_signal.connect(lambda request_id, value: progress.append(value))
        host.submit("p")

        self.assertTrue(wait_until(self.app, lambda: bool(self.finished)))
        self.assertEqual([0, 1, 2], progress)

    def test_shutdown_never_deletes_a_running_thread(self):
        release = threading.Event()
        host = WorkerHost(lambda payload, context: release.wait(5))
        host.submit("preso")
        wait_until(self.app, lambda: False, timeout=0.1)

        finished_cleanly = host.shutdown(timeout_ms=100)
        self.assertFalse(finished_cleanly)
        for _ in range(3):
            self.app.processEvents()  # um deleteLater pendente seria entregue aqui
        release.set()
        self.assertTrue(WorkerHost.drain_abandoned(timeout_ms=3000))


class RunProcessTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    def test_cancelling_kills_the_child_process(self):
        results: list = []
        sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
        host = WorkerHost(lambda payload, context: run_process(sleeper, context))
        host.failed_signal.connect(results.append)
        host.finished_signal.connect(lambda request_id, result: results.append(result))
        self.addCleanup(host.shutdown)

        started = time.monotonic()
        host.submit(None)
        wait_until(self.app, lambda: False, timeout=0.5)
        host.cancel()
        self.assertTrue(host.shutdown(timeout_ms=5000))

        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual([], results)

    def test_stderr_tail_goes_into_the_error(self):
        failing = [sys.executable, "-c", "import sys; sys.stderr.write('x' * 9000 + 'FIM'); sys.exit(3)"]

        class Context:
            cancelled = False

            def on_cancel(self, callback):
                pass

        with self.assertRaises(subprocess.CalledProcessError) as caught:
            run_process(failing, Context())
        self.assertTrue(caught.exception.stderr.endswith("FIM"))
        self.assertLessEqual(len(caught.exception.stderr), 4096)

    def test_a_child_flooding_stdout_does_not_hang(self):
        flooding = [sys.executable, "-c", "import sys; sys.stdout.write('y' * 2_000_000)"]

        class Context:
            cancelled = False

            def on_cancel(self, callback):
                pass

        self.assertEqual(0, run_process(flooding, Context(), timeout=30).returncode)


class FacadeShutdownTests(unittest.TestCase):
    """Fechar no meio de cada tipo de trabalho, 50 vezes: sem crash, sem resultado tardio."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    @staticmethod
    def stubborn(payload, context):
        # Ignora o cancelamento por um tempo, como um OCR no meio de uma pagina.
        time.sleep(0.02)
        return payload

    def facades(self):
        from pgn_live_editor.services.diagram_ocr_worker import AsyncDiagramOcr
        from pgn_live_editor.services.engine_service import EngineScanner
        from pgn_live_editor.services.full_pdf_recognition_worker import AsyncFullPdfRecognition

        return [
            (AsyncDiagramOcr(job=self.stubborn), lambda f: f.request("x.pdf", 0)),
            (AsyncFullPdfRecognition(job=self.stubborn), lambda f: f.request("x.pdf")),
            (EngineScanner(job=self.stubborn), lambda f: f.scan(None, "", None)),
        ]

    def test_closing_in_the_middle_of_each_kind_of_work(self):
        for _ in range(50):
            for facade, start in self.facades():
                delivered: list = []
                facade.finished_signal.connect(delivered.append)
                start(facade)
                facade.shutdown()
                self.app.processEvents()
                self.assertEqual([], delivered)
                facade.deleteLater()
        self.app.processEvents()
        self.assertTrue(WorkerHost.drain_abandoned(timeout_ms=3000))

    def test_cancel_drops_the_result_of_every_facade(self):
        for facade, start in self.facades():
            self.addCleanup(facade.shutdown)
            delivered: list = []
            facade.finished_signal.connect(delivered.append)
            start(facade)
            facade.cancel()
            wait_until(self.app, lambda: False, timeout=0.1)
            self.assertEqual([], delivered, type(facade).__name__)


if __name__ == "__main__":
    unittest.main()
