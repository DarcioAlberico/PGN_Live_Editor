"""Detector de duas fontes: imagem embutida localiza, contorno alinha (S-12).

A S-12 propunha gerar candidatos das duas fontes de forma independente e, quando
discordassem, **ler as duas** e arbitrar por legalidade. A medição mostrou que não é preciso
pagar por isso, porque as duas fontes não competem pela mesma coisa:

| livro (10 páginas) | embutida crua | embutida + warp | contorno puro |
|---|---|---|---|
| `1937 Kemeri` | 0,478 / 0 ilegais | **0,538 / 0** | 0,431 / **2 ilegais** |
| `AAGAARD` | 0,999 / 0 | 1,000 / 0 | 1,000 / 0 |
| `Schiller` | 0,137 / 0 | **0,360 / 0** | 0,257 / 0 |
| `Karpov 1` | 0,906 / 0 | **0,962** / 1 | 0,948 / 0 |
| `Euwe Band 1-2` | 0,010 / 1 | **0,025 / 1** | 0,014 / 2 |

(confiança mínima média / posições ilegais)

Lido assim: o bbox embutido é melhor em **localizar** -- ele sabe o que é diagrama e o que é
figura, então mata o falso positivo que o contorno inventa (Kemeri: 2 posições ilegais viram
0). O warp por contorno é melhor em **alinhar** -- ele acha os cantos exatos do tabuleiro, e
recortar o bbox cru deixa a grade 8×8 fora de registro, o que derruba a confiança (Schiller:
0,137 contra 0,360).

Então a composição certa é uma por candidato, não uma escolha entre listas: usar o bbox para
saber onde olhar e rodar o contorno **dentro** dele para alinhar. Fica melhor que as duas
fontes isoladas em 4 dos 5 livros, e empata no quinto.

O contorno segue rodando na página inteira também, para não perder o que a imagem embutida
não cobre -- que é a maioria do acervo: 12 dos 27 PDFs são scan de página inteira e 2 são
vetoriais (ver o docstring do pacote).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf as fitz

from ..board_detection import MIN_CHECKER_CONTRAST as _MIN_CHECKER_CONTRAST
from ..board_detection import RejectedQuad as RejectedQuad
from ..board_detection import (
    _bbox_iou,
    _board_pattern_score,
    _sort_selected_candidates,
    board_checker_score,
    detect_boards,
)
from ..config import BOARD_SIZE, DEFAULT_MAX_BOARDS, DEFAULT_READING_ORDER, ReadingOrder
from ..pdf_io import PdfSource
from .embedded import DiagramCandidate, _pixels_for_bbox, candidates_from_embedded_images

logger = logging.getLogger(__name__)

OVERLAP_IOU = 0.50
"""Acima disto, candidato de contorno é considerado o mesmo diagrama de um embutido."""

REFINE_PADDING_PT = 6.0
"""Folga em pontos ao redor do bbox, para o contorno ter borda onde achar a moldura."""

EMBEDDED_SIZE_TOLERANCE = 0.30
"""Quanto o lado de um achado de contorno pode fugir do lado típico das imagens embutidas.

Numa página que declara diagramas de ~590 pt, o falso positivo do Kemeri tem tamanho bem
diferente e cai fora; os 4 diagramas por livro que o `Schiller` e o `Karpov` não declaram têm
o mesmo tamanho dos declarados e passam.
"""


MIN_CHECKER_CONTRAST = _MIN_CHECKER_CONTRAST
"""Reexportado de `board_detection`, que é onde ele passou a morar (S-160).

O piso é o mesmo da S-143 e continua valendo **só para achado de contorno** -- imagem embutida
é declaração do PDF e continua ganhando, como desde a S-12. O que mudou é *quando* ele corre:
aqui ele julgava o que `detect_boards` já tinha devolvido, ou seja, depois da ordenação por
score e da supressão por IoU daquela função, e um falso positivo que vencesse por score comia
o diagrama de verdade antes de morrer. O relato inteiro, com os números da página que o
expôs, está em `board_detection.MIN_CHECKER_CONTRAST`.

`detect_diagrams` continua tendo o botão (`checker_contrast_floor`); ele agora é repassado ao
`detect_boards` em vez de aplicado depois dele.
"""


REFINE_TOLERANCE = 0.02
"""Quanto o refino pode piorar a textura de tabuleiro e ainda ser aceito (S-38).

