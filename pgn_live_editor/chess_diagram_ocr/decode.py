"""Decodificação de um tabuleiro sujeita às regras do xadrez (S-11).

O argmax por casa decide 64 vezes de forma independente, então nada impede que o
resultado tenha dois reis brancos ou um peão na oitava fila. Nos PDFs medidos isso não é
teórico: em fonte figurina alemã, K e Q se confundem sistematicamente e o resultado sai
sem rei branco.

A informação para corrigir já está na matriz de probabilidades -- o argmax é que a joga
fora. Aqui a decodificação vira uma busca: qual é a atribuição de **maior probabilidade**
entre as que respeitam as regras? Como o argmax é o ótimo irrestrito, a resposta é sempre
"o argmax com o menor número de trocas mais baratas possível".

As restrições são todas verificáveis sem saber o lado a jogar -- que o diagrama de livro
não carrega. Xeque não entra aqui por isso (ver `fen_utils`, S-05).
"""

from __future__ import annotations

import heapq
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from .config import PIECE_CLASSES, PIECE_TO_IDX
from .fen_utils import fen_from_class_indices

_EPS = 1e-12

EMPTY = PIECE_TO_IDX["empty"]
WHITE_KING = PIECE_TO_IDX["K"]
BLACK_KING = PIECE_TO_IDX["k"]
WHITE_PAWN = PIECE_TO_IDX["P"]
BLACK_PAWN = PIECE_TO_IDX["p"]

WHITE_CLASSES = frozenset(PIECE_TO_IDX[name] for name in "PNBRQK")
BLACK_CLASSES = frozenset(PIECE_TO_IDX[name] for name in "pnbrqk")

# Índices em ordem de leitura: 0..7 é a oitava fila, 56..63 é a primeira. Peão em nenhuma
# das duas -- na oitava teria promovido, na primeira não teria como ter chegado.
BACKRANK_SQUARES: tuple[int, ...] = tuple(range(8)) + tuple(range(56, 64))

MAX_PAWNS = 8
MAX_PIECES = 16

# Quantas peças de cada tipo o lado começa tendo. O que passa disso só pode ter vindo de
# promoção, e cada promoção consome um peão.
_INITIAL_COUNT = {"N": 2, "B": 2, "R": 2, "Q": 1}


@dataclass(frozen=True)
class DecodeResult:
    class_indices: list[int]
    fen_board: str

    log_prob: float
    """Log-probabilidade da atribuição escolhida. Sempre ≤ a do argmax."""

    changed_squares: list[tuple[int, int, int]]
    """(casa, classe do argmax, classe final) para cada troca feita."""

    constraints_satisfied: bool
    """False quando a busca esgotou o limite sem achar atribuição legal."""

    remaining_problems: tuple[str, ...] = ()
    """O que continuou violado, quando `constraints_satisfied` é False."""

    @property
    def changed_square_count(self) -> int:
        return len(self.changed_squares)


@dataclass(frozen=True)
class _Violation:
    """Uma regra violada e o conjunto de reparos capaz de resolvê-la.

    `squares` são as casas que uma solução obrigatoriamente precisa alterar (ou, no caso
    de rei faltando, as casas candidatas a virar rei). Ramificar sobre elas é completo:
    nenhuma atribuição legal fica fora do espaço de busca.
    """

    description: str
    squares: tuple[int, ...]
    targets: tuple[int, ...] | None
    """Classes-alvo fixas. `None` significa "qualquer outra classe plausível da casa"."""


def _color_counts(state: tuple[int, ...]) -> Counter[int]:
    return Counter(state)


def _promotion_excess(counts: Counter[int], upper: bool) -> int:
    """Peças a mais do que o início da partida -- cada uma consumiu um peão."""
    excess = 0
    for name, initial in _INITIAL_COUNT.items():
        symbol = name if upper else name.lower()
        excess += max(0, counts[PIECE_TO_IDX[symbol]] - initial)
    return excess


