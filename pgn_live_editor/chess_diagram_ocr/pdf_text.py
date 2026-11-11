"""Contexto textual de um diagrama: lado a jogar e metadados da legenda (S-16).

Medido: **0 dos 3.244 rótulos** têm lado a jogar preto, e toda FEN exportada sai com `w`.
Num livro de táticas metade dos exercícios é "pretas jogam" -- então metade sai errada. A
informação existe: os PDFs têm camada de texto, e o `bbox_pdf` que a S-12 passou a carregar
em cada `DiagramCandidate` permite achar o texto que pertence a *aquele* diagrama.

O que o acervo tem de fato (27 PDFs, 40 páginas amostradas de cada)

| a camada de texto declara o lado a jogar | livros |
|---|---|
| sim, em legenda ao lado de cada diagrama | 3 |
| tem texto, mas sem declarar o lado | 12 |
| não tem camada de texto (scan puro) | 12 |

Ou seja: **o texto resolve a minoria**. O que ele resolve, resolve bem e de graça; o resto
é a S-17. Este módulo existe para nunca inventar -- campo que a página não diz sai `None`.

Onde fica a legenda, medido

| livro | posição | formato |
|---|---|---|
| `400 Quebra-cabeças` | abaixo | `31: Jogada das pretas **` |
| `Schiller` | acima | `5` / `Morphy-De Riviere` / `Paris, 1858` |
| `AAGAARD` | acima | `Hickl - Yusupov` / `Bremen 1998` |
| `Karpov` | acima | `№79. Steinitz - Bird` |
| `Reinfeld 1001` | acima | só o número da página impressa |

A S-16 supunha legenda **abaixo** do diagrama e mandava priorizar esse lado. Na medição
três dos quatro livros com legenda a põem **acima** -- e o lado não é preferência de
apresentação, é o que decide de quem é cada legenda (ver `dominant_placement`).

Quatro armadilhas que a medição impôs ao desenho

1. **Granularidade de linha, não de bloco.** No `Karpov` um único bloco cobre as legendas
   das *duas* colunas (`№79. Steinitz - Bird` e `№80. Steinitz - Mortimer`, x de 82 a 402).
   Associado por bloco, cada diagrama herdaria o adversário do vizinho. As linhas do
   `get_text("dict")` têm bbox próprio e separam as colunas.
2. **Mas a legenda decide junta.** O número, os jogadores e o evento são um bloco só, e
   distribuí-los linha a linha desloca a página inteira em um exercício. A unidade da
   distribuição é o grupo: bloco, quebrado por coluna (ver `assign_lines_to_diagrams`).
3. **Prosa não é legenda.** Rodar os padrões na página inteira produz falso positivo em 8
   livros: "se as brancas jogarem 32 f2×g2", "it was possible for White to play 20.Nd3".
   O que separa não é o padrão, é o formato -- mas cortar toda prosa custa recall no
   `AAGAARD`, que gruda o enunciado no comentário. Ver `_side_from_line`.
4. **Número de página parece número de exercício.** No `Reinfeld 1001` o único texto da
   página é o número impresso, a 16 pt do diagrama -- proximidade o entrega como se fosse o
   exercício. Dois filtros complementares, ambos necessários: faixa de margem (pega o
   cabeçalho do `AAGAARD` a 2% do topo e o rodapé do `Schiller` a 94%) e consecutividade
   local (pega o `Reinfeld`, que fica a 14% da altura e escaparia da margem). Ver
   `running_page_number`.
"""

from __future__ import annotations

import logging
import math
import re
import time
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

import chess
import pymupdf as fitz

from .pdf_io import PdfSource
from .procedencias import DA_CAMADA, DE_TERCEIROS, LineOrigin, SideOrigin, escopo_de_pagina

logger = logging.getLogger(__name__)

Placement = Literal["above", "below", "left", "right", "overlapping"]

# **`LineOrigin` e `SideOrigin` moram em `procedencias.py`, e não aqui** (S-207). Este módulo não
# pode nomear motor nenhum -- é a regra que
# `test_text_recognizer.py::test_a_s43_nao_precisou_saber_que_o_glifo_existe` cobra desde a S-181,
# e um `Literal` com o nome de um motor escrito dentro dele a quebraria tanto quanto um `if`.
#
# O que eles significam continua o mesmo, e está documentado lá: um lado a jogar decidido por um
# motor com 0,62 de confiança não é o mesmo dado que um lido da camada de texto, e o
# `[SideToMoveSource]` do PGN -- que a Fase 3 criou justamente para que um palpite pareça um
# palpite -- precisa distinguir os dois. `*-page-scope` é a declaração que vale para a página
# inteira (`LAS BLANCAS JUEGAN PRIMERO` no topo do `Reinfeld`) e não para um diagrama: vale menos
# que uma legenda e mais que o padrão "brancas".

DEFAULT_RADIUS_PT = 60.0
"""Raio de busca em pontos do PDF. As legendas medidas ficam a 0-20 pt do diagrama."""

MIN_AXIS_OVERLAP = 0.25
"""Fração do lado menor que a linha e o diagrama precisam compartilhar no eixo transversal.

É o que impede a legenda da coluna vizinha de ser adotada: no `Karpov`, a linha da coluna
direita (x 278-402) não toca o diagrama da esquerda (x 56-203) e sai da lista.
"""

MAX_CAPTION_WORDS = 25
"""Acima disto o bloco é comentário, não legenda -- e seus padrões não valem como declaração."""

MARGIN_BAND = 0.07
"""Faixa do topo e do rodapé onde mora cabeçalho/rodapé corrente. Descartada por completo."""


@dataclass(frozen=True)
class TextLine:
    """Uma linha de texto da página, com o bbox dela e o tamanho do bloco de origem."""

    text: str
    bbox: tuple[float, float, float, float]
    block_words: int
    """Palavras do bloco inteiro de onde a linha veio. Ver `MAX_CAPTION_WORDS`."""

    group_id: int = 0
    """Identidade do grupo de legenda: linhas do mesmo bloco e da mesma coluna."""

    origin: LineOrigin = "text"
    """Camada de texto por padrão -- é o que este módulo sempre produziu."""

    confidence: float = 1.0
    """Quanto o motor confia no que leu. A camada de texto não é um palpite: vale 1,0."""

    @property
    def is_caption_like(self) -> bool:
        return self.block_words <= MAX_CAPTION_WORDS


@dataclass(frozen=True)
class NearbyLine:
    """Uma linha próxima a um diagrama, com a geometria da relação preservada."""

    line: TextLine
    distance: float
    placement: Placement
    primary: bool = False
    """Veio da legenda deste diagrama, e não de um vizinho que apenas passou perto."""

    @property
    def text(self) -> str:
        return self.line.text


