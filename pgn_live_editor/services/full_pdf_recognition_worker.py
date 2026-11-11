"""Worker assíncrono para reconhecimento de todo o PDF (sobre o `WorkerHost`, R-303)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject
from PyQt6.QtCore import pyqtSignal as Signal

from .diagram_ocr import DiagramOcrUnavailable, _torch_error_message
from .worker_host import JobContext, WorkerHost, run_process

FULL_PDF_TIMEOUT_S = 3600


def recognize_full_pdf(payload: tuple[Path, int, int, int], context: JobContext) -> list[dict[str, object]]:
    """Roda o reconhecimento do intervalo num subprocesso; o cancelamento o mata."""
    pdf_path, dpi, start_page, end_page = payload
    with tempfile.NamedTemporaryFile(prefix="pgn_full_pdf_", suffix=".jsonl", delete=False) as output:
        output_path = Path(output.name)

    def on_stderr_line(line: str) -> None:
        parts = line.strip().split()
        if len(parts) == 5 and parts[0] == "PROGRESS":
            try:
                context.progress((int(parts[1]), int(parts[2]), int(parts[4])))
            except ValueError:
                pass

    command = [
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
    ]
    try:
        try:
            completed = run_process(command, context, timeout=FULL_PDF_TIMEOUT_S, on_stderr_line=on_stderr_line)
            return_code, tail = completed.returncode, completed.stderr or ""
        except subprocess.CalledProcessError as exc:
            return_code, tail = exc.returncode, exc.stderr or ""
        if context.cancelled:
            return []
        progress_free = "\n".join(line for line in tail.splitlines() if not line.startswith("PROGRESS "))
        try:
            return collect_pages(output_path, return_code=return_code, stderr_tail=progress_free)
        except ValueError as exc:
            raise DiagramOcrUnavailable(_torch_error_message(str(exc))) from exc
    finally:
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
    finished_signal = Signal(object)  # [pagina]
    failed_signal = Signal(str)
    progress_signal = Signal(int, int, int)

    def __init__(self, parent: QObject | None = None, *, job=recognize_full_pdf):
        super().__init__(parent)
        self._host = WorkerHost(job, self)
        self._host.finished_signal.connect(lambda _request, pages: self.finished_signal.emit(pages))
        self._host.failed_signal.connect(lambda failure: self.failed_signal.emit(failure.message))
        self._host.progress_signal.connect(lambda _request, value: self.progress_signal.emit(*value))

    @property
    def busy(self) -> bool:
        return self._host.busy

    def request(self, pdf_path: Path, *, start_page: int = 0, end_page: int | None = None, dpi: int = 220) -> int:
        final_page = start_page if end_page is None else end_page
        return self._host.submit((Path(pdf_path), int(dpi), int(start_page), int(final_page)))

    def cancel(self) -> None:
        self._host.cancel()

    def shutdown(self) -> None:
        self._host.shutdown()
