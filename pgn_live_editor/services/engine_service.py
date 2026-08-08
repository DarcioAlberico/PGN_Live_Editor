"""Motor de xadrez opcional (SPEC 5.3).

O uso mais valioso do motor aqui **nao e analisar a partida** -- e desconfiar do
OCR. Um lance da linha principal de um livro que derruba a avaliacao e quase
sempre um lance mal lido, nao um erro do mestre: se fosse erro do mestre, o
livro teria marcado com `?` ou `??` e comentado. E o alerta `SUSPECT_BLUNDER`,
a ultima peneira -- a que pega o que passou por todas as outras verificacoes
justamente porque o lance errado tambem era legal na posicao.

Tres decisoes sustentam este modulo:

1. **O motor e sempre opcional.** Sem Stockfish instalado nada aqui quebra: o
   programa segue inteiro e a varredura simplesmente nao e oferecida.
2. **A regra de suspeita e pura.** `scan_mainline` recebe uma *funcao* de
   avaliacao, nao um motor. E o que permite testar a deteccao inteira sem
   binario nenhum, e o que permite trocar a fonte de avaliacao depois.
3. **Nada roda na thread de UI.** `EngineScanner` leva a varredura para uma
   `QThread`, como `parse_worker` faz com a analise.

Convencao de sinal, usada o tempo todo abaixo: toda avaliacao e em centipeoes
**do ponto de vista de quem tem a vez** -- que e como o UCI responde. Isso torna
a queda de um lance uma soma, nao uma subtracao com troca de sinal:

    queda = avaliacao_antes + avaliacao_depois

porque `avaliacao_depois` ja esta na vez do adversario. Perder a dama num lance
da +900 para o outro lado, e a soma da os 900 de queda direto.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import chess
import Levenshtein
from PyQt6.QtCore import QObject, QThread
from PyQt6.QtCore import pyqtSignal as Signal
from PyQt6.QtCore import pyqtSlot as Slot

from ..core.issues import (
    ACTION_ACCEPT,
    ACTION_IGNORE,
    SUSPECT_BLUNDER,
    Candidate,
    Issue,
)
from ..core.parser import GameAST, MoveNode
from .project_service import app_data_dir

# Mate vira um numero grande, mas finito: um mate a favor nao pode dominar a
# aritmetica de queda a ponto de todo lance seguinte virar alerta.
MATE_SCORE = 10_000

ENGINE_ENV_VAR = "PGN_LIVE_EDITOR_ENGINE"
ENGINE_SETTINGS_FILE = "engine.json"

# Se o livro ja marcou o lance como ruim, o motor nao tem nada a acrescentar.
BAD_MOVE_NAGS = {"?", "??", "?!", "$2", "$4", "$6"}

#: Avalia um tabuleiro e devolve centipeoes do ponto de vista de quem joga.
Evaluator = Callable[[chess.Board], int]


class EngineUnavailable(RuntimeError):
    """Nao ha executavel de motor utilizavel."""


@dataclass(frozen=True)
class EngineSettings:
    """Tudo que o usuario pode regular na varredura."""

    path: str = ""
    movetime_ms: int = 200
    depth: int = 0  # 0 = sem limite de profundidade, so tempo
    threshold_cp: int = 300
    # Numa posicao ja perdida qualquer lance "derruba" a avaliacao; alertar ali
    # so encheria o painel de ruido.
    lost_position_cp: int = 900
    max_moves: int = 200
    # Acima disto a vizinhanca do lance e ambigua demais para valer a pena.
    repair_max_alternatives: int = 6

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "movetime_ms": self.movetime_ms,
            "depth": self.depth,
            "threshold_cp": self.threshold_cp,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> EngineSettings:
        base = cls()
        if not isinstance(payload, dict):
            return base
        return replace(
            base,
            path=str(payload.get("path", base.path)),
            movetime_ms=int(payload.get("movetime_ms", base.movetime_ms) or base.movetime_ms),
            depth=int(payload.get("depth", base.depth) or 0),
            threshold_cp=int(payload.get("threshold_cp", base.threshold_cp) or base.threshold_cp),
        )


# ----------------------------------------------------------------------
# Onde esta o motor
# ----------------------------------------------------------------------


def discover_engine_path(configured: str = "") -> str:
    """Caminho do motor: o configurado, a variavel de ambiente, ou o PATH."""
    if configured and Path(configured).is_file():
        return configured

    from_env = os.environ.get(ENGINE_ENV_VAR, "")
    if from_env and Path(from_env).is_file():
        return from_env

    for name in ("stockfish", "stockfish.exe"):
        found = shutil.which(name)
        if found:
            return found

    return ""


def _settings_file() -> Path:
    return app_data_dir() / ENGINE_SETTINGS_FILE


def load_engine_settings() -> EngineSettings:
    target = _settings_file()
    if not target.is_file():
        return EngineSettings()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return EngineSettings()
    return EngineSettings.from_dict(payload)


def save_engine_settings(settings: EngineSettings) -> None:
    try:
        _settings_file().write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Preferencia de motor nao vale travar o programa.
        pass


def engine_is_available(settings: EngineSettings | None = None) -> bool:
    return bool(discover_engine_path((settings or EngineSettings()).path))


# ----------------------------------------------------------------------
# Sessao UCI
# ----------------------------------------------------------------------


class EngineSession:
    """Um processo UCI vivo, com memoria das posicoes ja avaliadas.

    Usar sempre como gerenciador de contexto: o processo do motor precisa ser
    encerrado mesmo quando a varredura levanta excecao.
    """

    def __init__(self, settings: EngineSettings | None = None):
        self.settings = settings or EngineSettings()
        self.path = discover_engine_path(self.settings.path)
        self._engine = None
        self._cache: dict[str, int] = {}

    # ------------------------------------------------------------------

    def open(self) -> EngineSession:
        if self._engine is not None:
            return self

        if not self.path:
            raise EngineUnavailable(
                "Nenhum motor UCI encontrado. Instale o Stockfish, ou aponte o "
                f"executavel em {ENGINE_ENV_VAR} ou no menu Conferir."
            )

        # Import tardio: `chess.engine` puxa asyncio, e quem nunca usa o motor
        # nao deve pagar por isso ao abrir o programa.
        import chess.engine

        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
        except (OSError, ValueError) as exc:
            raise EngineUnavailable(f"Nao foi possivel iniciar o motor em {self.path}.\n\n{exc}") from exc
        return self

    def close(self) -> None:
        engine, self._engine = self._engine, None
        if engine is None:
            return
        try:
            engine.quit()
        except Exception:  # noqa: BLE001 - encerrar motor nunca pode derrubar a UI
            pass

    def __enter__(self) -> EngineSession:
        return self.open()

    def __exit__(self, *_exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------

    def _limit(self):
        import chess.engine

        if self.settings.depth > 0:
            return chess.engine.Limit(depth=self.settings.depth)
        return chess.engine.Limit(time=max(self.settings.movetime_ms, 1) / 1000)

    def evaluate(self, board: chess.Board) -> int:
        """Centipeoes do ponto de vista de quem tem a vez."""
        if self._engine is None:
            raise EngineUnavailable("A sessao do motor nao foi aberta.")

        key = board.fen()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        info = self._engine.analyse(board, self._limit())
        score = info["score"].relative.score(mate_score=MATE_SCORE)
        value = int(score if score is not None else 0)
        self._cache[key] = value
        return value


# ----------------------------------------------------------------------
# A regra de suspeita -- pura, sem motor
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ScannedMove:
    """Um lance da linha principal, com a posicao antes e depois avaliadas."""

    node: MoveNode
    ply: int
    san: str
    board_before: chess.Board
    eval_before_cp: int
    eval_after_cp: int

    @property
    def drop_cp(self) -> int:
        """Quanto quem jogou perdeu. Ver a convencao de sinal no topo."""
        return self.eval_before_cp + self.eval_after_cp


@dataclass
class ScanReport:
    issues: list[Issue] = field(default_factory=list)
    scanned_moves: int = 0
    evaluated_positions: int = 0
    truncated: bool = False

    def summary(self) -> str:
        if not self.scanned_moves:
            return "Nao ha linha principal para conferir."
        parts = [f"{self.scanned_moves} lance(s) conferido(s) pelo motor"]
        parts.append(f"{len(self.issues)} suspeita(s)" if self.issues else "nenhuma suspeita")
        if self.truncated:
            parts.append("linha truncada pelo limite de lances")
        return ", ".join(parts) + "."


@dataclass
class ScanFindings:
    """O resultado de uma varredura, amarrado ao texto que o produziu.

    A varredura custa segundos; a reanalise acontece a cada tecla. Sem guardar
    de qual texto os alertas vieram, eles sumiriam no primeiro caractere
    digitado -- ou, pior, ficariam apontando para o lugar errado. Aqui vale a
    mesma ideia do `SegmentDigest` do `parse_worker`: enquanto o **corpo** da
    partida nao mudar, os alertas continuam validos, e uma edicao numa partida
    acima so desloca as posicoes por uma soma.
    """

    segment_body: str
    raw_offset: int
    issues: list[Issue] = field(default_factory=list)

    def matches(self, segment_body: str) -> bool:
        return segment_body == self.segment_body

    def shifted_to(self, raw_offset: int) -> list[Issue]:
        delta = raw_offset - self.raw_offset
        if delta == 0:
            return list(self.issues)
        return [
            replace(issue, raw_start=issue.raw_start + delta, raw_end=issue.raw_end + delta)
            for issue in self.issues
        ]


def is_annotated_as_bad(node: MoveNode) -> bool:
    """O livro ja disse que o lance e ruim?"""
    if any(nag in BAD_MOVE_NAGS for nag in node.nags):
        return True
    # `Nd5?` pode chegar com o sinal colado no proprio token bruto.
    return "?" in (node.raw_text or "")


def iter_mainline(ast: GameAST, start_fen: str) -> Iterator[tuple[MoveNode, chess.Board, chess.Board]]:
    """`(no, tabuleiro antes, tabuleiro depois)` da linha principal.

    Para no primeiro lance que nao encaixa: dali para frente a posicao e ficcao,
    e avaliar ficcao so produziria alerta falso. O lance quebrado ja tem o seu
    proprio alerta, vindo do parser.
    """
    try:
        board = chess.Board(start_fen or chess.STARTING_FEN)
    except ValueError:
        return

    for node in ast.moves:
        if node.is_null_move:
            board.push(chess.Move.null())
            continue
        if not node.is_valid:
            return

        before = board.copy(stack=False)
        try:
            board.push_san(node.display_san)
        except (ValueError, AssertionError):
            return

        yield node, before, board.copy(stack=False)


def _memoized(evaluate: Evaluator) -> tuple[Evaluator, dict[str, int]]:
    """A posicao depois do lance N e a posicao antes do lance N+1.

    Sem a memoria, uma varredura de 40 lances pagaria 80 avaliacoes em vez de 41.
    """
    cache: dict[str, int] = {}

    def cached(board: chess.Board) -> int:
        key = board.fen()
        if key not in cache:
            cache[key] = evaluate(board)
        return cache[key]

    return cached, cache


def repair_candidates(
    move: ScannedMove,
    evaluate: Evaluator,
    settings: EngineSettings,
) -> list[Candidate]:
    """Lances parecidos com o jogado que **nao** derrubam a avaliacao.

    E aqui que a suspeita vira conserto: se o livro escreveu `Cd1` e o OCR leu
    `Cd5`, o lance certo esta a um caractere de distancia e o motor confirma que
    ele nao perde nada. Se houver mais de um assim, o programa **nao escolhe** --
    lista os dois com confianca baixa e deixa a decisao com quem esta lendo o
    livro (SPEC 0, principio 3).
    """
    played = move.san
    board = move.board_before

    nearby: list[tuple[str, chess.Move]] = []
    for legal in board.legal_moves:
        san = board.san(legal)
        if san == played:
            continue
        if Levenshtein.distance(san, played) > 1:
            continue
        nearby.append((san, legal))

    if not nearby or len(nearby) > settings.repair_max_alternatives:
        return []

    safe: list[tuple[int, str]] = []
    for san, legal in nearby:
        board.push(legal)
        try:
            after = evaluate(board)
        finally:
            board.pop()

        drop = move.eval_before_cp + after
        if drop < settings.threshold_cp:
            safe.append((drop, san))

    if not safe:
        return []

    safe.sort()
    if len(safe) == 1:
        drop, san = safe[0]
        return [
            Candidate(
                san=san,
                confidence=0.75,
                source="motor",
                rationale=(
                    f"a um caractere de '{played}', e o unico que o motor nao ve "
                    f"perdendo material ({drop / 100:+.1f})"
                ),
            )
        ]

    others = ", ".join(san for _drop, san in safe[:3])
    return [
        Candidate(
            san=san,
            confidence=0.40,
            source="motor",
            rationale=f"a um caractere de '{played}'; empatado com {others}",
        )
        for _drop, san in safe[:3]
    ]


def _blunder_issue(move: ScannedMove, candidates: list[Candidate]) -> Issue:
    pawns = move.drop_cp / 100
    message = (
        f"'{move.san}' derruba a avaliacao em {pawns:.1f} peao(oes) e o livro nao "
        f"marca com '?'. Provavel erro de leitura."
    )
    if candidates and candidates[0].confidence >= 0.60:
        message += f" O motor sugere '{candidates[0].san}'."

    actions = [ACTION_IGNORE]
    # So oferece gravar no texto bruto quando a proposta e unica; havendo empate,
    # o alerta informa e nao conserta.
    if candidates and candidates[0].confidence >= 0.60:
        actions.insert(0, ACTION_ACCEPT)

    return Issue(
        severity="warning",
        code=SUSPECT_BLUNDER,
        message=message,
        raw_start=move.node.san_start_index,
        raw_end=move.node.san_end_index,
        raw_text=move.node.raw_text,
        candidates=candidates,
        actions=actions,
    )


def scan_mainline(
    ast: GameAST,
    start_fen: str,
    evaluate: Evaluator,
    settings: EngineSettings | None = None,
) -> ScanReport:
    """Percorre a linha principal e devolve as suspeitas de erro de leitura."""
    settings = settings or EngineSettings()
    report = ScanReport()

    steps = []
    for step in iter_mainline(ast, start_fen):
        if len(steps) >= settings.max_moves:
            report.truncated = True
            break
        steps.append(step)

    if not steps:
        return report

    cached_evaluate, cache = _memoized(evaluate)

    ply = 0
    for node, before, after in steps:
        ply += 1
        report.scanned_moves += 1

        move = ScannedMove(
            node=node,
            ply=ply,
            san=node.display_san,
            board_before=before,
            eval_before_cp=cached_evaluate(before),
            eval_after_cp=cached_evaluate(after),
        )

        if move.drop_cp < settings.threshold_cp:
            continue
        if is_annotated_as_bad(node):
            continue
        if move.eval_before_cp <= -settings.lost_position_cp:
            # Ja estava perdido antes do lance: a queda nao diz nada sobre a leitura.
            continue

        report.issues.append(_blunder_issue(move, repair_candidates(move, cached_evaluate, settings)))

    report.evaluated_positions = len(cache)
    return report


# ----------------------------------------------------------------------
# Fora da thread de UI
# ----------------------------------------------------------------------


class _ScanRunner(QObject):
    """Vive na thread de trabalho. Nunca toca em widget."""

    finished_signal = Signal(object)

    @Slot(object)
    def run_scan(self, payload) -> None:
        request_id, ast, start_fen, settings = payload
        try:
            with EngineSession(settings) as session:
                report = scan_mainline(ast, start_fen, session.evaluate, settings)
        except EngineUnavailable as exc:
            self.finished_signal.emit((request_id, None, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001 - o motor e externo; nao pode derrubar a janela
            self.finished_signal.emit((request_id, None, f"Falha do motor: {exc}"))
            return

        self.finished_signal.emit((request_id, report, ""))


class EngineScanner(QObject):
    """Fachada da varredura: dispara na thread de trabalho, responde por sinal.

    A thread so nasce na primeira varredura -- quem nunca usa o motor nao paga
    thread nenhuma. Pedidos antigos que chegarem atrasados sao descartados.
    """

    finished_signal = Signal(object)  # ScanReport
    failed_signal = Signal(str)

    _request_signal = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._runner: _ScanRunner | None = None
        self._request_id = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return

        self._thread = QThread()
        self._runner = _ScanRunner()
        self._runner.moveToThread(self._thread)
        self._request_signal.connect(self._runner.run_scan)
        self._runner.finished_signal.connect(self._on_finished)
        self._thread.start()

    def scan(self, ast: GameAST, start_fen: str, settings: EngineSettings) -> None:
        self._request_id += 1
        self._running = True
        self._ensure_thread()
        self._request_signal.emit((self._request_id, ast, start_fen, settings))

    @Slot(object)
    def _on_finished(self, payload) -> None:
        request_id, report, error = payload
        if request_id != self._request_id:
            # Pedido ja substituido por outro: descartar em silencio.
            return

        self._running = False
        if report is None:
            self.failed_signal.emit(error)
        else:
            self.finished_signal.emit(report)

    def shutdown(self) -> None:
        if self._thread is None:
            return

        thread, runner = self._thread, self._runner
        self._thread, self._runner = None, None
        self._running = False

        thread.quit()
        thread.wait(5000)
        if runner is not None:
            runner.deleteLater()
        thread.deleteLater()
