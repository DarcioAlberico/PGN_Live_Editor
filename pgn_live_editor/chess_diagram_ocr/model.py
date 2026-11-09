"""O classificador de peças e a descrição da sua arquitetura.

Até a Fase 5 a arquitetura era literal: uma CNN fixa, entrada 64×64 em tons de cinza,
cabeça `Linear(8192, 256)`. Nenhuma dessas três escolhas tinha sido medida contra
alternativa nenhuma (S-29), e o checkpoint não registrava qual delas produziu os pesos --
então trocar qualquer uma silenciosamente descartava metade dos pesos ao carregar, porque
`load_state_dict` rodava com `strict=False` (S-27).

`ArchConfig` resolve as duas coisas com o mesmo objeto: descreve a arquitetura para
`build_model` e vira a string `arch_version` gravada no checkpoint. Um checkpoint de
`cnn-gray-64-linear` carregado num `cnn-rgb-48-gap` passa a falhar alto, com o nome das
duas arquiteturas na mensagem.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import cv2
import numpy as np
import torch
import torch.nn as nn

from .config import MODEL_IMAGE_SIZE, PIECE_CLASSES

Channels = Literal["gray", "rgb"]
Head = Literal["linear", "gap", "board"]
Backbone = Literal["cnn", "mobilenet_v3_small"]

SQUARES_PER_BOARD = 64
BOARD_EMBEDDING_DIM = 256
"""Largura do embedding por casa que a cabeça por tabuleiro consome (S-62b).

256 e não outro número porque é exatamente a largura da `Linear(8192, 256)` que a cabeça
`linear` já usava: a cabeça nova troca o que vem **depois** dessa projeção, e manter a largura
faz do item uma troca de cabeça e não uma troca de modelo."""

COORDINATE_CHANNELS = 3
"""Paridade da casa, fila normalizada, coluna normalizada (S-62a)."""


@dataclass(frozen=True)
class ArchConfig:
    """Os fatores que a S-29 manda medir, um por vez.

    Os defaults são a arquitetura que produziu o baseline de 0,9906 -- para que
    `ArchConfig()` continue sendo exatamente o modelo que está em produção hoje.
    """

    backbone: Backbone = "cnn"
    channels: Channels = "gray"
    image_size: int = MODEL_IMAGE_SIZE
    head: Head = "linear"

    coords: bool = False
    """Canais de coordenada e paridade na entrada da casa (S-62a).

    Desligado por padrão pela mesma regra dos outros três fatores: `ArchConfig()` continua
    sendo o modelo em produção. Ligado, a entrada ganha **três planos constantes** -- paridade
    da casa, fila e coluna normalizadas -- e o modelo passa a saber onde está a casa que está
    olhando, que é metade do que o `decode.py` usa depois para consertar o argmax.
    """

    def __post_init__(self) -> None:
        if self.image_size % 8 != 0 or self.image_size <= 0:
            raise ValueError(
                f"image_size deve ser múltiplo positivo de 8 (três MaxPool2d); recebido {self.image_size}."
            )

    @property
    def image_channels(self) -> int:
        """Canais que vêm da imagem. É o que `preprocess_cell_to_tensor` produz."""
        return 1 if self.channels == "gray" else 3

    @property
    def in_channels(self) -> int:
        return self.image_channels + (COORDINATE_CHANNELS if self.coords else 0)

    @property
    def version(self) -> str:
        """Identidade da arquitetura, gravada no checkpoint como `arch_version`.

        O sufixo `-coords` é acrescentado, e não intercalado, para que toda `arch_version`
        gravada antes da S-62 continue interpretável -- e para que um checkpoint sem os canais
        carregado num pipeline com eles falhe alto, com as duas versões na mensagem, que é a
        garantia que a S-27 estabeleceu.
        """
        base = f"{self.backbone}-{self.channels}-{self.image_size}-{self.head}"
        return f"{base}-coords" if self.coords else base

    @classmethod
    def from_version(cls, version: str) -> ArchConfig:
        coords = version.endswith("-coords")
        if coords:
            version = version[: -len("-coords")]
        backbone, channels, size, head = version.rsplit("-", 3)
        return cls(backbone=backbone, channels=channels, image_size=int(size), head=head, coords=coords)  # type: ignore[arg-type]


DEFAULT_ARCH = ArchConfig()


class PieceClassifier(nn.Module):
    """CNN de três blocos convolucionais.

    `temperature` é um atributo simples, não um buffer: ela é gravada no checkpoint como
    chave própria e **fora** do `state_dict`, de propósito. Como buffer ela entraria no
    `state_dict` e todo checkpoint anterior à S-28 passaria a falhar sob `strict=True` --
    inclusive `piece_classifier_baseline.pt`, que é a única forma de reproduzir os números
    do BASELINE.md. Ver `calibration.py`.
    """

    def __init__(self, arch: ArchConfig = DEFAULT_ARCH, num_classes: int = len(PIECE_CLASSES)) -> None:
        super().__init__()
        self.arch = arch
        self.temperature: float = 1.0
        self.features = nn.Sequential(
            nn.Conv2d(arch.in_channels, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )

        if arch.head == "gap":
            # ~150 k parametros contra 2,1 M: a cabeca linear concentra 96% do modelo.
            self.classifier: nn.Module = nn.Sequential(
                nn.AdaptiveAvgPool2d(1),
                nn.Flatten(),
                nn.Dropout(p=0.25),
                nn.Linear(128, num_classes),
            )
        else:
            side = arch.image_size // 8
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(128 * side * side, 256),
                nn.ReLU(inplace=True),
                nn.Dropout(p=0.25),
                nn.Linear(256, num_classes),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Devolve logits crus. A temperatura é aplicada na inferência, não aqui --
        dividir no treino mudaria o gradiente da loss."""
        x = self.features(x)
        return self.classifier(x)