Não é zero porque `_board_pattern_score` tem ruído de reamostragem: o recorte refinado é
outro conjunto de pixels, e uma diferença de ±0,01 entre dois recortes igualmente bons não
significa nada. Zero faria o refino ser recusado metade das vezes por sorteio.
"""


def board_texture_score(board_rgb: np.ndarray) -> float:
    """Quanto este recorte parece um tabuleiro: contraste entre casas mais grade periódica.

    Envelopa `_board_pattern_score` na resolução em que ele foi calibrado (320 px, 40 por
    casa). Chamá-lo direto num recorte de outro tamanho compara números que não são
    comparáveis, e a decisão da S-38 é exatamente uma comparação.
    """
    return _board_pattern_score(cv2.resize(board_rgb, (320, 320), interpolation=cv2.INTER_AREA))


def texture_scores_side_by_side(a_rgb: np.ndarray, b_rgb: np.ndarray) -> tuple[float, float]:
    """A nota de textura dos dois recortes, medida **do mesmo lado** (S-130).

    `board_texture_score` leva qualquer recorte a 320 px, então os dois chegam ao mesmo
    tamanho -- mas não à mesma **informação**: um recorte de 800 px reduzido a 320 é nítido, e
    um de 200 px ampliado a 320 continua borrado, porque ampliar não cria detalhe. A nota
    passava a depender de qual dos dois chegou maior, e não só do que cada um mostra.

    **Medido, e o defeito é maior do que o enunciado supunha -- em conteúdo texturizado.** O
    mesmo tabuleiro hachurado, entregue em oito resoluções, e a nota que sai:

    | lado de origem | 800 | 640 | 480 | 320 | 240 | 200 | 160 | 128 |
    |---|---|---|---|---|---|---|---|---|
    | `board_texture_score` | 0,0775 | 0,0772 | 0,0775 | 0,0775 | **0,2862** | **0,4203** | 0,0775 | **0,4202** |

    Amplitude **0,343**, contra uma margem de decisão de 0,02 (`REFINE_TOLERANCE`). Num
    tabuleiro sintético limpo a nota é perfeitamente estável (0,6000 nas oito) — é por isso que
    ninguém tinha visto: o defeito só aparece onde o acervo de verdade vive.

    **De onde vem, exatamente.** Decompondo a nota nas duas parcelas, a de **xadrez** é estável
    (0,0335 a 0,0340 nas oito) e a de **grade** é toda a variação (0,1429 nas resoluções que são
    múltiplos limpos de 160, e 0,6646 / 1,0000 em 240, 200 e 128). É *aliasing*: nessas
    reduções as linhas da hachura caem em cima da expectativa de período 20 px, e
    `_periodic_peak_score` reporta uma grade perfeita que a imagem não tem.

    Isso dá à S-143 uma **segunda razão independente** para não confiar na parcela de grade como
    guarda: além de ser imitável por foto e moldura, ela não é invariante à escala.

    **O que esta função conserta, e o que ela não conserta.** Ela não torna a nota invariante --
    não dá: uma hachura genuinamente pode aliasar para uma grade, e nenhuma reamostragem desfaz
    isso. O que ela garante é que os **dois lados da comparação sofram o mesmo**, reduzindo
    ambos ao menor lado dos dois antes de pontuar. A decisão passa a depender do que as imagens
    mostram, que é o que `refine_candidate_with_contour` e `_contour_wins_over_merged` precisam.
    """
    lado = min(a_rgb.shape[0], a_rgb.shape[1], b_rgb.shape[0], b_rgb.shape[1])

    def _reduzir(imagem: np.ndarray) -> np.ndarray:
        if imagem.shape[0] == lado and imagem.shape[1] == lado:
            return imagem
        # `INTER_AREA` porque aqui só se reduz, por construção: `lado` é o menor dos dois.
        return cv2.resize(imagem, (lado, lado), interpolation=cv2.INTER_AREA)

    return board_texture_score(_reduzir(a_rgb)), board_texture_score(_reduzir(b_rgb))


def board_checker_contrast(board_rgb: np.ndarray) -> float:
    """Só a parcela de xadrez da textura, na mesma resolução calibrada (S-143).

    **Quem a chamava dentro deste módulo era o piso da S-143, e ele mudou de casa na S-160**
    -- ver `MIN_CHECKER_CONTRAST`. Ela fica porque é a leitura isolada dessa parcela sobre um
    recorte já pronto, que é o que a suíte usa para afirmar que foto dá zero e diagrama não;
    o caminho de produção mede a mesma coisa mais cedo, sobre o warp que já tem em mãos.

    Separada de `board_texture_score` porque as duas parcelas respondem perguntas
    diferentes, e misturá-las é o que fez a S-80 fracassar -- ver `MIN_CHECKER_CONTRAST`.
    """
    return board_checker_score(cv2.resize(board_rgb, (320, 320), interpolation=cv2.INTER_AREA))


@dataclass(frozen=True)
class ContourInside:
    """O que o contorno achou **dentro** do bbox de um candidato embutido.

    Uma passada só, dois clientes: `refine_candidate_with_contour` usa o recorte para
    realinhar a grade (S-38) e `is_page_band` usa o quad para decidir se aquele bbox é um
    diagrama ou uma faixa da página que contém um (S-176). Medir a mesma coisa duas vezes é o
    que este dataclass existe para não fazer -- cada medida custa um `get_pixmap` e um
    `detect_boards` por candidato, em toda página de todo livro exportado.
    """

    region_rgb: np.ndarray
    """A região renderizada: o bbox mais `REFINE_PADDING_PT` de folga, cortada na página."""

    board_rgb: np.ndarray
    """O tabuleiro que o contorno achou ali, já em `BOARD_SIZE`."""

    quad: np.ndarray | None
    """Os quatro cantos dele, em pixels de `region_rgb`. `None` quando o achado é a região."""

    @property
    def board_fill(self) -> float:
        """Que fração do lado maior da região o tabuleiro achado ocupa, em 0..1.

        1,0 quando o bbox embutido **é** o tabuleiro, que é o caso de 99% do acervo. Bem
        abaixo disso quando ele é uma faixa da página com um tabuleiro dentro -- ver
        `BAND_BOARD_FILL`.
        """
        lado_regiao = max(self.region_rgb.shape[0], self.region_rgb.shape[1])
        if lado_regiao <= 0:
            return 0.0
        if self.quad is None:
            return 1.0
        xs, ys = self.quad[:, 0], self.quad[:, 1]
        lado = max(float(xs.max() - xs.min()), float(ys.max() - ys.min()))
        return lado / float(lado_regiao)


def contour_inside_candidate(
    page: fitz.Page,
    candidate: DiagramCandidate,
    *,
    padding_pt: float = REFINE_PADDING_PT,
) -> ContourInside | None:
    """Roda o detector de contorno dentro do bbox do candidato. `None` quando não acha nada.

    A folga de `padding_pt` existe para o contorno ter borda onde achar a moldura, e é a mesma
    desde a S-12; ela entra na conta do `board_fill`, então um diagrama pequeno num bbox justo
    chega a ~0,93 e não a 1,00.
    """
    bbox = fitz.Rect(candidate.bbox_pdf)
    padded = fitz.Rect(bbox.x0 - padding_pt, bbox.y0 - padding_pt, bbox.x1 + padding_pt, bbox.y1 + padding_pt)
    padded = padded & page.rect
    if padded.is_empty:
        return None

    region = _pixels_for_bbox(page, padded, native_side=max(candidate.native_size))
    if region is None:
        return None

    found = detect_boards(region, max_boards=1, warn_on_cap=False)
    if not found:
        return None

    board_rgb, quad = found[0]
    return ContourInside(region_rgb=region, board_rgb=board_rgb, quad=quad)


BAND_BOARD_FILL = 0.50
"""Quanto do lado da imagem embutida o tabuleiro achado dentro dela precisa ocupar (S-176).

