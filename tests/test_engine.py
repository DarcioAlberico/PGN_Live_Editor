"""Fase 5: o motor desconfia por voce.

O alerta `SUSPECT_BLUNDER` existe porque um lance mal lido pelo OCR tambem e um
lance **legal** -- e por isso passa por todas as outras verificacoes do
programa. O que o denuncia e a avaliacao despencar sem o livro ter marcado o
lance com '?'.

Nenhum teste aqui precisa de Stockfish instalado. O modulo recebe uma *funcao*
de avaliacao, e o que entra e uma busca material de dois lances -- deterministica
e suficiente para enxergar uma peca pendurada, que e exatamente o caso que o
alerta persegue.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import chess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pgn_live_editor.core.issues import (  # noqa: E402
    ACTION_ACCEPT,
    SUSPECT_BLUNDER,
)
from pgn_live_editor.core.normalizer import LiveTextNormalizer  # noqa: E402
from pgn_live_editor.core.parser import TolerantParser  # noqa: E402
from pgn_live_editor.core.tokenizer import PGNTokenizer  # noqa: E402
from pgn_live_editor.services.engine_service import (  # noqa: E402
    ENGINE_ENV_VAR,
    EngineSession,
    EngineSettings,
    EngineUnavailable,
    ScanFindings,
    discover_engine_path,
    engine_is_available,
    is_annotated_as_bad,
    iter_mainline,
    scan_mainline,
)

PIECE_VALUE = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 320,
    chess.ROOK: 500,
    chess.QUEEN: 900,
}

# Cavalo em c3 pendurado se for a d5 (peoes em c6 e e6) ou a b5 (peao em a6).
# A unica casa segura a um caractere de `Nd5` e `Nd1`.
HANGING_KNIGHT_FEN = "6k1/8/p1p1p3/8/8/2N5/8/6K1 w - - 0 1"


def material_balance(board: chess.Board) -> int:
    """Material em centipeoes, do ponto de vista de quem tem a vez."""
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        total += value * len(board.pieces(piece_type, chess.WHITE))
        total -= value * len(board.pieces(piece_type, chess.BLACK))
    return total if board.turn == chess.WHITE else -total


def shallow_search(board: chess.Board, depth: int = 2) -> int:
    """Negamax material de profundidade fixa: ve uma peca pendurada, so isso.

    Faz o papel do motor nos testes. Nao precisa ser bom -- precisa ser
    deterministico e enxergar a captura de resposta.
    """
    if depth == 0 or board.is_game_over():
        return material_balance(board)

    best = None
    for move in board.legal_moves:
        board.push(move)
        score = -shallow_search(board, depth - 1)
        board.pop()
        if best is None or score > best:
            best = score

    return best if best is not None else material_balance(board)


def parse_text(text: str, start_fen: str = chess.STARTING_FEN):
    normalized = LiveTextNormalizer(locale="pt").normalize_with_mapping(text)
    tokens = PGNTokenizer().tokenize(normalized)
    return TolerantParser(locale="pt").parse(tokens, start_fen=start_fen)


class BlunderDetectionTests(unittest.TestCase):
    """A regra central: queda grande + livro calado = suspeita de leitura."""

    def setUp(self):
        self.settings = EngineSettings(threshold_cp=250)

    def test_hanging_a_piece_without_question_mark_raises_the_alert(self):
        ast = parse_text("1. Nd5", start_fen=HANGING_KNIGHT_FEN)
        report = scan_mainline(ast, HANGING_KNIGHT_FEN, shallow_search, self.settings)

        self.assertEqual(len(report.issues), 1, report.summary())
        issue = report.issues[0]
        self.assertEqual(issue.code, SUSPECT_BLUNDER)
        self.assertEqual(issue.severity, "warning")
        self.assertIn("Nd5", ast.moves[0].display_san)
        # O trecho apontado e o proprio lance no texto bruto.
        self.assertEqual("1. Nd5"[issue.raw_start : issue.raw_end], "Nd5")

    def test_book_marked_the_move_as_bad_so_the_engine_stays_quiet(self):
        """`Nd5?` e erro do mestre comentado pelo livro, nao erro de leitura."""
        ast = parse_text("1. Nd5?", start_fen=HANGING_KNIGHT_FEN)
        report = scan_mainline(ast, HANGING_KNIGHT_FEN, shallow_search, self.settings)

        self.assertTrue(is_annotated_as_bad(ast.moves[0]))
        self.assertEqual(report.issues, [])
        self.assertEqual(report.scanned_moves, 1)

    def test_double_question_mark_is_also_respected(self):
        ast = parse_text("1. Nd5??", start_fen=HANGING_KNIGHT_FEN)
        report = scan_mainline(ast, HANGING_KNIGHT_FEN, shallow_search, self.settings)
        self.assertEqual(report.issues, [])

    def test_a_sound_opening_produces_no_alert(self):
        ast = parse_text("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7")
        report = scan_mainline(ast, chess.STARTING_FEN, shallow_search, self.settings)

        self.assertEqual(report.scanned_moves, 10)
        self.assertEqual(report.issues, [], [issue.message for issue in report.issues])

    def test_already_lost_position_does_not_generate_noise(self):
        """Numa posicao ja perdida qualquer lance 'derruba' a avaliacao."""
        lost_fen = "6k1/8/8/8/8/8/5qpp/6K1 w - - 0 1"
        ast = parse_text("1. Kh1", start_fen=lost_fen)
        report = scan_mainline(ast, lost_fen, shallow_search, EngineSettings(threshold_cp=100))
        self.assertEqual(report.issues, [])

    def test_scan_stops_at_the_first_move_that_does_not_fit(self):
        """Depois de um lance quebrado a posicao e ficcao; avaliar so daria alerta falso."""
        ast = parse_text("1. e4 e5 2. Nf3 zzz9 3. Bb5")
        steps = list(iter_mainline(ast, chess.STARTING_FEN))

        # e4, e5, Nf3 -- e para no `zzz9`, que ja tem o alerta dele vindo do parser.
        self.assertEqual([node.display_san for node, _before, _after in steps], ["e4", "e5", "Nf3"])

    def test_positions_are_evaluated_once_each(self):
        """A posicao depois do lance N e a posicao antes do N+1."""
        calls: list[str] = []

        def counting(board: chess.Board) -> int:
            calls.append(board.fen())
            return material_balance(board)

        ast = parse_text("1. e4 e5 2. Nf3 Nc6")
        report = scan_mainline(ast, chess.STARTING_FEN, counting, self.settings)

        self.assertEqual(report.scanned_moves, 4)
        # 4 lances = 5 posicoes, nao 8.
        self.assertEqual(len(calls), 5)
        self.assertEqual(report.evaluated_positions, 5)

    def test_max_moves_truncates_and_says_so(self):
        ast = parse_text("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6")
        report = scan_mainline(ast, chess.STARTING_FEN, shallow_search, EngineSettings(max_moves=2))
        self.assertEqual(report.scanned_moves, 2)
        self.assertTrue(report.truncated)
        self.assertIn("truncada", report.summary())


class RepairCandidateTests(unittest.TestCase):
    """A suspeita so vira conserto quando a proposta e unica."""

    def test_unique_nearby_safe_move_becomes_an_acceptable_candidate(self):
        ast = parse_text("1. Nd5", start_fen=HANGING_KNIGHT_FEN)
        report = scan_mainline(ast, HANGING_KNIGHT_FEN, shallow_search, EngineSettings(threshold_cp=250))

        issue = report.issues[0]
        self.assertEqual([candidate.san for candidate in issue.candidates], ["Nd1"])
        self.assertEqual(issue.candidates[0].source, "motor")
        self.assertGreaterEqual(issue.candidates[0].confidence, 0.60)
        # Proposta unica: o alerta pode gravar no texto bruto.
        self.assertIn(ACTION_ACCEPT, issue.actions)
        self.assertIn("Nd1", issue.message)

    def test_several_safe_neighbours_are_listed_but_never_applied(self):
        """Empate nao vira correcao: o programa duvida, nao adivinha (SPEC 0.3)."""
        # Dama em d1 com a coluna d inteira livre: Qd2..Qd8 sao todos vizinhos
        # a um caractere de `Qd5`, e quase todos seguros.
        fen = "7k/8/2p1p3/8/8/8/8/3QK3 w - - 0 1"
        ast = parse_text("1. Qd5", start_fen=fen)
        report = scan_mainline(ast, fen, shallow_search, EngineSettings(threshold_cp=250))

        self.assertEqual(len(report.issues), 1)
        issue = report.issues[0]
        self.assertNotIn(ACTION_ACCEPT, issue.actions)
        for candidate in issue.candidates:
            self.assertLess(candidate.confidence, 0.60)

    def test_no_nearby_alternative_means_no_candidate(self):
        """Sem vizinho parecido e seguro, o alerta informa e nao propoe nada."""
        # `b4` cai em axb4; o unico vizinho a um caractere, `b3`, cai em cxb3.
        fen = "6k1/8/8/p7/2p5/8/1P6/6K1 w - - 0 1"
        ast = parse_text("1. b4", start_fen=fen)
        report = scan_mainline(ast, fen, shallow_search, EngineSettings(threshold_cp=100))

        self.assertEqual(len(report.issues), 1)
        issue = report.issues[0]
        self.assertEqual(issue.candidates, [])
        self.assertNotIn(ACTION_ACCEPT, issue.actions)


class ScanFindingsTests(unittest.TestCase):
    """A varredura custa segundos; a reanalise acontece a cada tecla."""

    def setUp(self):
        ast = parse_text("1. Nd5", start_fen=HANGING_KNIGHT_FEN)
        report = scan_mainline(ast, HANGING_KNIGHT_FEN, shallow_search, EngineSettings(threshold_cp=250))
        self.findings = ScanFindings(segment_body="1. Nd5", raw_offset=0, issues=report.issues)

    def test_untouched_game_keeps_its_alerts(self):
        self.assertTrue(self.findings.matches("1. Nd5"))
        self.assertEqual(len(self.findings.shifted_to(0)), 1)

    def test_edited_game_drops_them(self):
        self.assertFalse(self.findings.matches("1. Nd1"))

    def test_a_game_pushed_down_the_document_only_shifts(self):
        """Digitar numa partida acima nao invalida a varredura desta."""
        original = self.findings.issues[0]
        moved = self.findings.shifted_to(120)[0]

        self.assertEqual(moved.raw_start, original.raw_start + 120)
        self.assertEqual(moved.raw_end, original.raw_end + 120)
        self.assertEqual(moved.message, original.message)
        # O deslocamento nao pode alterar o alerta guardado.
        self.assertEqual(self.findings.issues[0].raw_start, original.raw_start)


class EngineDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get(ENGINE_ENV_VAR)
        os.environ.pop(ENGINE_ENV_VAR, None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop(ENGINE_ENV_VAR, None)
        else:
            os.environ[ENGINE_ENV_VAR] = self._saved

    def test_a_path_that_does_not_exist_is_not_accepted(self):
        self.assertNotEqual(discover_engine_path("C:/não/existe/stockfish.exe"), "C:/não/existe/stockfish.exe")

    def test_environment_variable_is_honoured(self):
        # Qualquer arquivo serve para o teste: o que se verifica e a ordem de busca.
        this_file = os.path.abspath(__file__)
        os.environ[ENGINE_ENV_VAR] = this_file
        self.assertEqual(discover_engine_path(""), this_file)

    def test_settings_round_trip_through_json(self):
        settings = EngineSettings(path="stockfish", movetime_ms=350, threshold_cp=250)
        restored = EngineSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.path, "stockfish")
        self.assertEqual(restored.movetime_ms, 350)
        self.assertEqual(restored.threshold_cp, 250)

    def test_garbage_settings_fall_back_to_the_defaults(self):
        self.assertEqual(EngineSettings.from_dict({"movetime_ms": 0}).movetime_ms, EngineSettings().movetime_ms)
        self.assertEqual(EngineSettings.from_dict([]), EngineSettings())


class EngineSessionTests(unittest.TestCase):
    """Sem motor instalado o programa segue inteiro -- so a varredura nao acontece."""

    def setUp(self):
        self._saved = os.environ.pop(ENGINE_ENV_VAR, None)
        self.addCleanup(self._restore)
        # `which` fica fixo para o teste valer tanto na maquina com Stockfish
        # quanto na sem.
        patcher = mock.patch("pgn_live_editor.services.engine_service.shutil.which", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        if self._saved is not None:
            os.environ[ENGINE_ENV_VAR] = self._saved

    def test_opening_without_an_engine_says_what_to_do(self):
        session = EngineSession(EngineSettings(path=""))
        with self.assertRaises(EngineUnavailable) as raised:
            session.open()

        self.assertIn(ENGINE_ENV_VAR, str(raised.exception))
        self.assertFalse(engine_is_available())

    def test_evaluating_a_closed_session_is_an_error_not_a_crash(self):
        with self.assertRaises(EngineUnavailable):
            EngineSession().evaluate(chess.Board())

    def test_closing_a_session_that_never_opened_is_harmless(self):
        EngineSession().close()


if __name__ == "__main__":
    unittest.main()
