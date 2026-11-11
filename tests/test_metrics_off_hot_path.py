"""Metrica fora do caminho quente (R-404).

S-04 (resto): mesmo so com append, cada medida fazia mkdir + stat + abrir e
fechar o arquivo na thread que media -- ~1 ms por chamada, duas por partida:
abrir um livro de 3 500 partidas gastava ~7 s so gravando metrica.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401

from pgn_live_editor.services import observability
from pgn_live_editor.services.parse_worker import ParseConfig, ParsePipeline

sys.path.insert(0, str(Path(__file__).parent))

BOOK = "".join(f'[Event "{n}"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 (3... Nf6 4. O-O) 4. Ba4 Nf6 *\n\n' for n in range(150))


def best_of(runs: int, action) -> float:
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        action()
        timings.append(time.perf_counter() - started)
    return min(timings)


class HotPathTests(unittest.TestCase):
    def setUp(self):
        observability.flush_metrics()

    def test_recording_costs_microseconds_on_the_calling_thread(self):
        elapsed = best_of(3, lambda: [observability.record_metric("parse", 1.0, game=n) for n in range(2000)])

        self.assertLess(elapsed / 2000 * 1000, 0.05, "ms por metrica na thread que mede")

    def test_analysis_with_metrics_costs_at_most_ten_percent_more(self):
        def analyze():
            ParsePipeline(ParseConfig.build(locale="en")).analyze_all(BOOK)

        analyze()
        observability.flush_metrics()
        # Alternado, o melhor de cada: o ruido da maquina (outras threads da
        # suite, o coletor de lixo) cai igual nos dois lados.
        with_runs: list[float] = []
        without_runs: list[float] = []
        for _ in range(7):
            with_runs.append(best_of(1, analyze))
            with mock.patch.object(observability, "record_metric", lambda *a, **k: None):
                without_runs.append(best_of(1, analyze))
        with_metrics, without_metrics = min(with_runs), min(without_runs)

        self.assertLessEqual(with_metrics, without_metrics * 1.1, f"{with_metrics:.3f} s x {without_metrics:.3f} s")

    def test_recorded_metrics_reach_the_file(self):
        observability.record_metric("teste_r404", 2.5, game=7)

        observability.flush_metrics()

        recorded = [m for m in observability.read_metrics() if m["operation"] == "teste_r404"]
        self.assertEqual([2.5], [m["duration_ms"] for m in recorded])

    def test_the_file_is_bounded_by_size(self):
        path = observability.metrics_path()
        for n in range(20_000):
            observability.record_metric("parse", 1.0, game=n)
        observability.flush_metrics()

        sizes = [p.stat().st_size for p in [path, *path.parent.glob(path.name + ".*")] if p.exists()]
        self.assertLessEqual(max(sizes), observability.MAX_METRIC_BYTES + 1024)
        self.assertLessEqual(len(sizes), 2)


if __name__ == "__main__":
    unittest.main()
