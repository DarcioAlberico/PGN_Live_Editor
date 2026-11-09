"""A decisão de orientação, separada da inferência (S-48).

**Por que isto é um módulo e não um `if`.** A cascata de orientação já mudou uma vez, e vai
mudar de novo. A S-13 supôs que a legalidade decidiria; a medição mostrou que ela só decide
em 16% dos casos, e que a confiança -- que acerta 320 de 320 em leitura boa -- vira ruído
quando a leitura é ruim. Cada uma dessas descobertas exigia editar uma função de 112 linhas
que também fazia a inferência, o que significa que medir uma regra isolada custava recortar
código à mão.

Aqui a cascata é uma tupla de objetos. Cada regra responde a uma pergunta e cala quando não
sabe; a **ordem** é a decisão, e `explain()` diz o que cada uma disse sobre um diagrama
específico. Medir uma regra sozinha contra o conjunto de campo passa a ser trocar a tupla.

A tabela que justifica a ordem, medida no split de teste (320 tabuleiros):

| sinal              | acerta | erra | empata |
|--------------------|--------|------|--------|
| legalidade         |     52 |    0 |    268 |
| `min_confidence`   |    320 |    0 |      0 |
| peões nas filas    |    264 |    9 |     47 |
| reis nas filas     |    267 |   37 |     16 |

O prior de **reis** que a S-13 sugeria erra 37 vezes em 320 e ficou fora -- e continua fora,
o que é informação e não omissão.

A tabela acima, porém, só descreve leitura boa. Medido depois no `1937 Kemeri.pdf`, a
confiança **para de decidir** quando a leitura é ruim: em duas páginas cuja leitura de pé era
claramente correta, as duas orientações saíram com confiança ~0,04 e margens de 0,001 e 0,019
-- ruído, e seguir a margem girava o diagrama errado. O prior de peões, que olha a estrutura
da posição e não a aparência das peças, continuava informativo nos mesmos casos (+3,8 contra
-4,2). É por isso que ele vem **depois** da margem e não antes: ele é a regra do regime de
leitura ruim, e no regime bom a confiança é melhor que ele.

**O que isto não resolve:** diagrama impresso do ponto de vista das pretas. Ali as peças
estão desenhadas para cima e o que muda é o mapeamento casa→índice, não os pixels; girar a
imagem estragaria a leitura. O sinal que resolveria são as coordenadas das bordas, e é o que
`CoordinateRule` lê. Ver a ressalva dela sobre a medição da S-45.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .config import ORIENTATION_DECISIVE_MARGIN, ORIENTATION_PAWN_PRIOR_MARGIN
from .fen_utils import pawn_direction_score

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from .inference import BoardPrediction

__all__ = [
    "BoardCoordinates",
    "ConfidenceMarginRule",
    "CoordinateRule",
    "OrientationEvidence",
    "OrientationPolicy",
    "OrientationRule",
    "OrientationVerdict",
    "OrientedPrediction",
    "PawnPriorRule",
    "SingleLegalRule",
    "TightMarginFallback",
]


@dataclass(frozen=True, eq=False)
class OrientedPrediction:
    """Leitura de um diagrama junto com a orientação escolhida para ela (S-13).

    Mora aqui, e não em `inference`, porque é o **resultado da decisão** e não o resultado da
    inferência. `inference` reexporta o nome para não quebrar os chamadores.
    """

    prediction: BoardPrediction
    rotation: int
    """0 ou 180 graus, aplicado à imagem antes de reconhecer."""

    margin: float
    """`min_confidence` da orientação escolhida menos a da descartada.

    Sempre ≥ 0 quando a escolha foi por confiança. É a medida de quão folgada foi."""

    ambiguous: bool
    """A escolha foi apertada, ou os dois sinais discordaram: vale olho humano."""

    reason: str
    """Por que esta orientação venceu, em pt-BR, para a UI e o arquivo de revisão."""

    alternative: BoardPrediction | None = None
    """A leitura descartada. `None` quando a orientação foi imposta, não escolhida."""


@dataclass(frozen=True)
class BoardCoordinates:
    """As coordenadas impressas na borda do diagrama, quando alguém as tiver lido (S-45).

    `ranks_top_to_bottom` é o que a coluna da esquerda diz, de cima para baixo:
    `(8, 7, 6, 5, 4, 3, 2, 1)` é ponto de vista das brancas, `(1, 2, ..., 8)` é das pretas.

    **Nada no projeto constrói isto hoje, e é de propósito.** A S-45 foi medida em 2026-08-09
    e adiada: coordenadas legíveis na camada de texto em 52 de 380 diagramas (13,7%), das
    quais 48 do `Polgar`, que já lê a 1,000 -- e dos 49 conclusivos, 49 apontam ponto de vista
    das brancas e nenhum das pretas. A pendência que a regra existe para fechar não apareceu
    uma vez no acervo. O tipo e a regra ficam porque o custo é vinte linhas e porque isso
    transforma a S-45, quando ela voltar, num problema de **produzir o dado** em vez de um
    problema de mexer na política.
    """

    ranks_top_to_bottom: tuple[int, ...]

    @property
    def white_point_of_view(self) -> bool | None:
        """`True`/`False` quando a leitura é conclusiva, `None` quando ela não diz nada."""
        ranks = self.ranks_top_to_bottom
        if len(ranks) < 2:
            return None
        if list(ranks) == sorted(ranks, reverse=True):
            return True
        if list(ranks) == sorted(ranks):
            return False
        return None


@dataclass(frozen=True)
class OrientationEvidence:
    """Tudo o que as regras podem olhar, e nada além disso.

    As regras não recebem o modelo, o dispositivo nem a imagem: recebem as duas leituras
    já feitas. É o que permite exercitá-las com `BoardPrediction` sintético e sem torch.
    """

    upright: BoardPrediction
    flipped: BoardPrediction
    coordinates: BoardCoordinates | None = None

    @property
    def margin(self) -> float:
        """`min_confidence` de pé menos a de cabeça para baixo. Positivo favorece de pé."""
        return self.upright.min_confidence - self.flipped.min_confidence

    @property
    def legal_upright(self) -> bool:
        return not self.upright.position.is_fatal

    @property
    def legal_flipped(self) -> bool:
        return not self.flipped.position.is_fatal

    @property
    def pawn_gap(self) -> float | None:
        """Quanto o prior de peões prefere a leitura de pé. `None` se ele não se aplica."""
        score_upright = pawn_direction_score(self.upright.class_indices)
        score_flipped = pawn_direction_score(self.flipped.class_indices)
        if score_upright is None and score_flipped is None:
            return None
        # Uma leitura sem peão de alguma cor não pontua; tratá-la como 0 é justo, porque o
        # outro lado é que está afirmando algo sobre a estrutura.
        return (score_upright or 0.0) - (score_flipped or 0.0)


@dataclass(frozen=True)
class OrientationVerdict:
    """O que uma regra respondeu.

    A spec da S-48 desenhava isto como `tuple[bool, str]`. Um terceiro campo entrou porque
    `ambiguous` é **por regra** e a tupla o perderia: uma legalidade que discorda da confiança
    é ambígua por definição, uma margem decisiva não é, e o desempate final é ambíguo sempre.
    Derivá-lo fora da regra exigiria que a política soubesse qual regra decidiu -- que é
    exatamente o acoplamento que este item desfaz.
    """

    upright: bool
    reason: str
    ambiguous: bool = False


class OrientationRule(Protocol):
    """Uma pergunta sobre a orientação. Cala quando não sabe responder.

    `name` é propriedade só de leitura de propósito: as regras são `dataclass(frozen=True)`,
    e um membro de protocolo declarado como atributo mutável as recusaria -- uma regra com
    estado seria uma regra cujo resultado depende de quantas vezes foi chamada.
    """

    @property
    def name(self) -> str:
        """Como a regra aparece em `explain()` e no painel de diagnóstico."""
        ...

    def decide(self, ev: OrientationEvidence) -> OrientationVerdict | None:
        """O veredito da regra, ou `None` quando ela não tem o que dizer sobre este diagrama."""
        ...


@dataclass(frozen=True)
class CoordinateRule:
    """As coordenadas da borda, quando alguém as leu (S-45).

    Primeira da cascata porque, quando responde, **responde**: as coordenadas são a única
    evidência direta do ponto de vista, e não um prior sobre a posição. Hoje ela cala em
    100% dos diagramas, porque nada produz `BoardCoordinates` -- ver a ressalva do tipo.
    """

    name: str = "coordenadas"

    def decide(self, ev: OrientationEvidence) -> OrientationVerdict | None:
        if ev.coordinates is None:
            return None
        white_pov = ev.coordinates.white_point_of_view
        if white_pov is None:
            return None
        return OrientationVerdict(
            upright=white_pov,
            reason="coordenadas da borda" + ("" if white_pov else " (ponto de vista das pretas)"),
        )


@dataclass(frozen=True)
class SingleLegalRule:
    """Uma orientação ilegal e a outra não → a legal.

    Primeira das regras medidas porque, quando decide, **nunca errou** em 320 tabuleiros -- e
    uma leitura ilegal é pior que uma de confiança baixa. Ela cala nos 84% em que as duas são
    legais, o que é o motivo de a S-13 ter se enganado ao supô-la dominante: girar 180° manda
    peão branco da fila `r` para a `9-r`, e 2..7 vira 7..2, o que continua legal.
    """

    name: str = "legalidade"

    def decide(self, ev: OrientationEvidence) -> OrientationVerdict | None:
        if ev.legal_upright == ev.legal_flipped:
            return None
        upright = ev.legal_upright
        return OrientationVerdict(
            upright=upright,
            reason="única orientação legal",
            # Discordância entre sinais nunca apareceu na medição, mas se aparecer é
            # exatamente o que "ambíguo" quer dizer -- e não algo para resolver em silêncio.
            ambiguous=(ev.margin > 0) != upright,
        )


@dataclass(frozen=True)
class ConfidenceMarginRule:
    """Margem de confiança mínima ≥ `decisive_margin` → a mais confiante.

    Acerta 320 de 320 quando a leitura é boa. O limiar existe porque abaixo dele a margem
    deixa de ser sinal -- ver o caso do `Kemeri` no docstring do módulo.
    """

    decisive_margin: float = ORIENTATION_DECISIVE_MARGIN
    name: str = "margem de confiança"

    def decide(self, ev: OrientationEvidence) -> OrientationVerdict | None:
        margin = ev.margin
        if abs(margin) < self.decisive_margin:
            return None
        return OrientationVerdict(upright=margin > 0, reason=f"maior confiança mínima (margem {abs(margin):.3f})")


@dataclass(frozen=True)
class PawnPriorRule:
    """A estrutura de peões, para o regime em que a confiança empatou baixo.

    Erra 9 vezes em 320 no regime bom, o que é pior que a margem -- por isso vem depois dela.
    No regime ruim é a única coisa que ainda sabe algo, porque olha a posição e não a
    aparência das peças.
    """

    pawn_prior_margin: float = ORIENTATION_PAWN_PRIOR_MARGIN
    name: str = "prior de peões"

    def decide(self, ev: OrientationEvidence) -> OrientationVerdict | None:
        gap = ev.pawn_gap
        if gap is None or abs(gap) < self.pawn_prior_margin:
            return None
        return OrientationVerdict(
            upright=gap > 0,
            reason=f"peões apontam a orientação (vantagem {abs(gap):.1f} filas, confiança empatada)",
        )


@dataclass(frozen=True)
class TightMarginFallback:
    """O desempate quando nenhuma regra soube. Nunca cala, e sempre marca `ambiguous`.

    Escolher a de maior confiança aqui não é uma decisão sobre a orientação: é a menos ruim
    das duas, dita em voz alta. Quem lê `ambiguous=True` sabe que vale olho humano.
    """

    name: str = "desempate"

    def decide(self, ev: OrientationEvidence) -> OrientationVerdict | None:
        margin = ev.margin
        tem_peoes = ev.pawn_gap is not None
        return OrientationVerdict(
            upright=margin >= 0,
            reason=(
                f"margem apertada ({abs(margin):.3f}) e peões não decidem"
                if tem_peoes
                else f"margem apertada ({abs(margin):.3f}) e sem peões dos dois lados"
            ),
            ambiguous=True,
        )


class OrientationPolicy:
    """Cascata ordenada de regras. A ordem é a decisão, e ela é medível regra a regra.

    `DEFAULT` reproduz exatamente a cascata que `predict_with_orientation` tinha embutida,
    mais `CoordinateRule` na frente -- que hoje cala sempre, porque nada produz o dado dela.
    """

    DEFAULT: tuple[OrientationRule, ...] = (
        CoordinateRule(),
        SingleLegalRule(),
        ConfidenceMarginRule(),
        PawnPriorRule(),
        TightMarginFallback(),
    )

    def __init__(self, rules: tuple[OrientationRule, ...] | None = None) -> None:
        self.rules = self.DEFAULT if rules is None else rules
        if not self.rules:
            raise ValueError("Uma política de orientação precisa de pelo menos uma regra.")

    def decide(self, ev: OrientationEvidence) -> tuple[OrientationRule, OrientationVerdict]:
        """A primeira regra que não cala, e o que ela disse."""
        for rule in self.rules:
            verdict = rule.decide(ev)
            if verdict is not None:
                return rule, verdict
        raise ValueError(
            "Nenhuma regra decidiu a orientação. A última regra da cascata tem de ser um "
            "desempate que nunca cala -- ver TightMarginFallback."
        )

    def resolve(self, ev: OrientationEvidence) -> OrientedPrediction:
        """A leitura escolhida, com a orientação, a margem e o motivo em pt-BR."""
        _rule, verdict = self.decide(ev)
        chosen, discarded = (ev.upright, ev.flipped) if verdict.upright else (ev.flipped, ev.upright)
        return OrientedPrediction(
            prediction=chosen,
            rotation=0 if verdict.upright else 180,
            margin=abs(ev.margin),
            ambiguous=verdict.ambiguous,
            reason=verdict.reason,
            alternative=discarded,
        )

    def explain(self, ev: OrientationEvidence) -> list[tuple[str, str | None]]:
        """O que **cada** regra disse, na ordem, inclusive as que calaram.

        Para o painel de diagnóstico e para medir uma regra isolada contra o conjunto de
        campo da S-41: `None` é "calou", e o texto é o motivo que ela daria se decidisse.
        Ao contrário de `resolve`, não para na primeira que responde.
        """
        return [(rule.name, verdict.reason if (verdict := rule.decide(ev)) else None) for rule in self.rules]


DEFAULT_POLICY = OrientationPolicy()
"""A cascata em produção. Instanciada uma vez porque as regras não têm estado."""