@dataclass(frozen=True)
class DiagramContext:
    """O que a camada de texto diz sobre um diagrama. Campo não dito é `None`, sempre."""

    caption: str = ""
    """Linhas associadas, já limpas e unidas por `\\n`. Vazio quando não há texto vizinho."""

    side_to_move: chess.Color | None = None
    """Lado a jogar **declarado em texto**. `None` = a página não disse (não = brancas)."""

    side_to_move_evidence: str = ""
    """O trecho que decidiu o lado a jogar. Existe para o usuário poder discordar."""

    side_to_move_origin: SideOrigin | None = None
    """De onde veio a declaração, quando houve uma. `None` quando `side_to_move` é `None`."""

    side_to_move_confidence: float = 1.0
    """Confiança do trecho que decidiu. 1,0 para a camada de texto, o que o motor disser
    para o OCR. Sobrevive até o `[SideToMoveConfidence]` do PGN quando não é 1,0."""

    exercise_number: int | None = None
    players: tuple[str, str] | None = None
    event: str | None = None
    year: int | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.caption, self.side_to_move is not None, self.exercise_number, self.players, self.event, self.year)
        )


# --------------------------------------------------------------------------------------
# Normalizacao e padroes de lado a jogar
# --------------------------------------------------------------------------------------

_DASHES = "‐‑‒–—―−"


def fold(text: str) -> str:
    """Minúsculas, sem acento, espaços colapsados, travessão virando hífen.

    `ß` é tratado à parte porque NFKD não a decompõe: sem isso "Weiß am Zug" não casa com
    padrão nenhum, e o alemão é um dos três idiomas do acervo.
    """
    text = text.replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return re.sub(r"\s+", " ", text).strip().lower()


# O portugues do acervo nao segue uma forma so. Levantadas as legendas do `400
# Quebra-cabecas` (400 exercicios), aparecem cinco: "brancas jogam" (117), "jogada das
# pretas" (52), "jogada de pretas" (27), "pretas jogam" (13), "jogar de pretas" (12) e
# "jogam as pretas" (1). Cobrir so as duas que a S-16 listou deixaria 39 exercicios sem
# lado a jogar num livro que declara todos.
_PT_PLAY = r"jog(?:ada|ar|am|a)\s+(?:d(?:as|os|a|o|e)|as|os)\s+"

_WHITE_PATTERNS: tuple[str, ...] = (
    r"\b(?:as\s+)?brancas\s+(?:jogam|jogar|comecam|iniciam)\b",
    rf"\b{_PT_PLAY}brancas\b",
    r"\b(?:e\s+a\s+)?vez\s+d[ao]s\s+brancas\b",
    r"\bbrancas\s+no\s+lance\b",
    r"\bwhite\s+to\s+(?:move|play)\b",
    r"\bwhite\s+(?:plays|moves|begins|starts)\b",
    r"\bweiss?\s+(?:am\s+zug|zieht|beginnt|spielt|setzt)\b",
    r"\bam\s+zug\s*:\s*weiss?\b",
    r"\b(?:juegan\s+(?:las\s+)?blancas|blancas\s+juegan|turno\s+de\s+las\s+blancas)\b",
    r"\b(?:les\s+)?blancs\s+jouent\b",
    r"\btrait\s+aux\s+blancs\b",
)

_BLACK_PATTERNS: tuple[str, ...] = (
    r"\b(?:as\s+)?(?:pretas|negras)\s+(?:jogam|jogar|comecam|iniciam)\b",
    rf"\b{_PT_PLAY}(?:pretas|negras)\b",
    r"\b(?:e\s+a\s+)?vez\s+d[ao]s\s+(?:pretas|negras)\b",
    r"\b(?:pretas|negras)\s+no\s+lance\b",
    r"\bblack\s+to\s+(?:move|play)\b",
    r"\bblack\s+(?:plays|moves|begins|starts)\b",
    r"\bschwarz\s+(?:am\s+zug|zieht|beginnt|spielt|setzt)\b",
    r"\bam\s+zug\s*:\s*schwarz\b",
    r"\b(?:juegan\s+(?:las\s+)?negras|negras\s+juegan|turno\s+de\s+las\s+negras)\b",
    r"\b(?:les\s+)?noirs\s+jouent\b",
    r"\btrait\s+aux\s+noirs\b",
)

_WHITE_SYMBOLS = "◻□▫⬜◽⚪"
_BLACK_SYMBOLS = "◼■▪⬛◾⚫"

_SUBORDINATORS = (
    "se",
    "if",
    "when",
    "wenn",
    "quando",
    "after",
    "depois",
    "apos",
    "because",
    "porque",
    "since",
    "although",
    "though",
    "embora",
    "caso",
    "que",
    "para",
    "wo",
    "als",
    "nachdem",
    "wie",
)
"""Palavra imediatamente antes do padrão que transforma declaração em hipótese.

"Se as brancas jogarem 32.f2xg2" fala de uma variante imaginária, não de quem está na vez.
Foi um dos falsos positivos medidos, no `Melhores Finais de Capablanca`.
"""


def _match_side(folded: str) -> tuple[chess.Color, str] | None:
    """Lado a jogar declarado neste texto já normalizado, com o trecho que decidiu."""
    hits: list[tuple[int, chess.Color, str]] = []
    for patterns, color in ((_WHITE_PATTERNS, chess.WHITE), (_BLACK_PATTERNS, chess.BLACK)):
        for pattern in patterns:
            match = re.search(pattern, folded)
            if match is None:
                continue
            if _preceded_by_subordinator(folded, match.start()):
                logger.debug("padrão de lado a jogar descartado por contexto hipotético: %r", match.group(0))
                continue
            hits.append((match.start(), color, match.group(0)))

    if not hits:
        return None

    hits.sort()
    # Duas declaracoes opostas na mesma legenda: a pagina esta dizendo as duas coisas (caso
    # tipico da pagina de solucoes que lista varios exercicios). Nao ha o que escolher.
    colors = {color for _, color, _ in hits}
    if len(colors) > 1:
        logger.debug("declarações de lado a jogar conflitantes em %r", folded[:80])
        return None

    _, color, evidence = hits[0]
    return color, evidence


def _preceded_by_subordinator(folded: str, start: int) -> bool:
    prefix_words = folded[:start].split()
    return bool(prefix_words) and prefix_words[-1] in _SUBORDINATORS


def _match_side_symbol(text: str) -> tuple[chess.Color, str] | None:
    """Convenção tipográfica: quadrado vazado = brancas, cheio = pretas.

    Só vale em texto curto. Num parágrafo o mesmo símbolo é ornamento ou peça, e o
    chamador já filtra por `is_caption_like`.
    """
    for ch in text:
        if ch in _WHITE_SYMBOLS:
            return chess.WHITE, ch
        if ch in _BLACK_SYMBOLS:
            return chess.BLACK, ch
    return None


# --------------------------------------------------------------------------------------
# Extracao de linhas da pagina
# --------------------------------------------------------------------------------------

_BOARD_FONT_ROW = re.compile(r"^[1-8]?[0-9A-Za-z]{8}$")
_FILE_LABELS = re.compile(r"^a\W*b\W*c\W*d\W*e\W*f\W*g\W*h$", re.IGNORECASE)
_AXIS_LABEL = re.compile(r"^[a-h1-8]$")
_MIN_AXIS_LABELS = 6
"""A partir de tantos rótulos de eixo soltos na mesma faixa, são as bordas de um diagrama."""

_FONT_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
"""`ABCDEF+ChessMerida`: o prefixo de subconjunto do PDF, que não faz parte do nome."""

