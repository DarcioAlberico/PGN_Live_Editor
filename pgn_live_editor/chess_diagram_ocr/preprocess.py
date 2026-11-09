"""Normalização do tabuleiro antes de virar 64 casas (S-39).

**O caso que motivou.** Euwe Band 1-2, página 25: o recorte está perfeito -- grade alinhada,
tabuleiro inteiro, `board_texture_score` de 1,0000 -- e a leitura sai com `min_confidence`
de **0,0000** e posição errada. Não é detecção nem capacidade do modelo. É que as casas
escuras daquele livro são **hachuradas com linhas diagonais** em vez de cinza sólido, sobre
papel amarelado de 1956, e essa textura não existe no domínio de treino.

O que o modelo recebia até aqui, em `model.preprocess_cell_to_tensor`:

    cvtColor(RGB2GRAY) → resize(64×64) → /255

Sem normalização de iluminação, sem equalização, sem supressão de trama, sem correção de
rotação residual. A hachura sobrevive ao downsample como estrutura de alta frequência dentro
de cada casa.

**A restrição que organiza o desenho: ser quase identidade num diagrama limpo.** Medido no
conjunto de campo, o `Polgar` sai a 1,000 de taxa de exportação e o `Karpov` a 0,857. Um
normalizador que "melhorasse" esses estragaria mais do que conserta. Cada etapa aqui é ou
uma operação que não faz nada quando não há o que corrigir (o campo plano de uma imagem
uniforme é uma constante; o deskew de uma grade alinhada é 0°), ou tem a força limitada por
esse critério.

**Por que no tabuleiro e não na casa.** Iluminação e trama são propriedades da página.
Estimá-las sobre 100×100 px de uma casa é estimá-las sobre ruído -- e uma casa vazia não tem
como saber se está clara porque é clara ou porque o scanner iluminou demais ali.

**A armadilha do domínio, e por que ela não é hipotética.** Normalizar na inferência e não no
treino cria um segundo desencontro de domínio -- o inverso do atual. As 3.289 amostras de
`data/samples/` foram gravadas cruas. A saída é normalizar **na leitura**, em
`BoardFenDataset._load_board`, com os mesmos parâmetros da inferência: o disco continua com o
original, e trocar de normalização é retreinar, não re-anotar.

E a consequência que não pode passar em silêncio: um checkpoint treinado com normalização e
outro sem são **incompatíveis**, e `ArchConfig.version` precisa distingui-los. Sem isso volta
o defeito que a S-27 corrigiu, com outra roupa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MAX_SKEW_DEGREES = 3.0
"""Sobra de rotação que vale a pena procurar.

O warp da S-12 corrige perspectiva; o que sobra é fração de grau a poucos graus. Procurar
além disso não acha rotação -- acha o ângulo em que a busca casa com outra coisa, e girar
um tabuleiro alinhado por 8° é estragá-lo.
"""

SKEW_STEP_DEGREES = 0.25
SKEW_PROBE_SIDE = 256
"""Lado em que a busca de rotação roda. 32 px por casa basta para a periodicidade da grade,
e 25 rotações de 256×256 custam ~4 ms contra ~90 ms em 800×800."""

MIN_SKEW_GAIN = 1.02
"""Ganho mínimo de periodicidade para valer girar.

