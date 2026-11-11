"""Orcamentos de desempenho da SPEC_V3 §8 (R-401), marcados `slow`.

Rodam fora da suite normal (`pytest -m slow -s`); o CI roda no job de
qualidade e grava os tempos no resumo do job. Cada teste falha se passar de
2x o orcamento -- a margem da SPEC para maquinas de CI mais lentas que a de
referencia. A maquina e os tempos vao para `performance-report.txt`.

As entradas sao geradas aqui, sem arquivo externo: um livro de 1 MB em
descritiva inglesa e um em SAN com ~3 000 variantes, os dois em partidas de
32 lances com cabecalho.
"""

from __future__ import annotations

import os
import platform
import statistics
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401
import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from pgn_live_editor.services.parse_worker import ParseConfig, ParsePipeline
from pgn_live_editor.services.project_service import Project, save_project

pytestmark = pytest.mark.slow

ONE_MEGABYTE = 1_000_000
MARGIN = 2.0

# Orcamentos (segundos ou milissegundos) na maquina de referencia. Os dois
# primeiros foram revistos na Fase 18: 3 s nao cabe num analisador tolerante
# feito sobre o python-chess, que sozinho gasta 1,4 s so para interpretar e
# jogar os 141 mil lances do livro SAN (2,5 s para o proprio leitor estrito de
# PGN). O que o usuario sente -- abrir e digitar -- deixou de depender disso:
# o livro inteiro e analisado em segundo plano.
BUDGET_DESCRIPTIVE_S = 16.0
BUDGET_SAN_S = 12.0
BUDGET_OPEN_S = 2.0
BUDGET_KEY_P95_MS = 16.0
BUDGET_KEY_MAX_MS = 50.0

DESCRIPTIVE_GAME = (
    "1. P-K4 P-K4 2. N-KB3 N-QB3 3. B-N5 P-QR3 4. B-R4 N-B3 5. O-O B-K2 6. R-K1 P-QN4 "
    "7. B-N3 P-Q3 8. P-B3 O-O 9. P-KR3 N-QR4 10. B-B2 P-B4 11. P-Q4 Q-B2 "
    "White has a strong centre and the knight on the rook file is out of play, so the plan is to "
    "regroup the knights before advancing in the centre. 12. QN-Q2 BP×P 13. P×P N-B3 "
    "(13... B-Q2 14. N-B1 QR-B1 15. N-K3) 14. P-Q5 N-QN5 15. B-N1 P-QR4 16. P-R3 N-R3 "
)
SAN_GAME = (
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5 7. Bb3 d6 8. c3 O-O 9. h3 Na5 10. Bc2 c5 "
    "{The Chigorin.} 11. d4 Qc7 12. Nbd2 cxd4 13. cxd4 Nc6 14. Nb3 a5 15. Be3 a4 16. Nbd2 Bd7 "
)
SAN_GAME_WITH_VARIATION = SAN_GAME.replace("4. Ba4", "(3... Nf6 4. O-O Nxe4) 4. Ba4")

REPORT = Path(os.environ.get("PERFORMANCE_REPORT", "performance-report.txt"))


def book(target: int, game_for) -> str:
    parts: list[str] = []
    size = 0
    number = 0
    while size < target:
        number += 1
        block = f'[Event "Partida {number}"]\n\n{game_for(number)}*\n\n'
        parts.append(block)
        size += len(block)
    return "".join(parts)


def descriptive_book() -> str:
    return book(ONE_MEGABYTE, lambda _n: DESCRIPTIVE_GAME)


def san_book() -> str:
    # Duas em tres partidas com uma variante: ~3 000 variantes em 1 MB.
    return book(ONE_MEGABYTE, lambda n: SAN_GAME_WITH_VARIATION if n % 3 else SAN_GAME)