_DIAGRAM_FONT_NAME = re.compile(r"chess|skak|merida|diagram|schach|echecs|scacchi|ajedrez|xadrez", re.IGNORECASE)
_FIGURINE_FONT_NAME = re.compile(r"figurine|semfig", re.IGNORECASE)
"""Fonte de **figurina**, que desenha a peça no meio do lance (`♘f3`) e não o tabuleiro.

Precisa ser excluída à mão porque o nome dela casa com `_DIAGRAM_FONT_NAME`: no `Polgar` a
família é `SkakNew-Diagram` para o tabuleiro e `SkakNew-Figurine` para o lance. Medido no
acervo, as duas de figurina desenham **1.892 linhas que também têm prosa** -- são as listas
de lances, e descartá-las apagaria o texto que a S-208 vai precisar ler.
"""


def is_diagram_font(name: str) -> bool:
    """O nome desta fonte é o de uma fonte que desenha tabuleiro?

    Medido no acervo (2026-08-23, 964 fontes distintas em 41 livros): **3 casam** --
    `SkakNew-Diagram`, `ChessMerida` e `Chess-Merida` --, **2 são excluídas** por serem de
    figurina, e as 959 restantes não casam. Nenhuma fonte de prosa casa por engano.
    """
    limpo = _FONT_SUBSET_PREFIX.sub("", name)
    if _FIGURINE_FONT_NAME.search(limpo):
        return False
    return bool(_DIAGRAM_FONT_NAME.search(limpo))


def _is_diagram_font_row(text: str, fonts: Sequence[str] = ()) -> bool:
    """Linha que é o próprio diagrama desenhado com fonte de xadrez, não legenda.

    O `Polgar 5334` não tem imagem nenhuma: o tabuleiro é texto. Cada fila sai como uma
    linha (`0Z0Z0mkZ`, `Z0Z0Z0a0`) e as coordenadas saem como caracteres soltos. Sem este
    filtro, a legenda de cada diagrama desse livro seria o tabuleiro em si.

    **O crivo forte é o nome da fonte, e não o texto (S-217).** A versão original media a
    densidade de `0` e `Z` -- as casas vazias claras e escuras da Merida como o `Polgar` a
    codifica. Medido semeando a S-183, isso deixava passar **16 das 123 faixas** de
    referência, em 6 livros, por duas razões que o texto não alcança:

    | livro | fonte | casa vazia | por que escapava |
    |---|---|---|---|
    | `Polgar` | `SkakNew-Diagram` | `0` `Z` | `0l0o0ORL` tem 3 vazias, e o crivo pedia 4 |
    | 4 livros `_hq` | `ChessMerida` | `+` | outra codificação: o export do Lichess |
    | `Dvoretsky` | `Chess-Merida` | `+` `*` | idem, e a fila sai partida em `+`, `*`, `P` |

    A fila do `Dvoretsky` chega como caracteres de **um** símbolo (`+`, `*`, `P`, `k`), e
    não há crivo de texto que separe um `P` de tabuleiro de um `P` de prosa. O nome da
    fonte separa, e é a única coisa que separa.

    `fonts` são as fontes dos spans da linha, e a linha só cai se **todas** forem de
    diagrama. Medido, nenhuma linha do acervo mistura fonte de diagrama com outra
    (0 de 229.510) -- exigir todas não custa nada aqui, e é o que impede que uma prosa
    grudada num glifo de tabuleiro desapareça junto num livro que ainda não foi medido.

    Sem `fonts` -- linha vinda do OCR da S-43, ou fonte sem nome útil -- vale o crivo de
    texto de sempre, que continua pegando o `Polgar` sozinho.
    """
    if fonts and all(is_diagram_font(name) for name in fonts):
        return True
    compact = text.strip()
    if _FILE_LABELS.match(re.sub(r"\s+", "", compact)):
        return True
    if not _BOARD_FONT_ROW.match(compact):
        return False
    return sum(compact.count(ch) for ch in "0Zz") >= 4


def _axis_label_strip(
    items: Sequence[tuple[str, tuple[float, float, float, float]]],
) -> set[int]:
    """Índices dos rótulos que formam a **borda** de um diagrama, e não texto solto.

    Existe porque contar por bloco não bastava (S-217). O `Polgar` põe cada dígito de fila
    no seu **próprio bloco** -- `8`, `7`, `6` ... são 8 blocos de uma linha --, então o
    `_MIN_AXIS_LABELS` nunca disparava e os oito dígitos entravam na legenda. As letras
    `a b c d e f g h`, que o mesmo livro põe num bloco só, sempre caíram.

    Contar a página inteira em vez do bloco resolveria o `Polgar` e quebraria o `1937
    Kemeri`: a tabela de cruzamento do torneio tem dezenas de `1` soltos, que são
    **resultados**. Medido, a regra por página descartaria 4.904 linhas a mais na amostra do
    acervo, e as do `Kemeri` seriam perda de dado.

    O que separa os dois é o que a borda de um tabuleiro é de fato: rótulos **alinhados**
    numa faixa, e **distintos** -- as oito filas, ou as oito colunas, cada uma uma vez. A
    coluna de resultados do `Kemeri` é alinhada, mas é `1`, `1`, `0`, `1` -- repetida. Com
    as duas exigências o `Kemeri` sai inteiro da conta, e sobram só livros de xadrez.
    """
    dropped: set[int] = set()
    for axis in (0, 1):
        labels = list(range(len(items)))

        def find(index: int, labels: list[int] = labels) -> int:
            while labels[index] != index:
                labels[index] = labels[labels[index]]
                index = labels[index]
            return index

        for i, (_, a) in enumerate(items):
            for j in range(i + 1, len(items)):
                b = items[j][1]
                if _axis_overlap(a[axis], a[axis + 2], b[axis], b[axis + 2]) > 0.0:
                    labels[find(i)] = find(j)

        groups: dict[int, list[int]] = {}
        for index in range(len(items)):
            groups.setdefault(find(index), []).append(index)

        for group in groups.values():
            marks = {items[index][0].lower() for index in group}
            ranks = sum(1 for mark in marks if mark.isdigit())
            files = len(marks) - ranks
            if ranks >= _MIN_AXIS_LABELS or files >= _MIN_AXIS_LABELS:
                dropped |= set(group)
    return dropped


def _split_into_columns(bboxes: Sequence[tuple[float, float, float, float]]) -> list[int]:
    """Rotula por coluna as linhas de um bloco, unindo as que se sobrepõem na horizontal.

    O `Karpov` põe as legendas das duas colunas da página num bloco só (`№79. Steinitz -
    Bird` em x 82-181 e `№80. Steinitz - Mortimer` em x 278-402). Mantido inteiro, esse
    bloco daria a cada diagrama o adversário do vizinho.
    """
    labels = list(range(len(bboxes)))

    def find(index: int) -> int:
        while labels[index] != index:
            labels[index] = labels[labels[index]]
            index = labels[index]
        return index

    for i, (ax0, _, ax1, _) in enumerate(bboxes):
        for j in range(i + 1, len(bboxes)):
            bx0, _, bx1, _ = bboxes[j]
            if _axis_overlap(ax0, ax1, bx0, bx1) > 0.0:
                labels[find(i)] = find(j)

    return [find(index) for index in range(len(bboxes))]


def page_text_lines(page: fitz.Page) -> list[TextLine]:
    """Linhas de texto da página, sem cabeçalho/rodapé corrente e sem diagrama de fonte."""
    return _page_lines(page, margin=False)