def _find_violations(state: tuple[int, ...]) -> list[_Violation]:
    counts = _color_counts(state)
    violations: list[_Violation] = []

    for color, king in (("branco", WHITE_KING), ("preto", BLACK_KING)):
        total = counts[king]
        if total == 0:
            violations.append(
                _Violation(
                    f"falta o rei {color}",
                    tuple(square for square in range(64) if state[square] != king),
                    (king,),
                )
            )
        elif total > 1:
            violations.append(
                _Violation(
                    f"mais de um rei {color}",
                    tuple(square for square in range(64) if state[square] == king),
                    None,
                )
            )

    on_backrank = tuple(square for square in BACKRANK_SQUARES if state[square] in (WHITE_PAWN, BLACK_PAWN))
    if on_backrank:
        violations.append(_Violation("peão na primeira ou na oitava fila", on_backrank, None))

    for color, pawn in (("brancos", WHITE_PAWN), ("pretos", BLACK_PAWN)):
        if counts[pawn] > MAX_PAWNS:
            violations.append(
                _Violation(
                    f"peões {color} demais",
                    tuple(square for square in range(64) if state[square] == pawn),
                    None,
                )
            )

    for color, classes, upper in (("brancas", WHITE_CLASSES, True), ("pretas", BLACK_CLASSES, False)):
        squares = tuple(square for square in range(64) if state[square] in classes)
        if len(squares) > MAX_PIECES:
            violations.append(_Violation(f"peças {color} demais", squares, None))
            continue

        pawn = WHITE_PAWN if upper else BLACK_PAWN
        if counts[pawn] + _promotion_excess(counts, upper) > MAX_PAWNS:
            violations.append(
                _Violation(
                    f"peças {color} promovidas demais para o número de peões",
                    squares,
                    None,
                )
            )

    return violations


def _alternatives(
    square: int,
    current: int,
    logp: np.ndarray,
    limit: int,
) -> tuple[int, ...]:
    """Classes plausíveis para substituir a atual, da mais provável para a menos.

    Restringir às `limit` melhores é o que mantém a busca barata. `empty` entra sempre:
    é o reparo certo quando a casa é ruído da moldura ou da legenda, e nem por isso o
    modelo lhe dá probabilidade alta.
    """
    order = [int(index) for index in np.argsort(logp[square])[::-1] if int(index) != current]
    chosen = order[:limit]
    if EMPTY != current and EMPTY not in chosen:
        chosen.append(EMPTY)
    return tuple(chosen)


def _candidates(
    state: tuple[int, ...],
    violation: _Violation,
    logp: np.ndarray,
    alternatives_per_square: int,
) -> Iterator[tuple[int, int]]:
    for square in violation.squares:
        current = state[square]
        targets = (
            tuple(target for target in violation.targets if target != current)
            if violation.targets is not None
            else _alternatives(square, current, logp, alternatives_per_square)
        )
        for target in targets:
            yield square, target


def _build_result(
    state: tuple[int, ...],
    base: tuple[int, ...],
    logp: np.ndarray,
    *,
    satisfied: bool,
    problems: tuple[str, ...] = (),
) -> DecodeResult:
    changed = [(square, base[square], state[square]) for square in range(64) if state[square] != base[square]]
    log_prob = float(sum(logp[square, state[square]] for square in range(64)))
    return DecodeResult(
        class_indices=list(state),
        fen_board=fen_from_class_indices(state),
        log_prob=log_prob,
        changed_squares=changed,
        constraints_satisfied=satisfied,
        remaining_problems=problems,
    )


SEGUNDOS_MAXIMOS = 0.5
"""Quanto tempo a busca pode gastar num tabuleiro só (S-358).

**O teto de expansões não é teto de tempo.** Um tabuleiro legal é resolvido em ~0,1 ms -- nem
entra na busca --, e um tabuleiro ilegal gasta as 5.000 expansões inteiras para no fim admitir que
não achou: medido em 3,4 s no relatório da revisão, e **duas vezes por diagrama**, porque o modo
`auto` lê a 0° e a 180°. Sete segundos de janela parada num diagrama que vai ser recusado de
qualquer jeito.

Meio segundo é quatro mil vezes o caso normal e sete vezes menos que o pior medido: nenhum reparo
que hoje termina é cortado por ele, e o que não termina para de custar o que custava."""