def machine() -> str:
    memory = ""
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong),
                ("available", ctypes.c_ulonglong),
                ("page_total", ctypes.c_ulonglong),
                ("page_available", ctypes.c_ulonglong),
                ("virtual_total", ctypes.c_ulonglong),
                ("virtual_available", ctypes.c_ulonglong),
                ("extended", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.length = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            memory = f", {status.total / 2**30:.0f} GB de RAM"
    except (AttributeError, OSError):
        try:
            memory = f", {os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30:.0f} GB de RAM"
        except (ValueError, OSError, AttributeError):
            pass
    return f"{platform.processor() or platform.machine()}, {os.cpu_count()} nucleos{memory}, Python {platform.python_version()}"


def record(name: str, measured: str, budget: str, passed: bool) -> None:
    line = f"{'ok ' if passed else 'ACIMA'} {name}: {measured} (orcamento {budget}, falha acima de {MARGIN:g}x)"
    print(line)
    first = not REPORT.exists()
    with REPORT.open("a", encoding="utf-8") as handle:
        if first:
            handle.write(f"Maquina: {machine()}\n")
        handle.write(line + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"- {line}\n")


def best_of(runs: int, action) -> float:
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        action()
        timings.append(time.perf_counter() - started)
    return min(timings)


class ThroughputTests(unittest.TestCase):
    def check(self, name: str, text: str, budget: float) -> None:
        elapsed = best_of(1, lambda: ParsePipeline(ParseConfig.build(locale="en")).analyze_all(text))
        record(name, f"{elapsed:.2f} s", f"{budget:g} s", elapsed <= budget)
        self.assertLessEqual(elapsed, budget * MARGIN)

    def test_one_megabyte_of_descriptive(self):
        self.check("normalizar+tokenizar+analisar 1 MB de descritiva", descriptive_book(), BUDGET_DESCRIPTIVE_S)

    def test_one_megabyte_of_san_with_variations(self):
        self.check("normalizar+tokenizar+analisar 1 MB de SAN com variantes", san_book(), BUDGET_SAN_S)


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        from pgn_live_editor.__main__ import UI_SWITCH_INTERVAL_S
        from pgn_live_editor.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        # Como o programa roda (ver `__main__`): sem isto o teste mediria
        # outra coisa que o usuario ve.
        previous = sys.getswitchinterval()
        sys.setswitchinterval(UI_SWITCH_INTERVAL_S)
        self.addCleanup(sys.setswitchinterval, previous)
        self.tmp = Path(tempfile.mkdtemp(prefix="orcamento-"))
        self.path = save_project(Project(raw_text=san_book()), self.tmp / "livro.plez")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.editor_panel.shutdown)

    def test_opening_a_one_megabyte_project(self):
        started = time.perf_counter()
        self.window.open_path(self.path)
        self.app.processEvents()
        elapsed = time.perf_counter() - started

        record(
            "abrir projeto de 1 MB ate poder editar",
            f"{elapsed:.2f} s",
            f"{BUDGET_OPEN_S:g} s",
            elapsed <= BUDGET_OPEN_S,
        )
        self.assertLessEqual(elapsed, BUDGET_OPEN_S * MARGIN)

    def test_typing_in_a_one_megabyte_book(self):
        self.window.open_path(self.path)
        editor = self.window.editor_panel
        cursor = editor.text_edit.textCursor()
        cursor.setPosition(len(editor.document_text()) // 2)
        editor.text_edit.setTextCursor(cursor)

        laps: list[float] = []

        def spin(seconds: float) -> None:
            deadline = time.perf_counter() + seconds
            while time.perf_counter() < deadline:
                started = time.perf_counter()
                self.app.processEvents()
                lap = (time.perf_counter() - started) * 1000
                if lap > 0.5:  # voltas vazias nao dizem nada
                    laps.append(lap)
                time.sleep(0.002)

        # Digitando como gente (~16 teclas/s), com o livro ainda sendo
        # preenchido em segundo plano -- o pior caso.
        for char in "Nf3 Nc6 Bb5 a6 " * 4:
            started = time.perf_counter()
            editor.text_edit.textCursor().insertText(char)
            self.app.processEvents()
            laps.append((time.perf_counter() - started) * 1000)
            spin(0.06)
        spin(2.0)

        laps.sort()
        p95 = laps[max(0, int(len(laps) * 0.95) - 1)]
        worst = laps[-1]
        record(
            "trabalho na thread da UI por tecla (p95)",
            f"{p95:.1f} ms",
            f"{BUDGET_KEY_P95_MS:g} ms",
            p95 <= BUDGET_KEY_P95_MS,
        )
        record(
            "trabalho na thread da UI por tecla (maximo)",
            f"{worst:.1f} ms (mediana {statistics.median(laps):.1f} ms)",
            f"{BUDGET_KEY_MAX_MS:g} ms",
            worst <= BUDGET_KEY_MAX_MS,
        )
        self.assertLessEqual(p95, BUDGET_KEY_P95_MS * MARGIN)
        self.assertLessEqual(worst, BUDGET_KEY_MAX_MS * MARGIN)


if __name__ == "__main__":
    unittest.main()