def page_margin_lines(page: fitz.Page) -> list[TextLine]:
    """Só o que mora na faixa de margem -- o que `page_text_lines` descarta (S-43).

    Existe porque `MARGIN_BAND` joga fora 7% do topo e do rodapé como cabeçalho corrente, e
    às vezes o que mora ali é uma **declaração de escopo de página**: a página 40 do
    `Reinfeld` tem `LAS BLANCAS JUEGAN PRIMERO` em caixa alta no topo, e ela vale para os
    seis diagramas da página.

    O descarte continua certo como padrão. Medido (2026-08-09), a faixa de margem vale 6
    declarações contra as 150 que a S-16 já vê, em 3 livros -- é pouco, e o motivo de ser
    pouco é que a maioria dos cabeçalhos de fato repete o título do livro. Por isso a faixa
    não volta para o fluxo normal: ela é lida à parte, testada contra os padrões de lado a
    jogar, e só o que casa vira informação (ver `page_scope_declaration`).
    """
    return _page_lines(page, margin=True)


def _texto_girado(page: fitz.Page, bbox: Sequence[float]) -> tuple[float, float, float, float]:
    """A caixa de uma linha de texto no mesmo sistema em que a página é desenhada (S-129).

    `get_text("dict")` devolve bbox no sistema **não girado**, como o `get_image_info`. Aqui a
    consequência é o irmão do defeito da detecção: a legenda é casada ao diagrama **por
    proximidade**, e numa página com `/Rotate` as duas caixas estariam em sistemas diferentes —
    a legenda a 16 pt do diagrama pode acabar do outro lado da folha.

    Identidade quando a rotação é zero, que é o caso de 18.766 das 18.767 páginas do acervo.
    """
    x0, y0, x1, y1 = fitz.Rect(bbox) * page.rotation_matrix
    return float(x0), float(y0), float(x1), float(y1)


def _page_lines(page: fitz.Page, *, margin: bool) -> list[TextLine]:
    height = page.rect.height or 1.0
    top_limit = page.rect.y0 + height * MARGIN_BAND
    bottom_limit = page.rect.y1 - height * MARGIN_BAND

    blocks: list[tuple[int, list[tuple[str, tuple[float, float, float, float]]]]] = []
    for block in page.get_text("dict").get("blocks", ()):
        if block.get("type") != 0:
            continue
        block_words = sum(
            len(span.get("text", "").split()) for line in block.get("lines", ()) for span in line.get("spans", ())
        )

        kept: list[tuple[str, tuple[float, float, float, float]]] = []
        for line in block.get("lines", ()):
            spans = [span for span in line.get("spans", ()) if span.get("text", "").strip()]
            text = "".join(span.get("text", "") for span in line.get("spans", ()))
            text = re.sub(r"\s+", " ", text).strip()
            fonts = [str(span.get("font", "")) for span in spans]
            if not text or _is_diagram_font_row(text, fonts):
                continue

            x0, y0, x1, y1 = _texto_girado(page, line["bbox"])
            if (y1 <= top_limit or y0 >= bottom_limit) != margin:
                continue
            kept.append((text, (x0, y0, x1, y1)))

        # As coordenadas do diagrama de fonte chegam como caracteres avulsos ("a", "7"), e um
        # "7" avulso seria lido como numero de exercicio. Em bloco nenhum de legenda de
        # verdade eles aparecem aos seis.
        if sum(1 for text, _ in kept if _AXIS_LABEL.match(text)) >= _MIN_AXIS_LABELS:
            kept = [item for item in kept if not _AXIS_LABEL.match(item[0])]

        if kept:
            blocks.append((block_words, kept))

    # **A borda do diagrama nao respeita o bloco, e por isso a segunda passada e por pagina.**
    # O `Polgar` poe cada digito de fila num bloco proprio, e o corte por bloco acima nunca
    # os alcanca. Ver `_axis_label_strip` -- e por que ela e uma faixa, e nao a pagina toda.
    marks = [
        (block_index, item_index, text, bbox)
        for block_index, (_, kept) in enumerate(blocks)
        for item_index, (text, bbox) in enumerate(kept)
        if _AXIS_LABEL.match(text)
    ]
    if len(marks) >= _MIN_AXIS_LABELS:
        strip = _axis_label_strip([(text, bbox) for _, _, text, bbox in marks])
        drop = {(marks[i][0], marks[i][1]) for i in strip}
        if drop:
            blocks = [
                (
                    block_words,
                    [item for item_index, item in enumerate(kept) if (block_index, item_index) not in drop],
                )
                for block_index, (block_words, kept) in enumerate(blocks)
            ]

    lines: list[TextLine] = []
    next_group = 0
    for block_words, kept in blocks:
        if not kept:
            continue
        columns = _split_into_columns([bbox for _, bbox in kept])
        group_ids = {label: next_group + offset for offset, label in enumerate(dict.fromkeys(columns))}
        next_group += len(group_ids)
        for (text, bbox), label in zip(kept, columns, strict=True):
            lines.append(TextLine(text=text, bbox=bbox, block_words=block_words, group_id=group_ids[label]))
    return lines


def _axis_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    span = min(a1 - a0, b1 - b0)
    if span <= 0:
        return 0.0
    return max(0.0, min(a1, b1) - max(a0, b0)) / span


def _relate(
    line_bbox: tuple[float, float, float, float],
    bbox: tuple[float, float, float, float],
) -> tuple[float, Placement] | None:
    """Distância e posição relativa entre uma linha e um diagrama, ou `None` se desalinhados."""
    lx0, ly0, lx1, ly1 = line_bbox
    bx0, by0, bx1, by1 = bbox

    dx = max(bx0 - lx1, lx0 - bx1, 0.0)
    dy = max(by0 - ly1, ly0 - by1, 0.0)
    distance = math.hypot(dx, dy)

    if dy > 0.0 or (dx == 0.0 and dy == 0.0):
        if _axis_overlap(lx0, lx1, bx0, bx1) >= MIN_AXIS_OVERLAP:
            if dy == 0.0 and dx == 0.0:
                return distance, "overlapping"
            return distance, "above" if ly1 <= by0 else "below"

    if dx > 0.0 and _axis_overlap(ly0, ly1, by0, by1) >= MIN_AXIS_OVERLAP:
        return distance, "left" if lx1 <= bx0 else "right"

    return None


def lines_near(
    lines: Iterable[TextLine],
    bbox: tuple[float, float, float, float],
    *,
    radius_pt: float = DEFAULT_RADIUS_PT,
) -> list[NearbyLine]:
    """Linhas a até `radius_pt` do diagrama, ordenadas da mais próxima para a mais distante.

    Alinhamento no eixo transversal é exigido: linha "acima" precisa compartilhar coluna com
    o diagrama. Sem isso, uma página de duas colunas mistura as legendas.
    """
    found: list[NearbyLine] = []
    for line in lines:
        relation = _relate(line.bbox, bbox)
        if relation is None:
            continue
        distance, placement = relation
        if distance > radius_pt:
            continue
        found.append(NearbyLine(line=line, distance=distance, placement=placement))

    found.sort(key=lambda item: (item.distance, item.line.bbox[1]))
    return found


_GroupMatch = list[tuple[TextLine, float, Placement]]


