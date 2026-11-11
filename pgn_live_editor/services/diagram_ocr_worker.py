"""Execução assíncrona do OCR opcional de diagramas (sobre o `WorkerHost`, R-303)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject
from PyQt6.QtCore import pyqtSignal as Signal

from .diagram_ocr import DiagramOcrUnavailable, DiagramRecognition, _torch_error_message
from .worker_host import JobContext, WorkerFailed, WorkerHost, run_process

OCR_TIMEOUT_S = 300


def read_diagrams(payload: tuple[Path, int, int], context: JobContext) -> tuple[int, list[DiagramRecognition]]:
    """Roda o OCR de uma pagina num subprocesso; o cancelamento o mata (S-09)."""
    pdf_path, page_index, dpi = payload
    with tempfile.NamedTemporaryFile(prefix="pgn_diagram_", suffix=".json", delete=False) as output:
        output_path = Path(output.name)
    try:
        command = [
            sys.executable,
            "-m",
            "pgn_live_editor.services.diagram_ocr_process",
            str(pdf_path),
            str(page_index),
            "--dpi",
            str(dpi),
            "--output",
            str(output_path),
        ]
        try:
            run_process(command, context, timeout=OCR_TIMEOUT_S)
        except subprocess.CalledProcessError as exc:
            raise DiagramOcrUnavailable(_torch_error_message(exc.stderr or "processo OCR terminou com erro")) from exc
        if context.cancelled:
            return page_index, []
        raw_payload = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        if not raw_payload.strip():
            raise DiagramOcrUnavailable("o processo OCR não produziu um resultado JSON")
        records = json.loads(raw_payload)
        if not isinstance(records, list):
            raise DiagramOcrUnavailable("o processo OCR devolveu um resultado em formato inesperado")
        return page_index, [DiagramRecognition.from_record(record) for record in records]
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass


class AsyncDiagramOcr(QObject):
    """Fachada que mantém a UI responsiva e descarta resultados obsoletos."""

    finished_signal = Signal(object)  # (page_index, [DiagramRecognition])
    failed_signal = Signal(str)

    def __init__(self, parent: QObject | None = None, *, job=read_diagrams):
        super().__init__(parent)
        self._host = WorkerHost(job, self)
        self._host.finished_signal.connect(lambda _request, result: self.finished_signal.emit(result))
        self._host.failed_signal.connect(self._on_failed)

    @property
    def busy(self) -> bool:
        return self._host.busy

    def request(self, pdf_path: Path, page_index: int, *, dpi: int = 220) -> int:
        return self._host.submit((Path(pdf_path), int(page_index), int(dpi)))

    def cancel(self) -> None:
        """Nenhum resultado chega depois disto; o subprocesso em execução é terminado."""
        self._host.cancel()

    def _on_failed(self, failure: WorkerFailed) -> None:
        self.failed_signal.emit(failure.message)

    def shutdown(self) -> None:
        self._host.shutdown()