**O relato.** A página 14 do `Yusupov` desenhava uma caixa de 460×403 pt sobre uma página de
453×666 -- o texto do capítulo e os dois diagramas dele dentro de um retângulo só. A imagem
embutida ali não é diagrama nenhum: é uma **faixa da página rasterizada** pelo produtor do PDF,
porque as duas tarjas de vídeo reverso do cabeçalho de partida não são texto. Nas páginas
vizinhas essa mesma faixa tem aspecto 2,22 e 7,36 e morre no `ASPECT_TOLERANCE`; nesta ela
saiu em 1,140 -- dentro do limite -- e cobrindo 61% da página, abaixo do `MAX_PAGE_COVERAGE`.
As duas guardas de entrada são sobre **forma**, e a forma daquela faixa era, por acidente, a de
um diagrama.

**E o dano não parava na caixa errada.** A faixa entra em `_typical_side` como um lado de 1405
px contra os 516 px do diagrama de verdade da página; o desempate da S-79 é pelo maior, o
gabarito da página vira 1405, e os **dois diagramas que o contorno tinha achado** (423 e 417
px) morrem em `prior-de-tamanho`. A página entregava 2 caixas, uma delas absurda, onde havia 3
diagramas.

**O sinal, e por que não é o contraste de casa.** A tentação era estender o piso da S-143 à
fonte embutida -- a faixa dá `board_checker_score` **0,0000**. Medido no acervo, isso reprova:
24 dos 1287 candidatos embutidos dão zero, e **10 deles são diagramas impecáveis do
`Schiller`**, cujo recorte inclui duas linhas de legenda acima do tabuleiro. Grade fora de
registro dá zero pelo mesmo motivo que ausência de tabuleiro dá zero, e a nota não distingue os
dois casos. É a terceira vez que o projeto tenta julgar recorte embutido por nota absoluta e a
medição reprova -- ver a S-80 em `docs/ANALISE_DETECCAO.md`.

**O sinal que separa é geométrico:** um diagrama contém um tabuleiro que o **preenche**; uma
faixa contém um tabuleiro que é um pedaço dela. Medido nos 837 candidatos embutidos do acervo
em que o contorno acha algum tabuleiro dentro do bbox:

| população | `board_fill` |
|---|---|
| 831 candidatos | **0,7076 a 0,9829** |
| 6 candidatos | **0,1083 a 0,3758** |

O vão entre 0,3758 e 0,7076 é de 0,33, e qualquer corte dentro dele se comporta igual no
acervo inteiro. 0,50 tem significado próprio: abaixo dele a imagem é mais longa que duas vezes
o tabuleiro, ou seja, tem espaço para outra coisa que não é o diagrama.

**Os 6 não são todos faixa, e é por isso que a segunda condição existe** -- ver
`is_page_band` e `BAND_BOARD_CHECKER`."""


BAND_BOARD_CHECKER = 0.06
"""Contraste de casa mínimo para o achado dentro do bbox contar como **tabuleiro** (S-176).

A segunda condição de `is_page_band`, e ela não é enfeite: **sem ela a guarda perde diagrama**.
Medido no acervo, 24 páginas por livro, com a primeira condição sozinha: 1483 candidatos → 1478,
**5 perdas e nenhum ganho**. As cinco são o mesmo caso -- diagrama inteiro e legítimo em que o
contorno se prendeu a **uma casa** do próprio tabuleiro, o que dá preenchimento ~1/8 sem que
haja faixa nenhuma.

**Por que uma casa dá quase zero.** É o mecanismo da S-143 outra vez: `board_checker_score`
exige que as 32 casas de uma paridade sejam sistematicamente mais claras que as 32 da outra,
num reticulado 8×8. Um recorte de uma casa só divide tinta uniforme em 64 pedaços iguais, a
diferença entre as paridades é ruído, e o `clip` em 0 quase morde.

**O número, e por que 0,06.** As duas populações de `board_fill < 0,50`, medidas -- a de cima
varrendo o `1001_Winning ... _hq` de três em três páginas (374 de 1121) mais o `La_Combinacion`
inteiro, que é onde ela vive:

| população | quantos | contraste do achado |
|---|---|---|
| casa do próprio diagrama | 25 | 0,0000 a **0,0061** |
| faixa de página | 17 | **0,1310** a 0,7408 |

O vão é de **fator 21**, e 0,06 fica dentro dele: dez vezes acima do maior ruído e duas vezes
abaixo do menor sinal. Qualquer valor em (0,0061; 0,1310) se comporta igual no acervo.

O preenchimento dessas 25 confirma o mecanismo sem depender da nota: **0,108 a 0,128**, que é
1/8 -- quanto uma casa ocupa de um tabuleiro de oito.