def _group_matches(
    lines: Sequence[TextLine],
    bboxes: Sequence[tuple[float, float, float, float]],
    radius_pt: float,
) -> tuple[list[int], dict[tuple[int, int], _GroupMatch]]:
    """Para cada par (grupo, diagrama) dentro do raio, as linhas do grupo que se relacionam."""
    order: list[int] = list(dict.fromkeys(line.group_id for line in lines))
    by_group: dict[int, list[TextLine]] = {}
    for line in lines:
        by_group.setdefault(line.group_id, []).append(line)

    matches: dict[tuple[int, int], _GroupMatch] = {}
    for group_id in order:
        for index, bbox in enumerate(bboxes):
            matched: _GroupMatch = []
            for line in by_group[group_id]:
                relation = _relate(line.bbox, bbox)
                if relation is None or relation[0] > radius_pt:
                    continue
                matched.append((line, relation[0], relation[1]))
            if matched:
                matches[(group_id, index)] = matched
    return order, matches


def _match_distance(matched: _GroupMatch) -> float:
    return min(item[1] for item in matched)


def _match_placement(matched: _GroupMatch) -> Placement:
    return min(matched, key=lambda item: item[1])[2]


def dominant_placement(
    group_ids: Sequence[int],
    matches: dict[tuple[int, int], _GroupMatch],
    diagram_count: int,
) -> Placement | None:
    """De que lado do diagrama este livro imprime a legenda: acima ou abaixo.

    Existe porque distância pura erra de forma sistemática, e sempre no mesmo sentido. No
    `Karpov`, cujas legendas ficam **acima**, o vão acima do diagrama mede 10 pt e o abaixo
    7 pt -- então cada diagrama rouba a legenda do diagrama seguinte e a página inteira sai
    deslocada de um exercício (o `№79` vira `№81`). O mesmo acontecia no `Schiller`.

    Não dá para decidir isso por diagrama, justamente porque a informação local é o que
    engana. Decide-se pela página: o lado que consegue legenda para **mais** diagramas
    vence, e a distância média só desempata.
    """
    scores: dict[Placement, tuple[int, float]] = {}
    for side in ("above", "below"):
        distances = []
        for index in range(diagram_count):
            candidates = [
                _match_distance(matches[(group_id, index)])
                for group_id in group_ids
                if (group_id, index) in matches and _match_placement(matches[(group_id, index)]) == side
            ]
            if candidates:
                distances.append(min(candidates))
        if distances:
            scores[side] = (len(distances), sum(distances) / len(distances))

    if not scores:
        return None
    return min(scores, key=lambda side: (-scores[side][0], scores[side][1]))


def assign_lines_to_diagrams(
    lines: Sequence[TextLine],
    bboxes: Sequence[tuple[float, float, float, float]],
    *,
    radius_pt: float = DEFAULT_RADIUS_PT,
) -> list[list[NearbyLine]]:
    """Distribui as linhas entre os diagramas da página. Cada bucket vem com a legenda na frente.

    A unidade da distribuição é o **grupo** (bloco, quebrado por coluna), não a linha
    solta. Medido no `Schiller`, cuja legenda de três linhas fica acima do diagrama: linha
    a linha, o `6` da legenda do diagrama de baixo cai a 15 pt do diagrama de cima e a
    31 pt do dele próprio -- e o de cima, cuja legenda diz `5`, sai numerado 6. O número
    não anda sozinho: pertence ao mesmo bloco que os jogadores e o evento, e é o bloco
    inteiro que tem de escolher um dono.

    Escolhido o dono, a ordem dentro do bucket carrega a precedência: primeiro o grupo que
    está no lado onde este livro imprime a legenda (ver `dominant_placement`), depois o
    resto por distância. Quem lê o bucket fica com a legenda de verdade e ainda enxerga o
    comentário vizinho, que é o que vai para o `[Caption]` do PGN.
    """
    buckets: list[list[NearbyLine]] = [[] for _ in bboxes]
    if not bboxes:
        return buckets

    group_ids, matches = _group_matches(lines, bboxes, radius_pt)
    side = dominant_placement(group_ids, matches, len(bboxes))

    # Passo 1: cada diagrama fica com a legenda mais proxima do lado dominante, e um grupo
    # so serve a um diagrama -- o mais proximo dele.
    primary_of: dict[int, int] = {}
    claims = sorted(
        (
            (_match_distance(matched), index, group_id)
            for (group_id, index), matched in matches.items()
            if side is not None and _match_placement(matched) == side
        )
    )
    taken_groups: set[int] = set()
    for _, index, group_id in claims:
        if index in primary_of or group_id in taken_groups:
            continue
        primary_of[index] = group_id
        taken_groups.add(group_id)

    for index, group_id in primary_of.items():
        buckets[index].extend(
            NearbyLine(line=line, distance=distance, placement=placement, primary=True)
            for line, distance, placement in matches[(group_id, index)]
        )

    # Passo 2: o que sobrou vai para o diagrama mais proximo, como contexto e nao como legenda.
    for group_id in group_ids:
        if group_id in taken_groups:
            continue
        candidates = [
            (_match_distance(matches[(group_id, index)]), index)
            for index in range(len(bboxes))
            if (group_id, index) in matches
        ]
        if not candidates:
            continue
        _, index = min(candidates)
        buckets[index].extend(
            NearbyLine(line=line, distance=distance, placement=placement)
            for line, distance, placement in matches[(group_id, index)]
        )

    for bucket in buckets:
        bucket.sort(key=lambda item: (not item.primary, item.distance, item.line.bbox[1]))
    return buckets


# --------------------------------------------------------------------------------------
# Numero de pagina impresso
# --------------------------------------------------------------------------------------

_BARE_INT = re.compile(r"^\(?(\d{1,4})\)?$")

_NEIGHBOUR_DELTAS = (1, -1, 2, -2)
"""Vizinhos consultados. ±2 existe porque numeração de livro alterna de lado.

No `AAGAARD` a página par imprime o número à direita e a ímpar à esquerda; comparada só
com a página seguinte, nenhuma das duas confirma a outra.
"""

_SAME_COLUMN_TOLERANCE = 24.0
"""Quanto o número pode se deslocar entre páginas e ainda ser o mesmo elemento de layout."""


def _bare_integers(page: fitz.Page) -> list[tuple[int, tuple[float, float, float, float]]]:
    found: list[tuple[int, tuple[float, float, float, float]]] = []
    for block in page.get_text("dict").get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            text = "".join(span.get("text", "") for span in line.get("spans", ())).strip()
            match = _BARE_INT.match(text)
            if match is not None:
                x0, y0, x1, y1 = _texto_girado(page, line["bbox"])
                found.append((int(match.group(1)), (x0, y0, x1, y1)))
    return found