class MobileNetClassifier(nn.Module):
    """MobileNetV3-Small adaptada às 13 classes (fator "backbone" da S-29)."""

    def __init__(self, arch: ArchConfig, num_classes: int = len(PIECE_CLASSES), *, pretrained: bool = True) -> None:
        super().__init__()
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        self.arch = arch
        self.temperature: float = 1.0
        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
        self.net = mobilenet_v3_small(weights=weights)
        if arch.in_channels != 3:
            # A primeira conv espera 3 canais. Somar os pesos ao longo do eixo de entrada
            # preserva a resposta do filtro para uma imagem cinza replicada em RGB, que e o
            # que a rede pre-treinada viu -- reinicializar jogaria fora o pre-treino.
            #
            # Com os canais da S-62a a entrada pode ter 4 ou 6: os primeiros recebem o
            # pre-treino como antes e os de coordenada entram **zerados**, para que o modelo
            # comece exatamente onde o de hoje comeca e aprenda a usa-los, em vez de nascer
            # com ruido numa entrada que ele nunca viu.
            first = self.net.features[0][0]
            replacement = nn.Conv2d(
                arch.in_channels, first.out_channels, first.kernel_size, first.stride, first.padding, bias=False
            )
            with torch.no_grad():
                replacement.weight.zero_()
                if arch.image_channels == 1:
                    replacement.weight[:, :1].copy_(first.weight.sum(dim=1, keepdim=True))
                else:
                    replacement.weight[:, :3].copy_(first.weight)
            self.net.features[0][0] = replacement
        in_features = self.net.classifier[-1].in_features
        self.net.classifier[-1] = nn.Linear(in_features, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class BoardHeadClassifier(nn.Module):
    """O mesmo tronco por casa, e uma cabeça que decide as 64 juntas (S-62b).

    **O que o item ataca.** O modelo de hoje decide 64 vezes de forma independente, e é por
    isso que `decode.py` existe: o argmax não tem obrigação nenhuma de produzir posição legal,
    e a decodificação com restrições é uma busca *posterior* que conserta o que o modelo não
    sabia que estava errado. Um rei branco em e1 torna outro rei branco em g3 impossível, e o
    modelo que olha g3 não sabe de e1.

    **O que muda e o que não muda.** O tronco convolucional é o mesmo, a projeção para 256
    dimensões é a mesma, e as 64 saídas continuam sendo 64 logits de 13 classes. Entre a
    projeção e a classificação entram dois blocos de auto-atenção sobre a sequência das 64
    casas -- cada casa passa a poder olhar as outras 63 antes de decidir.

    **O decodificador não sai.** Ele continua sendo a garantia dura; um modelo que *prefere*
    posições legais ainda emite ilegais. O que este item promete mudar é a **frequência** com
    que ele precisa reparar, e `DecodeResult.changed_squares` é a métrica.

    **A forma da entrada é o contrato com o resto do projeto.** `forward` aceita as duas
    formas -- `(B, 64, C, S, S)`, que é o que o treino por tabuleiro entrega, e
    `(N, C, S, S)` com `N` múltiplo de 64, que é o que `board_probabilities` e `evaluation`
    já montam -- e **sempre devolve `(N, 13)` achatado**. Devolver `(B, 64, 13)` obrigaria
    cada consumidor a saber qual cabeça está carregada, e a cabeça é justamente o que se quer
    poder trocar sem tocar em ninguém.
    """

    def __init__(self, arch: ArchConfig, num_classes: int = len(PIECE_CLASSES), *, layers: int = 2) -> None:
        super().__init__()
        self.arch = arch
        self.temperature: float = 1.0
        self.features = nn.Sequential(
            nn.Conv2d(arch.in_channels, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )
        side = arch.image_size // 8
        self.embed = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * side * side, BOARD_EMBEDDING_DIM),
            nn.ReLU(inplace=True),
            nn.Dropout(p=0.25),
        )
        # Posicao aprendida, mesmo com os canais da S-62a ligados: eles dizem a coordenada
        # **a cada casa**, e isto diz a posicao **na sequencia**, que e o que a atencao usa
        # para saber que o token 4 e vizinho do 5. Sao 64x256 = 16 k parametros.
        self.position = nn.Parameter(torch.zeros(SQUARES_PER_BOARD, BOARD_EMBEDDING_DIM))
        camada = nn.TransformerEncoderLayer(
            d_model=BOARD_EMBEDDING_DIM,
            nhead=4,
            dim_feedforward=512,
            dropout=0.1,
            batch_first=True,
            norm_first=True,
        )
        # `enable_nested_tensor=False` porque com `norm_first=True` o torch ja o desliga e
        # avisa a cada construcao; declarar a escolha cala o aviso sem mudar nada. A sequencia
        # aqui tem 64 tokens fixos e nenhum padding -- nao ha o que o caminho aninhado ganhe.
        self.mixer = nn.TransformerEncoder(camada, num_layers=layers, enable_nested_tensor=False)
        self.classifier = nn.Linear(BOARD_EMBEDDING_DIM, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim == 5:
            if x.shape[1] != SQUARES_PER_BOARD:
                raise ValueError(f"Esperado {SQUARES_PER_BOARD} casas por tabuleiro, recebido {x.shape[1]}.")
            cells = x.flatten(0, 1)
        elif x.ndim == 4:
            if x.shape[0] % SQUARES_PER_BOARD != 0:
                # Falha alto de proposito. Um lote solto de casas nao e reagrupavel em
                # tabuleiros, e adivinhar produziria uma leitura errada sem erro nenhum.
                raise ValueError(
                    f"A cabeça por tabuleiro precisa das 64 casas juntas e em ordem; "
                    f"recebeu um lote de {x.shape[0]}, que não é múltiplo de {SQUARES_PER_BOARD}."
                )
            cells = x
        else:
            raise ValueError(f"Entrada com {x.ndim} dimensões; esperado 4 ou 5.")

        # `-1` e nao o numero de tabuleiros calculado em Python: sob tracing (o exportador
        # ONNX da S-30) um inteiro do Python vira constante no grafo, e o eixo de lote que o
        # `dynamic_axes` promete ser dinamico deixaria de ser -- silenciosamente.
        embeddings = self.embed(self.features(cells)).reshape(-1, SQUARES_PER_BOARD, BOARD_EMBEDDING_DIM)
        mixed = self.mixer(embeddings + self.position)
        return self.classifier(mixed).reshape(-1, self.classifier.out_features)


def build_model(arch: ArchConfig = DEFAULT_ARCH, *, pretrained: bool = True) -> nn.Module:
    if arch.head == "board":
        if arch.backbone != "cnn":
            raise ValueError("A cabeça por tabuleiro (S-62b) só existe sobre o tronco 'cnn'.")
        return BoardHeadClassifier(arch)
    if arch.backbone == "mobilenet_v3_small":
        return MobileNetClassifier(arch, pretrained=pretrained)
    return PieceClassifier(arch)


def preprocess_cell_to_tensor(cell_rgb: np.ndarray, arch: ArchConfig = DEFAULT_ARCH) -> torch.Tensor:
    """Recorte de casa (RGB, uint8) → tensor (C, S, S) em [0, 1].

    O nome do parâmetro dizia `cell_bgr` e o corpo convertia de `COLOR_RGB2GRAY`: quem
    passasse BGR de fato teria os canais trocados. Todos os chamadores sempre passaram RGB.
    """
    if arch.channels == "gray":
        image = cv2.cvtColor(cell_rgb, cv2.COLOR_RGB2GRAY)
    else:
        image = cell_rgb

    resized = cv2.resize(image, (arch.image_size, arch.image_size), interpolation=cv2.INTER_AREA)
    normalized = resized.astype(np.float32) / 255.0
    if arch.channels == "gray":
        return torch.from_numpy(normalized).unsqueeze(0)
    return torch.from_numpy(np.ascontiguousarray(normalized.transpose(2, 0, 1)))


@lru_cache(maxsize=256)
def coordinate_channels(square_index: int, size: int) -> torch.Tensor:
    """Os três planos constantes da casa `square_index` (S-62a): paridade, fila, coluna.

    `square_index` é a ordem de leitura da vista atual -- 0 é o canto superior esquerdo do
    recorte que o modelo vai ler. Não é a casa do tabuleiro real, e essa distinção é o que faz
    a orientação continuar funcionando: `predict_board(rotate_180=True)` gira a imagem e lê os
    64 recortes na ordem da vista girada, e o modelo tem de receber a coordenada **daquilo que
    está vendo**, não da posição impressa no papel.

    Planos constantes e não gradientes espaciais: a informação é da casa inteira, e um valor
    por pixel deixa a convolução tratá-la como qualquer outro canal de entrada. O custo é ~1%
    dos parâmetros, todo na primeira conv.

    Em cache porque são 64 tensores imutáveis reconstruídos 64 vezes por tabuleiro -- e
    imutáveis mesmo: quem os recebe não pode escrever neles. Ver `with_coordinate_channels`,
    que os concatena (o `cat` copia) em vez de os expor.
    """
    row, col = divmod(square_index, 8)
    # Paridade: 0 na casa clara, 1 na escura. Com `row=0` sendo a 8a fila, a casa 0 e a8 --
    # clara no tabuleiro impresso, e a convencao importa pouco desde que seja consistente.
    valores = ((row + col) % 2, row / 7.0, col / 7.0)
    planos = torch.empty((COORDINATE_CHANNELS, size, size), dtype=torch.float32)
    for canal, valor in enumerate(valores):
        planos[canal].fill_(float(valor))
    return planos


def with_coordinate_channels(cell: torch.Tensor, square_index: int, arch: ArchConfig = DEFAULT_ARCH) -> torch.Tensor:
    """Concatena os canais da S-62a a um recorte já preprocessado. Sem `coords`, devolve-o intacto.

    Roda **depois** do aumento, e isso não é detalhe de ordem. `RandomAffine` translada e
    preenche a borda com zero; aplicada sobre um plano constante de valor 0,71 ela produziria
    uma coordenada que varia dentro da casa e mente na margem. O aumento existe para degradar
    a imagem, e a coordenada não é imagem.
    """
    if not arch.coords:
        return cell
    return torch.cat((cell, coordinate_channels(square_index, cell.shape[-1])), dim=0)