def decode_constrained(
    probs: np.ndarray,
    *,
    max_changes: int = 6,
    max_expansions: int = 5000,
    alternatives_per_square: int = 4,
    max_seconds: float = SEGUNDOS_MAXIMOS,
) -> DecodeResult:
    """Atribuição de maior probabilidade que respeita as regras verificáveis do xadrez.

    Busca de custo uniforme sobre trocas: o custo de uma atribuição é a perda de
    log-probabilidade em relação ao argmax, que é sempre ≥ 0 e **não depende do caminho**
    -- o que torna a primeira solução retirada da fila a de maior probabilidade dentro do
    espaço considerado.

    `max_changes` limita quantas casas podem ser reescritas. É uma trava de segurança: se
    a leitura estiver muito errada, reparar 20 casas produziria uma posição legal e
    inventada, pior que admitir a falha. Nesse caso volta o melhor estado parcial com
    `constraints_satisfied=False`.

    **Uma posição reparada aqui nunca é exportada, e isso é aritmética e não tendência (S-132).**

    Uma casa reparada recebeu, por definição, uma classe que **não** era o argmax. A confiança
    reportada para ela é a dessa classe (`inference.prediction_from_probs` troca as confianças
    pelas das classes efetivamente escolhidas, que é o desenho certo — a confiança relatada tem
    de ser a verdade sobre o que foi escolhido). Essa probabilidade é no máximo a segunda maior
    da casa, e a segunda maior **não pode passar de 0,5**: se passasse, a maior também
    passaria, e as duas somariam mais que 1.

    `min_confidence` é o mínimo sobre as 64 casas. Logo, um diagrama com pelo menos uma casa
    reparada tem `min_confidence ≤ 0,5`, e o gate de exportação é
    `ACCEPT_MIN_CONFIDENCE = 0,80`. **Nenhum diagrama reparado passa**, hoje nem com outro
    modelo — o teto de 0,5 não depende de quão bem o classificador vai.

    Não é defeito, é propriedade. Mas ela muda a leitura de duas métricas que o projeto publica
    lado a lado: `cvoff-field` mostra "casas reparadas" ao lado da taxa de exportação, e o
    reparo — uma das entregas centrais da Fase 2 — **não contribui com um único diagrama
    exportado**. Por isso o relatório separa `repaired_exported` de `repaired_blocked` desde a
    S-132: somados, os dois números sugeriam que o reparo estava ajudando a exportar.

    Se um dia se quiser que o reparo chegue ao PGN, a mudança é no **gate** e não aqui — um
    gate que olhasse a confiança das casas *não* reparadas, ou um limiar próprio para posição
    reparada. Mexer no decodificador para elevar a confiança da casa consertada seria mentir
    sobre o que ele sabe.
    """
    probs = np.asarray(probs, dtype=np.float64)
    expected = (64, len(PIECE_CLASSES))
    if probs.shape != expected:
        raise ValueError(f"Esperada matriz {expected}, recebida {probs.shape}.")

    logp = np.log(np.clip(probs, _EPS, None))
    base: tuple[int, ...] = tuple(int(index) for index in probs.argmax(axis=1))

    if not _find_violations(base):
        return _build_result(base, base, logp, satisfied=True)

    heap: list[tuple[float, int, tuple[int, ...]]] = [(0.0, 0, base)]
    visited: set[tuple[int, ...]] = {base}
    counter = 0
    expansions = 0

    best_state = base
    best_key = (len(_find_violations(base)), 0.0)

    prazo = time.monotonic() + max_seconds if max_seconds > 0 else None
    while heap and expansions < max_expansions:
        # **O relógio é conferido a cada 64 expansões**, e não a cada uma: `time.monotonic` custa
        # ~50 ns e o corpo do laço custa pouco mais que isso -- perguntar as horas cinco mil vezes
        # seria pagar pelo cronômetro o que se quer economizar na busca.
        if prazo is not None and expansions % 64 == 0 and time.monotonic() > prazo:
            break
        cost, _, state = heapq.heappop(heap)
        expansions += 1

        violations = _find_violations(state)
        if not violations:
            return _build_result(state, base, logp, satisfied=True)

        key = (len(violations), cost)
        if key < best_key:
            best_key = key
            best_state = state

        changes = sum(1 for square in range(64) if state[square] != base[square])
        if changes >= max_changes:
            continue

        # Ramificar sobre a violação mais restrita primeiro reduz o fator de ramificação
        # sem perder completude: qualquer solução precisa reparar todas elas.
        violation = min(violations, key=lambda item: len(item.squares))

        for square, target in _candidates(state, violation, logp, alternatives_per_square):
            successor = state[:square] + (target,) + state[square + 1 :]
            if successor in visited:
                continue
            visited.add(successor)
            counter += 1
            delta = float(logp[square, state[square]] - logp[square, target])
            heapq.heappush(heap, (cost + delta, counter, successor))

    problems = tuple(violation.description for violation in _find_violations(best_state))
    return _build_result(best_state, base, logp, satisfied=False, problems=problems)