**Não é o piso da S-143, e reusá-lo seria o erro.** `MIN_CHECKER_CONTRAST` é **0,0**, e a
primeira versão desta guarda o reusou -- foi assim que as 5 perdas apareceram: 0,0061 é maior
que zero. Os dois pisos respondem perguntas diferentes. Lá a alternativa é uma **foto**, que dá
exatamente zero; aqui é um **pedaço de tabuleiro**, que dá quase zero. E não vale generalizar
0,06 de volta para lá: nesta mesma medição, 21 dos 831 tabuleiros que preenchem o próprio bbox
ficam abaixo de 0,06 e são todos legítimos -- eles só não são alcançados aqui porque a primeira
condição já os isentou.

**O erro é conservador de propósito.** Uma faixa cujo tabuleiro interno tenha contraste muito
baixo sobrevive, e volta a ser o defeito do relato naquela página. É a direção certa de errar:
na dúvida, a declaração do PDF fica de pé, que é a regra desde a S-12."""


def is_page_band(
    found: ContourInside,
    *,
    min_fill: float = BAND_BOARD_FILL,
    checker_floor: float | None = BAND_BOARD_CHECKER,
) -> bool:
    """Este bbox embutido é uma **faixa da página** com um diagrama dentro, e não o diagrama?

    Duas condições, e a segunda é o que impede a primeira de apagar diagrama de verdade:

    1. o tabuleiro achado dentro ocupa menos de `min_fill` do lado da região (`BAND_BOARD_FILL`);
    2. **e o que foi achado é mesmo um tabuleiro**, e não um pedaço de um (`BAND_BOARD_CHECKER`).

    Sem (2) a regra reprova, e reprova em produção -- ver `BAND_BOARD_CHECKER`, que é o número
    e a medição. Dos 6 candidatos do acervo abaixo do corte de preenchimento, **2 são diagramas
    inteiros e legítimos** em que o contorno se prendeu a uma casa do tabuleiro:

    | livro, página | `board_fill` | contraste do achado | o que é |
    |---|---|---|---|
    | `1001_Winning ... _hq` p812 | 0,1083 | **0,0002** | uma casa clara do próprio diagrama |
    | `La_Combinacion` p24 | 0,1140 | **0,0061** | idem |
    | `Yusupov` p14 | 0,3051 | 0,2608 | faixa: o relato da S-176 |
    | `Yusupov` p1950 | 0,3104 | 0,3110 | faixa |
    | `Yusupov` p1820 | 0,3133 | 0,4133 | faixa |
    | `GALLAGHER` p124 | 0,3758 | 0,7058 | faixa: duas colunas de texto e um diagrama |
    | `GALLAGHER` p140 | 0,3355 | 0,7408 | faixa |

    `checker_floor=None` desliga a segunda condição, e quem desliga assume perder os dois
    primeiros.
    """
    if found.board_fill >= min_fill:
        return False
    if checker_floor is None:
        return True
    return board_checker_contrast(found.board_rgb) >= checker_floor


def refine_candidate_with_contour(
    page: fitz.Page,
    candidate: DiagramCandidate,
    *,
    padding_pt: float = REFINE_PADDING_PT,
    tolerance: float = REFINE_TOLERANCE,
) -> DiagramCandidate:
    """Realinha um candidato embutido rodando o detector de contorno dentro do bbox dele.

    Devolve o candidato original quando o contorno não acha nada na região -- caso em que o
    recorte cru é o melhor que se tem, e não há razão para piorá-lo.

    **E também quando o que ele acha é pior (S-38).** Até aqui a função conferia se o
    contorno tinha achado *alguma coisa*, nunca se o que achou era *melhor*, e a diferença
    não é acadêmica: na página 80 do `Karpov 1` o bbox embutido do candidato #4 contém um
    diagrama impecável, e o refino o substituía por um trapézio de texto que o modelo lia
    como oito reis brancos, com confiança 0,0004.

    Medido nos seis candidatos daquela página, `board_texture_score` cru → refinado:

    | candidato | cru | refinado | |
    |---|---|---|---|
    | #0 | 0,3138 | 0,6042 | melhora |
    | #1 | 0,2000 | 0,4616 | melhora |
    | #2 | 0,3511 | **0,2388** | **piora** |
    | #3 | 0,2000 | 0,4271 | melhora |
    | #4 | 0,2892 | **0,2252** | **piora -- é o dos oito reis** |
    | #5 | 0,3306 | 0,5059 | melhora |

    O refino ajuda em quatro e atrapalha em dois. Ele fica: alinhar a grade é o que a medição
    da S-12 mostrou valer 0,137 → 0,360 de confiança no `Schiller`. O que muda é passar a
    conferir o resultado, e é a mesma regra que a função já aplicava ao caso "não achou
    nada" -- só que agora aplicada ao caso "achou coisa pior".

    A passada de contorno em si mora em `contour_inside_candidate` desde a S-176, porque a
    guarda de faixa lê o **mesmo** achado. Esta função continua sendo a porta de quem só quer
    o realinhamento.
    """
    found = contour_inside_candidate(page, candidate, padding_pt=padding_pt)
    if found is None:
        return candidate
    return _apply_refinement(candidate, found, tolerance=tolerance)


def _apply_refinement(
    candidate: DiagramCandidate,
    found: ContourInside,
    *,
    tolerance: float = REFINE_TOLERANCE,
) -> DiagramCandidate:
    """A decisão da S-38 sobre um achado de contorno que já está em mãos."""
    board_rgb = found.board_rgb
    # Do mesmo lado (S-130): o recorte embutido chega na resolução nativa do PDF e o do
    # contorno em `BOARD_SIZE`, e a nota dependia de qual dos dois era maior.
    antes, depois = texture_scores_side_by_side(candidate.board_rgb, board_rgb)
    if depois < antes - tolerance:
        # Descartar em silêncio foi o que deixou o trapézio do Karpov passar por semanas
        # parecendo um problema do classificador.
        logger.info(
            "Refino do contorno descartado em %s: textura de tabuleiro cairia de %.4f para "
            "%.4f. Fica o recorte embutido cru.",
            tuple(round(value) for value in candidate.bbox_pdf),
            antes,
            depois,
        )
        return candidate

    return DiagramCandidate(
        board_rgb=board_rgb,
        bbox_pdf=candidate.bbox_pdf,
        source="embedded",
        # Concordância entre as duas fontes é sinal de confiança, e sai de graça.
        detector_score=min(1.0, candidate.detector_score + 0.15),
        native_size=candidate.native_size,
        trimmed=True,
        # A proveniencia sobrevive ao refino (S-81). Reconstruir o candidato sem ela zerava o
        # `merged_tiles`, e as duas regras que dependem dele -- a uniao nao suprime contorno e
        # a uniao nao calibra o gabarito de tamanho -- nunca chegavam a rodar.
        merged_tiles=candidate.merged_tiles,
    )


MERGED_CONTAINMENT = 0.70
"""Fração da **união de ladrilhos** que precisa estar dentro do achado de contorno para os
dois falarem do mesmo diagrama (S-81).