def running_page_number(doc: fitz.Document, page_index: int) -> int | None:
    """O número **impresso** nesta página, se algum inteiro dela se comportar como tal.

    Existe por causa do `Reinfeld 1001`, cuja única linha de texto é o número impresso, a
    16 pt do diagrama -- perto demais para a faixa de margem pegar, e por proximidade ele
    viraria "exercício 10".

    A primeira versão disto media um deslocamento constante entre o número impresso e o
    índice da página, amostrando o documento. Medido, **não existe tal constante**: no
    próprio `Reinfeld` o deslocamento vai de -10 na página 46 a -29 na 1012, porque o scan
    tem páginas a mais que o livro. Um contador que anda com a página não é uma função
    afim do índice; o que ele é, e o que se testa aqui, é *localmente* consecutivo. Um
    inteiro é número de página quando a página vizinha traz o sucessor dele na mesma
    coluna -- e é o casamento de coluna que impede o número do exercício, que também anda
    de um em um no `400 Quebra-cabeças`, de ser confundido com ele.

    `None` quando o livro não tem numeração legível (o `Karpov`, cujo cabeçalho sai do OCR
    como `z_o` e `3_0`), e aí o filtro simplesmente não opera.
    """
    page = doc[page_index]
    current = _bare_integers(page)
    if not current:
        return None

    confirmed: list[tuple[float, int]] = []
    for delta in _NEIGHBOUR_DELTAS:
        neighbour_index = page_index + delta
        if not 0 <= neighbour_index < doc.page_count:
            continue
        neighbour = _bare_integers(doc[neighbour_index])
        for value, bbox in current:
            for other_value, other_bbox in neighbour:
                if other_value != value + delta:
                    continue
                if _axis_overlap(bbox[0], bbox[2], other_bbox[0], other_bbox[2]) <= 0.0:
                    continue
                if abs(bbox[1] - other_bbox[1]) > _SAME_COLUMN_TOLERANCE:
                    continue
                confirmed.append((min(bbox[1] - page.rect.y0, page.rect.y1 - bbox[3]), value))

    if not confirmed:
        return None

    # Numero de exercicio tambem anda de um em um, e no `400 Quebra-cabeças` ele anda na
    # mesma cadencia do numero de pagina. O que separa os dois e a altura: numeracao de
    # pagina mora na borda, enunciado mora ao lado do diagrama.
    return min(confirmed)[1]


# --------------------------------------------------------------------------------------
# Interpretacao da legenda
# --------------------------------------------------------------------------------------

