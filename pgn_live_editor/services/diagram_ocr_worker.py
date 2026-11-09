"""Execução assíncrona do OCR opcional de diagramas."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, QThread
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtCore import pyqtSlot as Slot

from .diagram_ocr import DiagramOcrUnavailable, DiagramRecognition, _torch_error_message


class _DiagramOcrRunner(QObject):
    finished_signal = Signal(object)
    failed_signal = Signal(object)

    @Slot(object)
    def run(self, payload: tuple[int, Path, int, int]):
        request_id, pdf_path, page_index, dpi = payload
        output_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="pgn_diagram_", suffix=".json", delete=False) as output:
                output_path = Path(output.name)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pgn_live_editor.services.diagram_ocr_process",
                    str(pdf_path),
                    str(page_index),
                    "--dpi",
                    str(dpi),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or "processo OCR terminou com erro"
                raise DiagramOcrUnavailable(_torch_error_message(detail))
            raw_payload = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
            if not raw_payload.strip():
                detail = completed.stderr.strip() or "o processo OCR não produziu um resultado JSON"
                raise DiagramOcrUnavailable(detail)
            records = json.loads(raw_payload)
            results = [DiagramRecognition.from_record(record) for record in records]
        except (DiagramOcrUnavailable, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            self.failed_signal.emit((request_id, str(exc)))
            return
        finally:
            if output_path is not None:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.finished_signal.emit((request_id, page_index, results))


class AsyncDiagramOcr(QObject):
    """Fachada que mantém a UI responsiva e descarta resultados obsoletos."""

    finished_signal = Signal(object)
    failed_signal = Signal(str)
    _request_signal = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._runner: _DiagramOcrRunner | None = None
        self._request_id = 0
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread()
        self._runner = _DiagramOcrRunner()
        self._runner.moveToThread(self._thread)
        self._request_signal.connect(self._runner.run)
        self._runner.finished_signal.connect(self._on_finished)
        self._runner.failed_signal.connect(self._on_failed)
        self._thread.finished.connect(self._runner.deleteLater)
        self._thread.start()

    def request(self, pdf_path: Path, page_index: int, *, dpi: int = 220) -> int:
        if self._busy:
            return self._request_id
        self._request_id += 1
        self._busy = True
        self._ensure_thread()
        self._request_signal.emit((self._request_id, Path(pdf_path), int(page_index), int(dpi)))
        return self._request_id

    def cancel(self) -> None:
        """Invalida o pedido atual; uma inferência já em execução é descartada."""
        if self._busy:
            self._request_id += 1
            self._busy = False

    @Slot(object)
    def _on_finished(self, payload: tuple[int, int, list[DiagramRecognition]]) -> None:
        request_id, page_index, results = payload
        self._busy = False
        if request_id == self._request_id:
            self.finished_signal.emit((page_index, results))

    @Slot(object)
    def _on_failed(self, payload: tuple[int, str]) -> None:
        request_id, message = payload
        self._busy = False
        if request_id == self._request_id:
            self.failed_signal.emit(message)

    def shutdown(self) -> None:
        self.cancel()
        if self._thread is None:
            return
        thread = self._thread
        self._thread, self._runner = None, None
        self._busy = False
        thread.quit()
        thread.wait(5000)
        thread.deleteLater()
