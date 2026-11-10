"""Worker assíncrono para reconhecimento de todo o PDF."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, QThread
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtCore import pyqtSlot as Slot

from .diagram_ocr import DiagramOcrUnavailable, _torch_error_message

#: Quantas linhas do stderr do processo entram na mensagem de erro.
STDERR_TAIL_LINES = 20


class _FullPdfRunner(QObject):
    finished_signal = Signal(object)
    failed_signal = Signal(object)
    progress_signal = Signal(int, int, int)
    _process: subprocess.Popen | None = None

    @Slot(object)
    def run(self, payload: tuple[int, Path, int, int, int]):
        request_id, pdf_path, dpi, start_page, end_page = payload
        output_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="pgn_full_pdf_", suffix=".jsonl", delete=False) as output:
                output_path = Path(output.name)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "pgn_live_editor.services.full_pdf_recognition_process",
                    str(pdf_path),
                    "--dpi",
                    str(dpi),
                    "--start-page",
                    str(start_page + 1),
                    "--end-page",
                    str(end_page + 1),
                    "--output",
                    str(output_path),
                ],
                # Ninguem le o stdout: com PIPE, um filho que escrevesse ~400 KB
                # ali travava para sempre e o timeout nunca chegava (S-10).
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._process = process
            assert process.stderr is not None
            tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
            for line in process.stderr:
                parts = line.strip().split()
                if len(parts) == 5 and parts[0] == "PROGRESS":
                    try:
                        self.progress_signal.emit(int(parts[1]), int(parts[2]), int(parts[4]))
                    except ValueError:
                        pass
                elif line.strip():
                    tail.append(line.rstrip())
            return_code = process.wait(timeout=3600)
            pages = collect_pages(output_path, return_code=return_code, stderr_tail="\n".join(tail))
            self.finished_signal.emit((request_id, pages))
        except (DiagramOcrUnavailable, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.failed_signal.emit((request_id, _torch_error_message(str(exc))))
        finally:
            self._process = None
            if output_path is not None:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass


def collect_pages(output_path: Path, *, return_code: int, stderr_tail: str) -> list[dict[str, object]]:
    """As paginas que o processo chegou a gravar (JSONL), mesmo que ele tenha morrido (R-109).

    Uma linha cortada no fim (o processo morreu escrevendo) e descartada. Se o
    processo falhou depois de ler alguma pagina, o resultado traz as paginas
    lidas e uma pagina-marcador `interrupted` com o motivo; se falhou antes da
    primeira, levanta ValueError com o fim do stderr -- antes a mensagem era so
    "terminou com erro".
    """
    pages: list[dict[str, object]] = []
    raw = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else ""
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            pages.append(item)
    reason = (
        stderr_tail.strip().splitlines()[-1]
        if stderr_tail.strip()
        else "o processo de reconhecimento terminou com erro"
    )
    if return_code != 0 and not pages:
        raise ValueError(f"O reconhecimento integral falhou antes da primeira página: {reason}")
    if not pages:
        raise ValueError("O reconhecimento integral não produziu resultado.")
    if return_code != 0:
        last_page = max(int(page.get("page", 0) or 0) for page in pages)
        pages.append(
            {"page": last_page + 1, "text": "", "text_method": "interrupted", "diagrams": [], "ocr_error": reason}
        )
    return pages


class AsyncFullPdfRecognition(QObject):
    finished_signal = Signal(object)
    failed_signal = Signal(str)
    progress_signal = Signal(int, int, int)
    _request_signal = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._runner: _FullPdfRunner | None = None
        self._request_id = 0
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def _ensure_thread(self):
        if self._thread is not None:
            return
        self._thread = QThread()
        self._runner = _FullPdfRunner()
        self._runner.moveToThread(self._thread)
        self._request_signal.connect(self._runner.run)
        self._runner.finished_signal.connect(self._on_finished)
        self._runner.failed_signal.connect(self._on_failed)
        self._runner.progress_signal.connect(self.progress_signal)
        self._thread.finished.connect(self._runner.deleteLater)
        self._thread.start()

    def request(self, pdf_path: Path, *, start_page: int = 0, end_page: int | None = None, dpi: int = 220):
        if self._busy:
            return
        self._request_id += 1
        self._busy = True
        self._ensure_thread()
        final_page = start_page if end_page is None else end_page
        self._request_signal.emit((self._request_id, Path(pdf_path), int(dpi), int(start_page), int(final_page)))

    def cancel(self):
        if self._busy:
            self._request_id += 1
            self._busy = False
            runner = self._runner
            process = runner._process if runner is not None else None
            if process is not None and process.poll() is None:
                process.terminate()

    @Slot(object)
    def _on_finished(self, payload):
        request_id, pages = payload
        self._busy = False
        if request_id == self._request_id:
            self.finished_signal.emit(pages)

    @Slot(object)
    def _on_failed(self, payload):
        request_id, message = payload
        self._busy = False
        if request_id == self._request_id:
            self.failed_signal.emit(message)

    def shutdown(self):
        self.cancel()
        if self._thread is None:
            return
        thread = self._thread
        self._thread, self._runner = None, None
        thread.quit()
        thread.wait(5000)
        thread.deleteLater()