_NUMBER_PREFIX = re.compile(
    r"^(?:n[o°º!№]*\s*[.º]?\s*)?(\d{1,4})\s*[.:)\]\-–]",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_NAME = r"[A-ZÀ-Þ][\w'’.À-ÿ-]*(?:\s+[A-ZÀ-Þ][\w'’.À-ÿ-]*)?"
_PLAYERS = re.compile(rf"({_NAME})\s*[-–—]\s*({_NAME})\s*$")
_MOVE_TEXT = re.compile(r"\d\s*[.…]\s*\w|[KQRBNRPKDTLSC][a-h][1-8]")


_LOOSE_NUMBER_PREFIX = re.compile(r"^(\d[\d ]{0,4})\s+(?=[A-ZÀ-Þ])")
"""Número de exercício sem pontuação, e com o OCR podendo ter partido os dígitos.

O `AAGAARD` numera assim, e a camada de texto sai com `1 19 Bartrina - Ghitescu` para o
exercício 119. Sem isto, o livro que o critério de aceite da S-16 nomeia fica sem número.
"""


def _strip_number_prefix(text: str) -> str:
    return _NUMBER_PREFIX.sub("", text, count=1).strip()


def _parse_exercise_number(text: str) -> int | None:
    """Número do exercício declarado nesta linha, ou `None`.

    Não distingue sozinho número de exercício de número de página: quem faz isso é a faixa
    de margem (`page_text_lines`) e a comparação com `page_number`, no chamador.
    """
    bare = _BARE_INT.match(text)
    if bare is not None:
        return int(bare.group(1))

    prefix = _NUMBER_PREFIX.match(text)
    if prefix is not None and _strip_number_prefix(text):
        return int(prefix.group(1))

    loose = _LOOSE_NUMBER_PREFIX.match(text)
    if loose is not None and not _MOVE_TEXT.search(text):
        digits = loose.group(1).replace(" ", "")
        if digits.isdigit() and 0 < int(digits) < 10000:
            return int(digits)
    return None


def _parse_players(text: str) -> tuple[str, str] | None:
    """`Steinitz - Bird` -> `("Steinitz", "Bird")`. Nada disso -> `None`.

    Exige nomes capitalizados dos dois lados e o par no fim da linha: assim
    `1937 Kemeri` (evento) e `12.Na4! - forte` (lance) não passam por confronto.
    """
    candidate = _strip_number_prefix(text).strip(" .;")
    if not candidate or _MOVE_TEXT.search(candidate):
        return None
    if _match_side(fold(candidate)) is not None:
        return None

    match = _PLAYERS.search(candidate)
    if match is None:
        return None

    white, black = match.group(1).strip(" .-"), match.group(2).strip(" .-")
    if not white or not black or white.isdigit() or black.isdigit():
        return None
    if _YEAR.search(white) or _YEAR.search(black):
        return None
    return white, black


def _parse_event_year(text: str) -> tuple[str | None, int | None]:
    """`Paris, 1858` -> `("Paris", 1858)`. Ano sem evento e evento sem ano também valem."""
    candidate = _strip_number_prefix(text).strip(" .;")
    match = _YEAR.search(candidate)
    if match is None:
        return None, None

    year = int(match.group(1))
    event = candidate[: match.start()].strip(" ,;.-") or candidate[match.end() :].strip(" ,;.-")
    if _MOVE_TEXT.search(event or ""):
        return None, year
    return (event or None), year


def _side_from_line(text: str, *, caption_like: bool) -> tuple[chess.Color, str] | None:
    """Lado a jogar declarado nesta linha, com a exigência valendo pelo formato dela.

    Em legenda o padrão vale onde estiver. Em parágrafo vale só quando **abre** a linha --
    e essa distinção é o que mantém a precisão sem custar recall. Os falsos positivos
    medidos são todos oracionais ("se as brancas jogarem 32.f2xg2", "it was possible for
    White to play 20.Nd3", "In this position, White surprised Black"); os verdadeiros que
    moram em parágrafo são todos de abertura, porque o `AAGAARD` escreve o enunciado do
    exercício grudado no comentário ("White to play and win — Do you like having pieces...").
    """
    symbol = _match_side_symbol(text) if caption_like else None
    if symbol is not None:
        return symbol

    folded = fold(text)
    found = _match_side(folded)
    if found is None:
        return None
    if caption_like:
        return found

    color, evidence = found
    return found if folded.startswith(evidence) else None


@dataclass(frozen=True)
class _ParsedLine:
    """Uma linha já pronta para virar contexto, com o que decide a precedência dela."""

    text: str
    caption_like: bool
    """Formato de legenda (bloco curto), e não de parágrafo. Ver `MAX_CAPTION_WORDS`."""

    primary: bool
    """Veio da legenda deste diagrama, e não de um vizinho que passou perto."""

    origin: LineOrigin = "text"
    confidence: float = 1.0


@dataclass(frozen=True)
class _SideDecision:
    color: chess.Color
    evidence: str
    origin: LineOrigin
    confidence: float


def _side_from_tier(lines: Sequence[_ParsedLine]) -> _SideDecision | None:
    """Lado a jogar deste conjunto de linhas, ou `None` se ele disser as duas coisas.

    A checagem de contradição vale **dentro** do conjunto, e é por isso que o chamador
    separa legenda de vizinhança. Uma página de soluções que lista `11: Brancas jogam` e
    `12: Pretas jogam` na mesma legenda não tem resposta; já uma legenda que diz "brancas"
    ao lado do comentário de *outro* diagrama que diz "pretas" tem, e é a da legenda.

    Com o OCR da S-42 ligado, o conjunto pode misturar as duas fontes -- e aí a contradição
    tem desempate: **a camada de texto vence**. Não é preferência estética. A camada de
    texto é o que o editor gravou; o OCR é uma leitura de pixels que pode confundir `blancas`
    com `blancos` e, pior, `negras` com `negros` num scan sujo. Quando as duas discordam, a
    que não adivinhou está certa com frequência muito maior -- e o caso em que a camada erra
    e o OCR acerta é o caso em que a camada não existe, onde não há conflito para desempatar.

    **O desempate é raro por construção, e é bom que seja.** `_lines_with_ocr` só roda o
    motor onde a camada calou, então as duas fontes quase nunca disputam o mesmo escalão do
    mesmo diagrama: para isso, uma linha lida na vizinhança de um diagrama precisa cair
    também na de outro que já tinha texto. A regra existe para esse caso e para o dia em que
    o gate mudar -- não é o mecanismo principal do item.
    """
    found = [
        _SideDecision(color=declaration[0], evidence=declaration[1], origin=line.origin, confidence=line.confidence)
        for line in lines
        if (declaration := _side_from_line(line.text, caption_like=line.caption_like)) is not None
    ]
    if not found:
        return None

    if len({item.color for item in found}) > 1:
        from_text = [item for item in found if item.origin == "text"]
        if from_text and len({item.color for item in from_text}) == 1:
            logger.debug("camada de texto e OCR discordam do lado a jogar; a camada de texto decide")
            return from_text[0]
        logger.debug("declarações de lado a jogar conflitantes em %d linhas", len(found))
        return None

    # Mesma cor por caminhos diferentes: fica a da camada de texto, cuja evidencia e o
    # trecho real do PDF e nao uma transcricao.
    return next((item for item in found if item.origin == "text"), found[0])


def _parse_lines(lines: Sequence[_ParsedLine], *, page_number: int | None) -> DiagramContext:
    # A legenda decide; a vizinhanca so responde quando a legenda cala. Sao dois escaloes, e
    # a contradicao vale dentro de cada um: uma legenda que diz as duas coisas nao tem
    # resposta, mas uma legenda contradita pelo comentario do diagrama ao lado tem.
    primary = [item for item in lines if item.primary]
    secondary = [item for item in lines if not item.primary]

    decision = _side_from_tier(primary) or _side_from_tier(secondary)

    captions = [item.text for item in [*primary, *secondary] if item.caption_like]

    exercise_number: int | None = None
    for text in captions:
        value = _parse_exercise_number(text)
        if value is None or value == page_number:
            continue
        exercise_number = value
        break

    players: tuple[str, str] | None = None
    event: str | None = None
    year: int | None = None
    for text in captions:
        if players is None:
            players = _parse_players(text)
        if year is None:
            line_event, line_year = _parse_event_year(text)
            if line_year is not None:
                event, year = line_event, line_year

    return DiagramContext(
        caption="\n".join(item.text for item in lines),
        side_to_move=None if decision is None else decision.color,
        side_to_move_evidence="" if decision is None else decision.evidence,
        side_to_move_origin=None if decision is None else decision.origin,
        side_to_move_confidence=1.0 if decision is None else decision.confidence,
        exercise_number=exercise_number,
        players=players,
        event=event,
        year=year,
    )


def context_from_lines(nearby: Sequence[NearbyLine], *, page_number: int | None = None) -> DiagramContext:
    """`DiagramContext` a partir das linhas já associadas a um diagrama.

    Linha de parágrafo entra na legenda -- o comentário do exercício é o que o `[Caption]`
    do PGN carrega e vale para o leitor humano --, mas com o crivo mais duro para o que
    vira dado estruturado. Ver `_side_from_line` e `MAX_CAPTION_WORDS`.

    Quando nenhuma linha foi marcada como legenda (página sem lado dominante detectável),
    todas contam como tal: um único escalão é melhor que nenhum.
    """
    if not nearby:
        return DiagramContext()

    has_primary = any(item.primary for item in nearby)
    return _parse_lines(
        [
            _ParsedLine(
                text=item.text,
                caption_like=item.line.is_caption_like,
                primary=item.primary or not has_primary,
                origin=item.line.origin,
                confidence=item.line.confidence,
            )
            for item in nearby
        ],
        page_number=page_number,
    )


# --------------------------------------------------------------------------------------
# Escopo de pagina e segunda fonte de linhas (S-43)
# --------------------------------------------------------------------------------------


@runtime_checkable
class CaptionSource(Protocol):
    """Uma segunda fonte de `TextLine` para a página (S-43).

    Protocolo, e não import do `ocr_caption`, por dois motivos. O primeiro é a direção da
    dependência: quem sabe da camada de texto não pode passar a depender do motor de OCR,
    senão o extra opcional deixa de ser opcional. O segundo é que **isto não é uma via
    alternativa** -- é uma segunda fonte das mesmas `TextLine` que `page_text_lines`
    produz, e por isso todo o aparato da S-16 (agrupamento por coluna,
    `dominant_placement`, `assign_lines_to_diagrams`, os tiers, o filtro de prosa) continua
    valendo sem uma linha de mudança.
    """

    def lines_around(
        self,
        page: fitz.Page,
        bbox_pdf: tuple[float, float, float, float],
        vizinhos: Sequence[tuple[float, float, float, float]] = (),
    ) -> list[TextLine]:
        """Linhas na vizinhança do diagrama, em coordenadas do PDF.

        `vizinhos` são os outros diagramas da página, apagados do recorte junto com o alvo -- sem
        eles as peças quebram a escala do texto (S-207). Opcional: um leitor que os ignore lê
        como sempre leu."""

    def margin_lines(self, page: fitz.Page) -> list[TextLine]:
        """Linhas da faixa de margem, em coordenadas do PDF."""


@dataclass(frozen=True)
class PageScope:
    """Uma declaração de lado a jogar que vale para a página inteira, não para um diagrama."""

    color: chess.Color
    evidence: str
    origin: SideOrigin
    confidence: float = 1.0


def page_scope_declaration(page: fitz.Page, *, caption_reader: CaptionSource | None = None) -> PageScope | None:
    """Declaração de escopo de página na faixa de margem, ou `None` (S-43).

    A página 40 do `Reinfeld_1001` tem `LAS BLANCAS JUEGAN PRIMERO` em caixa alta no topo,
    e ela vale para os seis diagramas da página. `MARGIN_BAND` a descartava junto com o
    cabeçalho corrente, que é o que a faixa contém no resto do acervo.

    **A precedência é o cuidado deste item.** Isto vale menos que a legenda do diagrama e
    mais que o padrão "brancas" -- e o chamador só o aplica onde a legenda calou. Um livro
    cujo cabeçalho declara o lado e cujas legendas também não muda de comportamento nenhum:
    a legenda decidiu antes.

    Duas fontes, nesta ordem: a camada de texto (de graça, e é onde moram as 6 declarações
    que o levantamento de 2026-08-09 mediu) e, se ela calar e houver motor, o OCR. Uma
    contradição dentro da faixa -- `White to Move` no topo e `Black to Move` no rodapé --
    devolve `None`, pela mesma regra dos tiers: a página está dizendo as duas coisas.
    """
    for lines, origin in _scope_candidates(page, caption_reader):
        declarations = [
            (found, line) for line in lines if (found := _side_from_line(line.text, caption_like=True)) is not None
        ]
        if not declarations:
            continue
        if len({color for (color, _), _ in declarations}) > 1:
            logger.debug("faixa de margem declara os dois lados; sem escopo de página")
            return None

        (color, evidence), line = declarations[0]
        logger.info("declaração de escopo de página (%s): %r", origin, evidence)
        return PageScope(color=color, evidence=evidence, origin=origin, confidence=line.confidence)
    return None


def _scope_candidates(
    page: fitz.Page,
    caption_reader: CaptionSource | None,
) -> Iterable[tuple[list[TextLine], SideOrigin]]:
    yield page_margin_lines(page), escopo_de_pagina(DA_CAMADA)
    if caption_reader is not None:
        # **A procedência sai do leitor, e não é fixa em `ocr-page-scope`** (S-207). Ele declara
        # qual é (`CaptionReader.procedencia`), o escopo é sufixo, e este módulo segue sem saber
        # que motores existem -- que é a regra da S-181. Um leitor que não declare procedência
        # continua sendo o de terceiros, que é o que ele sempre foi.
        motor = getattr(caption_reader, "procedencia", DE_TERCEIROS)
        yield caption_reader.margin_lines(page), escopo_de_pagina(motor)


def _apply_page_scope(context: DiagramContext, scope: PageScope) -> DiagramContext:
    """A declaração de página preenche o que a legenda deixou vazio, e só isso."""
    if context.side_to_move is not None:
        return context
    return replace(
        context,
        side_to_move=scope.color,
        side_to_move_evidence=scope.evidence,
        side_to_move_origin=scope.origin,
        side_to_move_confidence=scope.confidence,
    )


def _lines_with_ocr(
    page: fitz.Page,
    text_lines: list[TextLine],
    bboxes: Sequence[tuple[float, float, float, float]],
    *,
    radius_pt: float,
    caption_reader: CaptionSource,
) -> list[TextLine]:
    """As linhas da camada de texto, mais as do OCR onde ela não respondeu.

    **A decisão é por diagrama, não por livro**, e é o que os 5 livros de OCR parcial
    exigem: no `Gaprindashvili` a camada existe em 14 de 30 páginas, e decidir por livro
    deixaria metade sem legenda ou pagaria OCR em metade sem precisar. O critério é direto:
    se a camada de texto já pôs alguma linha na vizinhança deste diagrama, ela respondeu, e
    o OCR não roda -- economia real, dado o custo de página medido na S-61.
    """
    extras: list[TextLine] = []
    next_group = max((line.group_id for line in text_lines), default=-1) + 1
    lidos = 0
    inicio = time.perf_counter()

    for bbox in bboxes:
        if lines_near(text_lines, bbox, radius_pt=radius_pt):
            continue
        lidos += 1
        # **Os outros diagramas da página vão junto, para serem apagados do recorte** (S-207).
        # A faixa tem 60 pt de raio, e numa folha de quatro problemas ela contém pedaços dos
        # outros três -- as peças deles quebram a escala do texto e a leitura sai vazia. Ver
        # `CaptionReader.lines_around`, que traz a medição.
        vizinhos = [outro for outro in bboxes if outro != bbox]
        novas = caption_reader.lines_around(page, bbox, vizinhos)
        if not novas:
            continue

        # Os group_id do OCR comecam do zero a cada diagrama; sem o deslocamento, dois
        # diagramas teriam legendas no "mesmo bloco" e `assign_lines_to_diagrams` daria
        # as duas a um so.
        originais = dict.fromkeys(linha.group_id for linha in novas)
        remapeados = {group: next_group + offset for offset, group in enumerate(originais)}
        next_group += len(remapeados)
        extras.extend(replace(line, group_id=remapeados[line.group_id]) for line in novas)

    if lidos:
        # O custo por pagina e criterio de aceite da S-43, nao curiosidade: a S-61 mediu
        # ~2,95 s de pipeline por pagina, e um OCR que passe de ~2x o custo do
        # reconhecimento precisa de recorte mais apertado antes de o item fechar. Sem esta
        # linha, descobrir isso exigiria instrumentar de novo.
        logger.info(
            "OCR de legenda: %d de %d diagramas sem texto na camada, %d linhas lidas em %.3f s.",
            lidos,
            len(bboxes),
            len(extras),
            time.perf_counter() - inicio,
        )
    return [*text_lines, *extras]


def contexts_for_page(
    page: fitz.Page,
    bboxes: Sequence[tuple[float, float, float, float]],
    *,
    radius_pt: float = DEFAULT_RADIUS_PT,
    page_number: int | None = None,
    caption_reader: CaptionSource | None = None,
) -> list[DiagramContext]:
    """Um contexto por diagrama da página, com as linhas repartidas sem sobreposição.

    Com `caption_reader`, as linhas do OCR entram **junto** com as da camada de texto, e
    não por um caminho paralelo (S-43). Uma página que tem as duas usa as duas, e a camada
    de texto vence no empate -- ver `_side_from_tier`.
    """
    if not bboxes:
        return []

    lines = page_text_lines(page)
    if caption_reader is not None:
        lines = _lines_with_ocr(page, lines, bboxes, radius_pt=radius_pt, caption_reader=caption_reader)

    buckets = assign_lines_to_diagrams(lines, bboxes, radius_pt=radius_pt)
    contexts = [context_from_lines(bucket, page_number=page_number) for bucket in buckets]

    # A faixa de margem so e consultada quando sobra diagrama sem lado a jogar. Nao e
    # otimizacao: e o que garante que a precedencia da legenda nunca seja disputada.
    if any(context.side_to_move is None for context in contexts):
        scope = page_scope_declaration(page, caption_reader=caption_reader)
        if scope is not None:
            contexts = [_apply_page_scope(context, scope) for context in contexts]
    return contexts


def contexts_for_pdf_page(
    pdf_source: PdfSource,
    page_index: int,
    bboxes: Sequence[tuple[float, float, float, float]],
    *,
    radius_pt: float = DEFAULT_RADIUS_PT,
    caption_reader: CaptionSource | None = None,
) -> list[DiagramContext]:
    """`contexts_for_page` para quem tem o caminho do PDF, e não um `fitz.Page` aberto.

    Mesmo desenho de `detection.detect_diagrams_in_pdf_page`, e pelo mesmo motivo: manter
    a assinatura livre de objeto do PyMuPDF. Abre o documento a cada chamada e lê o texto
    de até quatro páginas vizinhas para `running_page_number` -- irrelevante ao lado do
    render a 220 DPI e das 64 inferências que a mesma página vai custar.
    """
    from .pdf_io import open_document

    if not bboxes:
        return []

    with open_document(pdf_source) as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"Página {page_index} fora do intervalo (0..{doc.page_count - 1})")
        return contexts_for_page(
            doc[page_index],
            bboxes,
            radius_pt=radius_pt,
            page_number=running_page_number(doc, page_index),
            caption_reader=caption_reader,
        )