Sem isto, o deskew gira todo tabuleiro por 0,25° em busca de um ganho de 0,1% que é ruído --
e cada rotação reamostra a imagem, que é custo real e sem retorno.
"""


@dataclass(frozen=True)
class NormalizerConfig:
    """As etapas, cada uma desligável.

    **Todas desligadas por padrão, e por medição** (docs/EXPERIMENTS_FASE7.md, S-39). Não é
    preguiça nem "fica para depois": as quatro foram medidas no conjunto de campo, uma por
    vez e em combinação, e o resultado foi

    | variante | taxa de exportação |
    |---|---|
    | **nenhuma** | **0,6842** |
    | deskew, campo plano, CLAHE (isolados ou juntos) | 0,6842 — idênticos |
    | qualquer uma com supressão de trama | **0,0000** |

    Dois motivos, e nenhum era o esperado:

    1. **Campo plano e CLAHE não mudam nada** porque
       `training.build_train_transform` já usa `ColorJitter(brightness=0.3, contrast=0.3)`:
       o modelo foi treinado a ignorar exatamente esse tipo de ajuste global. Eles alteram a
       imagem (3,7 e 3,0 de diferença média de pixel) e não alteram a leitura.
    2. **A trama não é separável da peça por escala.** A hachura do `Euwe` tem período de
       ~12,5 px numa casa de 100 px, que é a ordem de grandeza do traço da peça. Medido por
       mediana e por morfologia: o kernel que começa a mover o Euwe (0,000 → 0,10, ainda 8×
       abaixo do gate) é o mesmo que derruba `Karpov` e `Polgar` de 1,000 para 0,05–0,79.

    Fica igual ao `TTA_ENABLED` e ao `APPLY_CALIBRATED_TEMPERATURE` da Fase 5: medido,
    desligado, documentado, e disponível para quem tiver outro acervo.
    """

    deskew: bool = False
    flat_field: bool = False
    hatch_suppression: bool = False
    clahe: bool = False

    clahe_clip: float = 2.0
    flat_field_sigma_ratio: float = 0.25
    """Sigma do desfoque do campo plano, como fração do lado do tabuleiro.

    Precisa ser **muito maior que a casa**: com sigma ~ um lado de casa, o desfoque segue o
    próprio padrão xadrezado e dividir por ele apagaria o contraste entre casas claras e
    escuras -- que é justamente o sinal. 1/4 do lado são 2 casas, e o que sobra é o campo de
    iluminação.
    """

    hatch_kernel_ratio: float = 0.045
    """Lado do filtro de mediana, como fração do lado do tabuleiro.

    A mediana remove linha fina e preserva traço grosso: numa janela maior que o período da
    hachura, os pixels da trama são minoria e a casa volta a ser lisa. A peça, que é sólida,
    atravessa. ~0,045 de 800 px são 36 px, ou ~1/3 de casa.
    """

    @property
    def version(self) -> str:
        """Identidade da normalização, para entrar em `ArchConfig.version`.

        `norm0` é "nenhuma etapa", que é o pipeline anterior à S-39 -- e é o que um
        checkpoint antigo, que não registra nada, tem de continuar significando.
        """
        ativos = "".join(
            letra
            for letra, ligado in (
                ("d", self.deskew),
                ("f", self.flat_field),
                ("h", self.hatch_suppression),
                ("c", self.clahe),
            )
            if ligado
        )
        return f"norm{ativos}" if ativos else "norm0"

    @property
    def is_identity(self) -> bool:
        return not (self.deskew or self.flat_field or self.hatch_suppression or self.clahe)

    @classmethod
    def from_version(cls, version: str) -> NormalizerConfig:
        if not version.startswith("norm"):
            raise ValueError(f"Versão de normalização deve começar com 'norm'; recebido {version!r}.")
        letras = version[4:]
        if letras in ("", "0"):
            return cls()
        desconhecidas = set(letras) - set("dfhc")
        if desconhecidas:
            raise ValueError(f"Etapas desconhecidas em {version!r}: {''.join(sorted(desconhecidas))}")
        return cls(
            deskew="d" in letras,
            flat_field="f" in letras,
            hatch_suppression="h" in letras,
            clahe="c" in letras,
        )


IDENTITY = NormalizerConfig()


def _odd(value: int, minimum: int = 3) -> int:
    return max(minimum, value | 1)


def _grid_periodicity(gray: np.ndarray) -> float:
    """Quanto os gradientes desta imagem se organizam numa grade 8×8.

    É o alvo que o deskew maximiza. Somar o gradiente por coluna e por linha e olhar a
    variância dos perfis mede exatamente o que uma grade alinhada tem e uma torta não: picos
    nítidos nas oito divisões, em vez de picos borrados por várias colunas.
    """
    gx = np.abs(np.diff(gray.astype(np.float32), axis=1)).mean(axis=0)
    gy = np.abs(np.diff(gray.astype(np.float32), axis=0)).mean(axis=1)
    return float(gx.var() + gy.var())


def estimate_skew(board_rgb: np.ndarray, *, max_degrees: float = MAX_SKEW_DEGREES) -> float:
    """Sobra de rotação do tabuleiro, em graus. `0.0` quando não vale girar.

    Busca direta em vez de Hough: o que se quer é o ângulo que deixa a **grade** mais nítida,
    e medir isso é mais barato e mais direto que achar retas e depois inferir o ângulo delas.
    A busca é limitada a `MAX_SKEW_DEGREES` porque além disso ela deixa de achar rotação e
    passa a achar o ângulo em que o critério casa com outra coisa.
    """
    gray = cv2.cvtColor(board_rgb, cv2.COLOR_RGB2GRAY)
    probe = cv2.resize(gray, (SKEW_PROBE_SIDE, SKEW_PROBE_SIDE), interpolation=cv2.INTER_AREA)
    centro = (SKEW_PROBE_SIDE / 2, SKEW_PROBE_SIDE / 2)

    base = _grid_periodicity(probe)
    melhor_angulo, melhor_valor = 0.0, base

    passo = SKEW_STEP_DEGREES
    angulo = -max_degrees
    while angulo <= max_degrees + 1e-9:
        if abs(angulo) > 1e-9:
            matriz = cv2.getRotationMatrix2D(centro, angulo, 1.0)
            girada = cv2.warpAffine(
                probe,
                matriz,
                (SKEW_PROBE_SIDE, SKEW_PROBE_SIDE),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
            valor = _grid_periodicity(girada)
            if valor > melhor_valor:
                melhor_angulo, melhor_valor = angulo, valor
        angulo += passo

    if melhor_valor < base * MIN_SKEW_GAIN:
        return 0.0
    return melhor_angulo


def _apply_deskew(board_rgb: np.ndarray) -> np.ndarray:
    angulo = estimate_skew(board_rgb)
    if angulo == 0.0:
        return board_rgb
    altura, largura = board_rgb.shape[:2]
    matriz = cv2.getRotationMatrix2D((largura / 2, altura / 2), angulo, 1.0)
    return cv2.warpAffine(board_rgb, matriz, (largura, altura), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _apply_flat_field(board_rgb: np.ndarray, sigma_ratio: float) -> np.ndarray:
    """Divide pela própria imagem muito desfocada: some o gradiente de iluminação e o papel.

    Numa imagem já uniforme o desfoque é praticamente uma constante, e dividir por constante
    e reescalar é identidade -- que é a restrição desta etapa.
    """
    lado = max(board_rgb.shape[:2])
    sigma = max(4.0, lado * sigma_ratio)
    fundo: np.ndarray = cv2.GaussianBlur(board_rgb.astype(np.float32), (0, 0), sigmaX=sigma, sigmaY=sigma)
    corrigida = board_rgb.astype(np.float32) / np.maximum(fundo, 1.0)
    corrigida *= float(fundo.mean())
    return np.clip(corrigida, 0, 255).astype(np.uint8)


def _apply_hatch_suppression(board_rgb: np.ndarray, kernel_ratio: float) -> np.ndarray:
    """Mediana com janela maior que o período da trama: linha fina some, traço grosso fica."""
    lado = max(board_rgb.shape[:2])
    kernel = _odd(int(lado * kernel_ratio))
    # `medianBlur` acima de 5 exige uint8, que é o que temos, mas fica caro em kernel grande;
    # o teto mantém o custo previsível num tabuleiro de 800 px.
    kernel = min(kernel, 41)
    return cv2.medianBlur(board_rgb, kernel)


def _apply_clahe(board_rgb: np.ndarray, clip: float) -> np.ndarray:
    """CLAHE sobre a luminância, preservando a cor.

    `tileGridSize=(8, 8)` cai exatamente numa casa por tile, o que é deliberado: o que se
    quer equalizar é o contraste **dentro** da casa, e um tile por casa é a granularidade em
    que "peça contra fundo" é a única coisa na janela.
    """
    lab = cv2.cvtColor(board_rgb, cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


class BoardNormalizer:
    """Normaliza o tabuleiro inteiro antes do corte em casas.

    Sem estado entre chamadas de propósito: a mesma instância é usada da thread de OCR, da
    de exportação e da de treino, e guardar qualquer coisa aqui reintroduziria a corrida que
    a S-31 fechou no modelo.
    """

    def __init__(self, config: NormalizerConfig = IDENTITY) -> None:
        self.config = config

    @property
    def version(self) -> str:
        return self.config.version

    def normalize(self, board_rgb: np.ndarray) -> np.ndarray:
        """A imagem normalizada, ou a mesma imagem quando nenhuma etapa está ligada.

        A ordem não é arbitrária. O deskew vem primeiro porque as três seguintes medem
        estatísticas locais, e medi-las sobre uma grade torta as mede sobre a borda errada.
        O campo plano vem antes da trama porque a mediana é não-linear e um gradiente de
        iluminação faria a janela dela decidir diferente em cada canto. O CLAHE vem por
        último porque é o único que **amplifica**, e amplificar antes de remover a trama
        amplificaria a trama.
        """
        if self.config.is_identity:
            return board_rgb

        imagem = board_rgb
        if self.config.deskew:
            imagem = _apply_deskew(imagem)
        if self.config.flat_field:
            imagem = _apply_flat_field(imagem, self.config.flat_field_sigma_ratio)
        if self.config.hatch_suppression:
            imagem = _apply_hatch_suppression(imagem, self.config.hatch_kernel_ratio)
        if self.config.clahe:
            imagem = _apply_clahe(imagem, self.config.clahe_clip)
        return imagem