`OVERLAP_IOU` não serve aqui, e o caso mostra por quê: na página 168 do `GALLAGHER` a união
de 77×91 pt está **97% dentro** do achado de contorno de 120×120, e mesmo assim o IoU dá 0,41
-- porque a união é bem menor, e a diferença de área infla o denominador. IoU pergunta "as
duas caixas são a mesma?"; aqui a pergunta é "uma está dentro da outra?", que é razão sobre a
menor.

Só vale para união. Imagem embutida declarada continua respondendo a `OVERLAP_IOU`, porque
ali as duas fontes disputam o mesmo retângulo e não há contenção a resolver."""

MERGED_TILES_MARGIN = 0.10
"""Quanto o contorno precisa ser melhor que uma **união de ladrilhos** para tomar o lugar dela.

Só existe margem porque `board_texture_score` tem ruído de reamostragem, o mesmo motivo de
`REFINE_TOLERANCE`. Nos dois casos medidos no `GALLAGHER` a diferença é de 0,40 e 0,58 -- muito
acima disto, e a margem não decide nada ali. Ela existe para o empate."""


def _same_region(
    contour_box: tuple[int, int, int, int],
    embedded_box: tuple[int, int, int, int],
    *,
    merged: bool,
) -> bool:
    """As duas caixas falam do mesmo diagrama?

    Para imagem embutida declarada, é `OVERLAP_IOU` como sempre. Para **união de ladrilhos**,
    é contenção: a união costuma ser bem menor que o tabuleiro inteiro que o contorno acha, e
    o IoU castiga essa diferença de área a ponto de dar 0,41 onde uma caixa está 97% dentro
    da outra. Ver `MERGED_CONTAINMENT`.
    """
    if _bbox_iou(contour_box, embedded_box) > OVERLAP_IOU:
        return True
    if not merged:
        return False

    ax, ay, aw, ah = contour_box
    bx, by, bw, bh = embedded_box
    largura = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    altura = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = largura * altura
    menor = min(aw * ah, bw * bh)
    return menor > 0 and inter / menor >= MERGED_CONTAINMENT


def _contour_wins_over_merged(embedded: DiagramCandidate, contour_rgb: np.ndarray) -> bool:
    """O achado de contorno deve substituir este candidato embutido sobreposto? (S-81)

    Quase sempre **não**, e é a regra desde a S-12: a imagem embutida é uma *declaração* do
    PDF sobre onde está o diagrama, e o contorno é uma inferência sobre pixels. Declaração
    ganha.

    **A união de ladrilhos não é declaração nenhuma** -- ela é uma inferência nossa sobre um
    punhado de imagens que se encostam, e nada garante que o retângulo resultante seja um
    tabuleiro. Medido no `GALLAGHER`: nas páginas 168 e 169 a união produziu caixas de 91 pt
    com textura 0,34 e 0,11 que suprimiram achados de contorno de 120 pt com textura 0,74 e
    0,69 -- e na 169 uma união engoliu **dois** diagramas bons.

    Então, e só então, as duas fontes competem, e a comparação é legítima onde a da S-80 não
    era: aqui os dois recortes são **da mesma região da mesma página**, que é exatamente o que
    a S-38 já faz em `refine_candidate_with_contour`. O que não se pode é comparar a nota de
    livros diferentes, ou contra um piso fixo -- ver a S-80 em `docs/ANALISE_DETECCAO.md`,
    onde um corte absoluto apagava diagramas impecáveis do `Polgar` e do `Reinfeld`.

    Duas guardas antes de tentar: só vale para união (`merged_tiles`), e a união tem de estar
    perdendo por uma margem que não seja ruído de reamostragem.
    """
    if not embedded.merged_tiles:
        return False

    # Do mesmo lado (S-130): a união de ladrilhos e o achado de contorno chegam em resoluções
    # diferentes, e a nota media a resolução junto com a textura.
    nota_embutida, nota_contorno = texture_scores_side_by_side(embedded.board_rgb, contour_rgb)
    if nota_contorno <= nota_embutida + MERGED_TILES_MARGIN:
        return False

    logger.info(
        "Uniao de %d ladrilhos substituida pelo achado de contorno em %s: textura %.4f contra "
        "%.4f. A uniao e inferencia nossa, nao declaracao do PDF.",
        embedded.merged_tiles,
        tuple(round(valor) for valor in embedded.bbox_pdf),
        nota_embutida,
        nota_contorno,
    )
    return True


def _typical_side(sides: list[float], tolerance: float) -> float | None:
    """O lado de diagrama **desta página**, segundo as imagens embutidas dela.

    Era `np.median(sides)`, e a mediana responde a pergunta errada quando a lista tem duas
    populações. Mediana é robusta a *outlier*, não a **bimodalidade**: numa página com um
    glifo de 15 pt e um diagrama de 154 pt ela dá ~85 pt, que não é o tamanho de nada que
    exista ali. Com a tolerância de 30%, a janela aceita vira 59--110 pt, e **todo achado de
    contorno do tamanho real seria recusado** -- o prior de tamanho, que existe para
    *recuperar* diagrama não declarado (4 por livro no `Schiller` e no `Karpov`, medidos na
    S-12), passava a bloqueá-los. Medido em 42 das 1181 páginas do `Secrets`.

    A S-78 tira o glifo da lista antes de ela chegar aqui, então o caso medido não se repete
    por aquela porta. Esta função fecha a **classe**: qualquer página que misture dois
    tamanhos -- diagrama grande de destaque com diagramas pequenos de variante, capa de
    capítulo, fragmento -- caía no mesmo buraco, e nenhuma dessas some com um piso.

    A regra é agrupar os lados por proximidade relativa e tomar o **maior grupo**, com empate
    resolvido pelo lado maior: numa página com um diagrama grande e um pequeno, o gabarito
    tem de ser o do grupo que se repete, e na dúvida o maior, porque diagrama pequeno demais
    já é o que as outras guardas barram.

    Devolve `None` para lista vazia, e o próprio valor quando há um só -- que é o que a
    mediana fazia, e continua certo.
    """
    if not sides:
        return None
    if len(sides) == 1:
        return sides[0]

    ordenados = sorted(sides)
    grupos: list[list[float]] = [[ordenados[0]]]
    for lado in ordenados[1:]:
        referencia = grupos[-1][-1]
        # A mesma tolerancia que decide o filtro decide o agrupamento: dois lados que o filtro
        # aceitaria como o mesmo diagrama sao, por definicao, o mesmo grupo.
        if referencia > 0 and abs(lado - referencia) <= referencia * tolerance:
            grupos[-1].append(lado)
        else:
            grupos.append([lado])

    maior = max(grupos, key=lambda grupo: (len(grupo), max(grupo)))
    return float(np.median(maior))


def _pixel_bbox(bbox_pdf: tuple[float, float, float, float], scale: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox_pdf
    return (
        int(x0 * scale),
        int(y0 * scale),
        max(1, int((x1 - x0) * scale)),
        max(1, int((y1 - y0) * scale)),
    )


def detect_diagrams(
    page: fitz.Page,
    page_rgb: np.ndarray,
    *,
    max_boards: int = DEFAULT_MAX_BOARDS,
    reading_order: ReadingOrder = DEFAULT_READING_ORDER,
    refine_embedded: bool = True,
    size_prior_tolerance: float | None = EMBEDDED_SIZE_TOLERANCE,
    checker_contrast_floor: float | None = MIN_CHECKER_CONTRAST,
    band_board_fill: float | None = BAND_BOARD_FILL,
    rejected: list[RejectedQuad] | None = None,
) -> list[DiagramCandidate]:
    """Todos os diagramas da página, das duas fontes, sem duplicar e em ordem de leitura.

    `page_rgb` é a página já renderizada (o chamador costuma precisar dela de todo jeito, para
    exibir), e é o que alimenta o caminho por contorno.

    `size_prior_tolerance` controla o que fazer com candidato de contorno que **não** casa com
    nenhuma imagem embutida, numa página que tem imagens embutidas. Duas medições delimitaram
    a regra:

    - Unir os dois cegamente traz de volta o falso positivo que a imagem embutida evitava: no
      `1937 Kemeri` a figura que rende `8/8/8/8/8/8/8/8` volta pela porta do contorno.
    - Mas tratar a lista embutida como completa perde diagrama de verdade: no `Schiller` e no
      `Karpov`, 4 diagramas por livro que o contorno acha não têm imagem embutida listada.

    Então a lista embutida não vale como whitelist, vale como **prior de tamanho**: numa
    página cujos diagramas medem ~590 pt de lado, um achado de contorno desse tamanho é
    diagrama e um de tamanho muito diferente é figura. `None` desliga o filtro (união cega).

    O gabarito sai de `_typical_side` e não de `np.median` desde a S-79: a mediana entrega um
    número que não é o tamanho de nada quando a página tem duas populações de tamanho, e o
    prior passa a recusar exatamente o diagrama que ele existe para recuperar.

    `checker_contrast_floor` recusa achado de contorno **sem contraste de casa nenhum** -- a
    foto, o retrato e a moldura da S-143. `None` desliga. Não alcança imagem embutida: ali a
    declaração do PDF continua ganhando, como desde a S-12. Ver `MIN_CHECKER_CONTRAST`.

    `band_board_fill` recusa imagem embutida que é uma **faixa da página** com um diagrama
    dentro, e não o diagrama (S-176). `None` desliga. É a primeira guarda que alcança a fonte
    embutida por conteúdo, e não contradiz a regra da S-12: o PDF declarou uma *imagem* ali, e
    nunca declarou que ela é um diagrama. Ver `BAND_BOARD_FILL` e `is_page_band`.

    `rejected`, quando dado, recebe um `RejectedQuad` por achado de contorno **barrado** --
    tanto os do `detect_boards` (geometria, contraste de casa, score, IoU, teto) quanto os
    daqui: prior de tamanho (S-79), disputa perdida com uma união de ladrilhos (S-81), faixa
    de página (S-176) e o corte final por `max_boards`. É o instrumento da S-131, e as guardas
    deste laço só deixavam rastro em `logger.info`, que ninguém agrega.
    """
    scale_x = page_rgb.shape[1] / page.rect.width if page.rect.width else 1.0
    scale_y = page_rgb.shape[0] / page.rect.height if page.rect.height else 1.0
    scale = (scale_x + scale_y) / 2.0

    embedded = candidates_from_embedded_images(page)
    if refine_embedded or band_board_fill is not None:
        # Uma passada de contorno por candidato, dois consumidores dela (S-176): a guarda de
        # faixa pergunta *o que a imagem e* e o refino pergunta *onde a grade esta*. Medir duas
        # vezes custaria um `get_pixmap` e um `detect_boards` a mais por candidato, em toda
        # pagina de todo livro exportado -- e as duas leem o mesmo achado.
        sobreviventes: list[DiagramCandidate] = []
        for candidate in embedded:
            achado = contour_inside_candidate(page, candidate)
            if achado is None:
                sobreviventes.append(candidate)
                continue
            if band_board_fill is not None and is_page_band(achado, min_fill=band_board_fill):
                caixa = _pixel_bbox(candidate.bbox_pdf, scale)
                # `info` e nao `debug`, pelo mesmo motivo do prior de tamanho: esta linha apaga
                # um candidato inteiro, e sem ela o sintoma e uma caixa que simplesmente deixou
                # de aparecer.
                logger.info(
                    "Imagem embutida %s descartada como faixa da pagina: o tabuleiro achado "
                    "dentro dela ocupa %.0f%% do lado (piso de %.0f%%).",
                    tuple(round(valor) for valor in candidate.bbox_pdf),
                    achado.board_fill * 100,
                    band_board_fill * 100,
                )
                if rejected is not None:
                    rejected.append(
                        RejectedQuad(
                            caixa,
                            round(candidate.detector_score, 4),
                            round(board_checker_contrast(achado.board_rgb), 4),
                            "faixa-da-pagina",
                        )
                    )
                continue
            sobreviventes.append(_apply_refinement(candidate, achado) if refine_embedded else candidate)
        embedded = sobreviventes

    embedded_boxes: list[tuple[int, int, int, int] | None] = [
        _pixel_bbox(candidate.bbox_pdf, scale) for candidate in embedded
    ]
    candidates = list(embedded)

    # Lado tipico do diagrama nesta pagina, em pixels, segundo as imagens embutidas. Serve de
    # gabarito para separar achado de contorno que e diagrama de achado que e figura.
    #
    # Uniao de ladrilhos nao entra no gabarito (S-81), pela mesma razao que ela nao suprime
    # contorno em `_contour_wins_over_merged`: ela e inferencia nossa e nao declaracao do PDF.
    # Medido no `GALLAGHER`: uma uniao de 91 px definia o gabarito da pagina e o prior recusava
    # por tamanho o diagrama de 120 px que o contorno tinha achado -- sem sobreposicao nenhuma
    # entre os dois, entao a guarda de IoU nem chegava a opinar.
    expected_side: float | None = None
    if embedded and size_prior_tolerance is not None:
        sides = [
            float(max(box[2], box[3]))
            for box, candidate in zip(embedded_boxes, embedded, strict=True)
            if box is not None and not candidate.merged_tiles
        ]
        expected_side = _typical_side(sides, size_prior_tolerance)

    # O contorno na pagina inteira e a unica fonte quando nao ha imagem embutida -- o caso da
    # maioria do acervo: 12 dos 27 PDFs sao scan de pagina inteira e 2 sao vetoriais.
    def _recusado(caixa: tuple[int, int, int, int], contraste: float, motivo: str) -> None:
        if rejected is not None:
            rejected.append(RejectedQuad(caixa, 0.5, round(contraste, 4), motivo))

    def _caixa_do_quad(quad: np.ndarray | None) -> tuple[int, int, int, int]:
        if quad is None:
            return (0, 0, page_rgb.shape[1], page_rgb.shape[0])
        xs, ys = quad[:, 0], quad[:, 1]
        return (int(xs.min()), int(ys.min()), max(1, int(xs.max() - xs.min())), max(1, int(ys.max() - ys.min())))

    # O piso de contraste de casa da S-143 vai **dentro** do `detect_boards` (S-160), e nao
    # sobre o que ele devolve. A regra que este laco enunciava continua sendo a certa -- guarda
    # que julga o que a coisa e vem antes de guarda que julga com quem ela compete --, mas o
    # laco so alcanca os sobreviventes da ordenacao por score e da supressao por IoU que
    # acontecem la dentro. Aplicada aqui, ela removia o falso positivo **depois** de ele ja ter
    # comido o diagrama de verdade. Ver `board_detection.MIN_CHECKER_CONTRAST`.
    for board_rgb, quad in detect_boards(
        page_rgb,
        max_boards=max_boards,
        reading_order=reading_order,
        rejected=rejected,
        checker_floor=checker_contrast_floor,
    ):
        box = _caixa_do_quad(quad)

        conflito = next(
            (
                indice
                for indice, outra in enumerate(embedded_boxes)
                # `None` é a lápide de quem já saiu da lista (S-359): a caixa `(0, 0, 1, 1)` que
                # marcava isso antes **é uma caixa**, e `_same_region` a compara como tal --
                # qualquer contorno ancorado na origem da página voltava a "casar" com um
                # candidato que não existe mais, e o `candidates.remove` abaixo levantaria sobre
                # ele. Ancorado na origem não é caso raro: é a moldura da página inteira.
                if outra is not None and _same_region(box, outra, merged=bool(embedded[indice].merged_tiles))
            ),
            None,
        )
        if conflito is not None:
            if not _contour_wins_over_merged(embedded[conflito], board_rgb):
                # O contraste é **medido**, e não zero (S-364): o recorte está aqui, e
                # `RejectedQuad.checker` documenta o `0.0` como "a recusa foi antes de haver
                # recorte". Gravar zero sobre um recorte existente faz o censo de recusas dizer
                # que o candidato não tinha contraste nenhum -- que é uma afirmação, e falsa.
                _recusado(box, board_checker_contrast(board_rgb), "perdeu-para-embutido")
                continue
            # O contorno ganhou de uma uniao de ladrilhos: ele passa a ser o candidato daquela
            # regiao, e a uniao sai. Sem remover a caixa, o proximo achado de contorno da mesma
            # regiao seria suprimido por um candidato que ja nao esta na lista.
            candidates.remove(embedded[conflito])
            embedded_boxes[conflito] = None

        if expected_side is not None and size_prior_tolerance is not None:
            side = max(box[2], box[3])
            if abs(side - expected_side) > expected_side * size_prior_tolerance:
                # `info` e nao `debug` (S-79): esta linha apaga um diagrama em potencial, e a
                # unica evidencia de que ela agiu. Quando o gabarito estava envenenado, o
                # sintoma era um diagrama que simplesmente nao aparecia na tela -- sem log,
                # sem numero, sem nada a que voltar.
                logger.info(
                    "Contorno descartado por tamanho: %d px contra gabarito de %.0f px da pagina (tolerancia %.0f%%).",
                    side,
                    expected_side,
                    size_prior_tolerance * 100,
                )
                _recusado(box, board_checker_contrast(board_rgb), "prior-de-tamanho")
                continue

        candidates.append(
            DiagramCandidate(
                board_rgb=board_rgb,
                bbox_pdf=(box[0] / scale, box[1] / scale, (box[0] + box[2]) / scale, (box[1] + box[3]) / scale),
                source="contour",
                detector_score=0.5,
                native_size=(BOARD_SIZE, BOARD_SIZE),
            )
        )

    # **Quem passa do teto sai por score, e não por posição na página (S-362).** O corte era
    # aplicado depois da ordenação de leitura: numa página de treze diagramas com teto de doze,
    # o que sumia era o **último a ser lido** -- o do canto inferior direito --, e não o pior. A
    # S-14 já tinha visto o sintoma pela tela ("o nono pode ser o do canto superior direito").
    # A ordem de leitura continua sendo a da saída; ela só deixou de decidir *quem* fica.
    por_score = sorted(candidates, key=lambda c: -float(c.detector_score))
    escolhidos = por_score[:max_boards]
    cortados = por_score[max_boards:]
    ordered = _order_candidates(escolhidos, scale=scale, reading_order=reading_order)

    if cortados:
        # `warning`, como em `detect_boards` (S-362): o corte por contagem é a recusa que mais
        # engana -- o candidato passou por todas as guardas e sumiu porque não coube --, e era a
        # única sem nada no log. Quem vê doze diagramas numa página de treze precisa saber por quê.
        logger.warning(
            "max_boards=%d cortou %d candidato(s) que passaram nas guardas (scores %s contra "
            "%.4f do último aceito). Se a página tem mais diagramas que isso, aumente "
            "'Max diagramas'.",
            max_boards,
            len(cortados),
            ", ".join(f"{c.detector_score:.4f}" for c in cortados[:4]),
            escolhidos[-1].detector_score if escolhidos else 0.0,
        )
    if rejected is not None:
        # O corte final tambem e uma recusa, e e a que mais engana: o candidato passou por
        # todas as guardas e sumiu por contagem.
        for excedente in cortados:
            x0, y0, x1, y1 = excedente.bbox_pdf
            caixa = (int(x0 * scale), int(y0 * scale), max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale)))
            rejected.append(RejectedQuad(caixa, excedente.detector_score, 0.0, "teto-da-pagina"))
    return ordered


def detect_diagrams_in_pdf_page(
    pdf_source: PdfSource,
    page_index: int,
    page_rgb: np.ndarray,
    *,
    max_boards: int = DEFAULT_MAX_BOARDS,
    reading_order: ReadingOrder = DEFAULT_READING_ORDER,
) -> list[DiagramCandidate]:
    """`detect_diagrams` para quem tem o caminho do PDF, e não um `fitz.Page` aberto.

    Existe para que a GUI e a exportação usem **o mesmo** detector. Elas já divergiam na
    ordem de leitura antes da S-14, com o resultado de o "diagrama 2" da tela não ser o
    `[Diagram "2"]` do PGN; repetir o erro na fonte de detecção seria pior, porque aí a
    divergência é no recorte e não só na numeração.

    Abre o documento, ou o **empresta** quando `pdf_source` já é um `OpenPdf` (S-61): era
    aqui que morria uma das três aberturas por página. A assinatura continua aceitando caminho,
    que é a porta dos CLIs.
    """
    from ..pdf_io import open_document

    with open_document(pdf_source) as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"Página {page_index} fora do intervalo (0..{doc.page_count - 1})")
        return detect_diagrams(
            doc[page_index],
            page_rgb,
            max_boards=max_boards,
            reading_order=reading_order,
        )


def _order_candidates(
    candidates: list[DiagramCandidate],
    *,
    scale: float,
    reading_order: ReadingOrder,
) -> list[DiagramCandidate]:
    """Ordena candidatos das duas fontes juntos, com a mesma regra da S-14.

    Reaproveita `_sort_selected_candidates` em vez de reimplementar o agrupamento por coluna:
    são a mesma decisão, e duas implementações divergiriam com o tempo -- que é exatamente o
    bug que a S-14 corrigiu.
    """
    if len(candidates) <= 1:
        return candidates

    # `_sort_selected_candidates` ordena tuplas (quad, score, bbox) in-place; o indice viaja
    # no lugar do quad para sabermos onde cada candidato foi parar.
    sortable = [
        (np.array([[index, 0]], dtype=np.float32), candidate.detector_score, _pixel_bbox(candidate.bbox_pdf, scale))
        for index, candidate in enumerate(candidates)
    ]
    _sort_selected_candidates(sortable, reading_order)
    return [candidates[int(item[0][0, 0])] for item in sortable]
