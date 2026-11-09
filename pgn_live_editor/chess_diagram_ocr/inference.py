from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .board_detection import split_board_into_cells
from .checkpoint import load_checkpoint
from .config import (
    APPLY_CALIBRATED_TEMPERATURE,
    CONSTRAINED_DECODING,
    DEFAULT_ORIENTATION_MODE,
    MAX_DECODE_CHANGES,
    ORIENTATION_DECISIVE_MARGIN,
    ORIENTATION_PAWN_PRIOR_MARGIN,
    PIECE_CLASSES,
    TTA_ENABLED,
    UNCERTAIN_SQUARE_THRESHOLD,
    OrientationMode,
)
from .decode import DecodeResult, decode_constrained
from .fen_utils import (
    PositionCheck,
    check_position,
    fen_from_class_indices,
    square_name,
)
from .orientation import (
    ConfidenceMarginRule,
    CoordinateRule,
    OrientationEvidence,
    OrientationPolicy,
    OrientedPrediction,
    PawnPriorRule,
    SingleLegalRule,
    TightMarginFallback,
)
from .preprocess import IDENTITY, BoardNormalizer, NormalizerConfig

if TYPE_CHECKING:  # pragma: no cover - só para as anotações
    # `from __future__ import annotations` (linha 1) já transforma toda anotação em texto,
    # então `nn.Module` e `ArchConfig` nas assinaturas não precisam de `nn` nem de `.model`
    # em execução. Isto é o que permite este módulo -- que é o caminho da JANELA para o
    # modelo, por `service.py` -- importar sem torch. `dataset.py`, `training.py`, `model.py`
    # e `augment.py` continuam exigindo torch, e devem: são o treino e a rede. O que muda é
    # que abrir a janela deixa de importá-los.
    import torch.nn as nn

    from .model import ArchConfig

logger = logging.getLogger(__name__)

_EPS = 1e-12


def describe_device(device: str) -> str:
    """Descrição do dispositivo para log e barra de status (S-30).

    O `load_model` decidia entre CPU e GPU em silêncio, então uma máquina com placa mas
    com o torch `+cpu` instalado treinava e inferia na CPU sem que nada dissesse isso --
    é a diferença entre 7,5 min e ~45 s por época, invisível.
    """
    import torch

    if device.startswith("cuda") and torch.cuda.is_available():
        index = torch.cuda.current_device() if device == "cuda" else int(device.split(":")[1])
        return f"cuda:{index} ({torch.cuda.get_device_name(index)})"
    if device.startswith("cuda"):
        return f"{device} (indisponível!)"
    build = "+cpu" if "+cpu" in torch.__version__ else torch.__version__
    return f"cpu (torch {build}, sem CUDA compilada)" if "+cpu" in torch.__version__ else f"cpu (torch {build})"


def load_model(
    model_path: Path,
    device: str | None = None,
    *,
    arch: ArchConfig | None = None,
    apply_temperature: bool = APPLY_CALIBRATED_TEMPERATURE,
) -> tuple[nn.Module, str]:
    """Carrega o checkpoint e devolve `(modelo, dispositivo)`.

    A arquitetura vem do próprio checkpoint quando ele a registra (S-27). Checkpoints
    anteriores à Fase 5 não a registram e caem no default -- que é a arquitetura com que
    eles foram treinados, então continuam carregando. Isso é deliberado: recusá-los
    tornaria os números do BASELINE.md irreproduzíveis.

    A temperatura da S-28 fica em `model.temperature` e é aplicada por quem chama, não
    dentro do `forward`. `apply_temperature=False` (o padrão) carrega o modelo com
    temperatura neutra e apenas **reporta** a que está gravada -- ver
    `config.APPLY_CALIBRATED_TEMPERATURE` para o que foi medido e por quê.
    """
    import torch

    from .model import DEFAULT_ARCH, ArchConfig, build_model

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model_path = Path(model_path)

    if not model_path.exists():
        # **Levanta, e não devolve pesos aleatórios (S-320).** Devolver o modelo não treinado
        # como se ele tivesse carregado fazia o programa **inventar uma FEN e dizer que deu
        # certo**: `KKKKKKKK/KKKKKKKK/...` com confiança 0,081, o rodapé anunciando "Diagramas
        # detectados: 1", e `cvoff-infer` saindo com código 0 -- a FEN falsa no stdout e o único
        # aviso no stderr. Quem redirecionava a saída ficava com um arquivo limpo de mentiras.
        #
        # E `models/*.pt` está no `.gitignore`: este era o estado de **100% dos clones novos**.
        # A primeira FEN da vida de quem instala era ruído de pesos aleatórios, e nada dizia
        # isso -- a conclusão razoável é "este programa é ruim", e não "falta um arquivo".
        #
        # A mensagem segue o molde de `text/modelo.py`, que já acertou esta: o que falta, por
        # que não vem no git, e como obter.
        raise FileNotFoundError(
            f"O classificador de peças não está em {model_path}.\n\n"
            "Ele não vem no repositório: é um binário treinado, e o `.gitignore` manda `*.pt` "
            "para fora. Aponte o arquivo no campo 'Modelo (.pt)' da aba Configuração, ou treine "
            "um com `cvoff-train` depois de corrigir alguns diagramas na janela e salvá-los com "
            "Ctrl+S. Sem ele não há leitura de peça: uma rede não treinada devolve uma posição "
            "inventada, e é por isso que este caminho recusa em vez de seguir."
        )

    checkpoint = load_checkpoint(model_path, map_location=dev)
    if arch is None:
        arch = ArchConfig.from_version(checkpoint.arch_version) if checkpoint.arch_version else DEFAULT_ARCH

    model = build_model(arch, pretrained=False)
    model.load_state_dict(checkpoint.state, strict=True)
    model.temperature = checkpoint.temperature if apply_temperature else 1.0  # type: ignore[assignment]
    model.to(dev)
    model.eval()

    if checkpoint.temperature == 1.0:
        temperatura = "1,0000 (não calibrado)"
    elif apply_temperature:
        temperatura = f"{checkpoint.temperature:.4f} (aplicada)"
    else:
        temperatura = f"{checkpoint.temperature:.4f} gravada, não aplicada (S-28: medido que piora o gate)"

    logger.info(
        "Modelo carregado de %s | arquitetura %s%s | dispositivo %s | temperatura %s",
        model_path.name,
        arch.version,
        " (não registrada no checkpoint; assumida)" if not checkpoint.arch_version else "",
        describe_device(dev),
        temperatura,
    )
    return model, dev


def _shifted(board: np.ndarray, dx: int, dy: int) -> np.ndarray:
    matrix = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
    height, width = board.shape[:2]
    return cv2.warpAffine(board, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _scaled(board: np.ndarray, factor: float) -> np.ndarray:
    height, width = board.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 0.0, factor)
    return cv2.warpAffine(board, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def tta_views(board: np.ndarray) -> list[np.ndarray]:
    """As sete vistas do TTA da S-29: original, ±2 px nos dois eixos, escalas 0,98 e 1,02.

    Os deslocamentos são no **tabuleiro**, não na casa, porque é ali que o erro real
    acontece: a Fase 2 mediu que o recorte fora de registro é o que degrada a leitura em
    PDF de verdade (o bbox embutido cru contra o warp por contorno, ROADMAP §2.4). Uma
    casa de 100 px deslocada de 2 px vira ~1,3 px depois do resize para 64×64 -- pequeno
    de propósito: o TTA existe para estabilizar a probabilidade, não para inventar vistas
    que o modelo nunca veria.
    """
    return [
        board,
        _shifted(board, 2, 0),
        _shifted(board, -2, 0),
        _shifted(board, 0, 2),
        _shifted(board, 0, -2),
        _scaled(board, 0.98),
        _scaled(board, 1.02),
    ]


@dataclass(frozen=True, eq=False)
class BoardPrediction:
    """Predição de um tabuleiro com a distribuição por casa preservada.

    O pipeline antigo colapsava as 64×13 probabilidades em uma FEN e um único número (a
    média das confianças). Duas informações se perdiam aí: **onde** o modelo está inseguro
    e **qual** seria a segunda leitura mais provável de cada casa. A primeira alimenta a
    revisão humana (S-21); a segunda é o insumo da decodificação com restrições (S-11).
    """

    probs: np.ndarray
    """(64, 13) — probabilidade de cada classe por casa, em ordem de leitura (a8..h1)."""

    class_indices: list[int]
    """Argmax por casa. Sem restrição de legalidade: pode conter dois reis brancos."""

    fen_board: str
    """Apenas o campo de peças da FEN, sem lado a jogar nem contadores."""

    mean_confidence: float
    """Média das confianças. Enganosa neste domínio: ~77% das casas são vazias e triviais,
    então a média fica ~0,97 mesmo em tabuleiro com erro. Mantida para comparação."""

    min_confidence: float
    """Confiança da casa mais insegura. É o sinal que de fato separa acerto de erro."""

    mean_entropy: float
    """Entropia média por casa, em nats. Alternativa contínua ao mínimo."""

    uncertain_squares: list[int]
    """Índices das casas abaixo do limiar, da menos confiante para a mais confiante."""

    position: PositionCheck
    """Legalidade da posição decodificada (S-05)."""

    decode: DecodeResult | None = None
    """Reparo aplicado pela decodificação com restrições (S-11), quando ativa.

    `None` significa argmax puro. Quando presente, `class_indices` e `fen_board` já são
    os do reparo -- `decode.changed_squares` diz o que mudou em relação ao argmax."""

    @property
    def square_confidences(self) -> np.ndarray:
        return self.probs[np.arange(len(self.class_indices)), self.class_indices]

    @property
    def uncertain_square_names(self) -> list[str]:
        return [square_name(index) for index in self.uncertain_squares]

    def runner_up(self, square_index: int) -> tuple[int, float]:
        """Segunda classe mais provável da casa e sua probabilidade."""
        order = np.argsort(self.probs[square_index])[::-1]
        second = int(order[1])
        return second, float(self.probs[square_index, second])


def prediction_from_probs(
    probs: np.ndarray,
    *,
    uncertain_threshold: float = UNCERTAIN_SQUARE_THRESHOLD,
    constrained: bool = False,
    max_changes: int = MAX_DECODE_CHANGES,
) -> BoardPrediction:
    """Monta a `BoardPrediction` a partir da matriz (64, 13) já normalizada.

    Separada de `predict_board` para permitir testar a lógica de decisão com matrizes
    sintéticas, sem carregar modelo nem imagem.

    O padrão aqui é argmax puro -- esta é a camada de baixo nível, e quem quer a leitura
    do produto usa `predict_board`, que segue `config.CONSTRAINED_DECODING`.

    Com `constrained=True`, a leitura passa pela decodificação sujeita às regras (S-11);
    as confianças reportadas passam a ser as das classes efetivamente escolhidas, então
    uma casa reparada aparece com confiança baixa -- que é a verdade sobre ela.
    """
    expected = (64, len(PIECE_CLASSES))
    if probs.shape != expected:
        raise ValueError(f"Esperada matriz {expected}, recebida {probs.shape}.")

    probs = np.asarray(probs, dtype=np.float64)

    decode = decode_constrained(probs, max_changes=max_changes) if constrained else None
    class_indices = decode.class_indices if decode is not None else [int(idx) for idx in probs.argmax(axis=1)]
    confidences = probs[np.arange(64), class_indices]
    entropy = -(probs * np.log(np.clip(probs, _EPS, None))).sum(axis=1)

    # `stable` garante que casas empatadas saiam em ordem de tabuleiro, e nao arbitraria:
    # a fila de revisao (S-22) fica reproduzivel entre execucoes.
    ordered = np.argsort(confidences, kind="stable")
    uncertain = [int(idx) for idx in ordered if confidences[idx] < uncertain_threshold]

    fen_board = fen_from_class_indices(class_indices)

    return BoardPrediction(
        probs=probs,
        class_indices=class_indices,
        fen_board=fen_board,
        mean_confidence=float(confidences.mean()),
        min_confidence=float(confidences.min()),
        mean_entropy=float(entropy.mean()),
        uncertain_squares=uncertain,
        position=check_position(fen_board),
        decode=decode,
    )


def board_probabilities(
    board_rgb: np.ndarray,
    model: nn.Module,
    device: str,
    *,
    tta: bool = False,
    normalizer: NormalizerConfig = IDENTITY,
) -> np.ndarray:
    """Matriz (64, 13) de probabilidades por casa.

    `normalizer` roda no **tabuleiro inteiro**, antes do corte em casas (S-39): iluminação e
    trama são propriedades da página, e estimá-las sobre 100×100 px de uma casa é estimá-las
    sobre ruído. `IDENTITY` (o padrão) devolve a mesma imagem e não custa nada.

    A temperatura da S-28 é aplicada aqui, sobre os logits, e não dentro do `forward`:
    dividir no treino mudaria o gradiente. Com `tta`, as vistas são somadas **depois** do
    softmax de cada uma -- média de probabilidades, não de logits. As duas médias
    discordam, e a de probabilidades é a que o voto da S-29 descreve: uma vista muito
    confiante e errada não domina as outras seis como dominaria em espaço de logit.
    """
    return board_probabilities_batch([board_rgb], model, device, tta=tta, normalizer=normalizer)[0]


def board_probabilities_batch(
    boards_rgb: Sequence[np.ndarray],
    model: nn.Module,
    device: str,
    *,
    tta: bool = False,
    normalizer: NormalizerConfig = IDENTITY,
) -> list[np.ndarray]:
    """Uma matriz (64, 13) por tabuleiro, **num único `forward`** (S-61).

    Existe porque a orientação automática lia o mesmo diagrama duas vezes, em duas chamadas
    de 64 casas. A computação é a mesma; o que se corta é o custo fixo por lote, que em CPU
    não é desprezível -- e a inferência é 76% do tempo de página, do qual metade é a segunda
    orientação.

    A ordem do lote é `tabuleiro-maior, vista, casa`, e ela **é** o contrato com a cabeça por
    tabuleiro da S-62b: cada bloco de 64 casas consecutivas é um tabuleiro inteiro, em ordem
    de leitura. Embaralhar aqui não daria erro -- daria uma leitura errada.
    """
    if not boards_rgb:
        return []

    import torch

    from .model import DEFAULT_ARCH, preprocess_cell_to_tensor, with_coordinate_channels

    arch = getattr(model, "arch", DEFAULT_ARCH)
    temperature = float(getattr(model, "temperature", 1.0))
    normalizador = BoardNormalizer(normalizer)

    por_tabuleiro = [
        tta_views(normalizador.normalize(board)) if tta else [normalizador.normalize(board)] for board in boards_rgb
    ]
    tensors = [
        with_coordinate_channels(preprocess_cell_to_tensor(cell, arch), square_index, arch)
        for views in por_tabuleiro
        for view in views
        for square_index, cell in enumerate(split_board_into_cells(view))
    ]
    batch = torch.stack(tensors, dim=0).to(device)

    with torch.inference_mode():
        probs = torch.softmax(model(batch) / temperature, dim=1)

    plano = probs.cpu().numpy().astype(np.float64)
    resultado: list[np.ndarray] = []
    inicio = 0
    for views in por_tabuleiro:
        fim = inicio + len(views) * 64
        matrizes = plano[inicio:fim].reshape(len(views), 64, len(PIECE_CLASSES))
        resultado.append(matrizes.mean(axis=0))
        inicio = fim
    return resultado


def predict_board(
    board_rgb: np.ndarray,
    model: nn.Module,
    device: str,
    *,
    rotate_180: bool = False,
    uncertain_threshold: float = UNCERTAIN_SQUARE_THRESHOLD,
    constrained: bool = CONSTRAINED_DECODING,
    tta: bool = TTA_ENABLED,
    normalizer: NormalizerConfig = IDENTITY,
) -> BoardPrediction:
    """Reconhece um tabuleiro já recortado, preservando as probabilidades."""
    board = cv2.rotate(board_rgb, cv2.ROTATE_180) if rotate_180 else board_rgb
    return prediction_from_probs(
        board_probabilities(board, model, device, tta=tta, normalizer=normalizer),
        uncertain_threshold=uncertain_threshold,
        constrained=constrained,
    )


def predict_with_orientation(
    board_rgb: np.ndarray,
    model: nn.Module,
    device: str,
    *,
    mode: OrientationMode = DEFAULT_ORIENTATION_MODE,
    uncertain_threshold: float = UNCERTAIN_SQUARE_THRESHOLD,
    constrained: bool = CONSTRAINED_DECODING,
    decisive_margin: float = ORIENTATION_DECISIVE_MARGIN,
    pawn_prior_margin: float = ORIENTATION_PAWN_PRIOR_MARGIN,
    tta: bool = TTA_ENABLED,
    normalizer: NormalizerConfig = IDENTITY,
    policy: OrientationPolicy | None = None,
) -> OrientedPrediction:
    """Reconhece o diagrama decidindo a orientação por diagrama, não por checkbox global.

    Com `mode="auto"` tenta 0° e 180° e entrega as duas leituras à `OrientationPolicy`, que
    é quem decide. Esta função ficou com o que só ela pode fazer -- rodar o modelo duas vezes
    -- e a cascata de regras, os limiares e a medição que os justifica moram em
    `orientation.py` (S-48). É lá que se lê **por que** a ordem é essa.

    O resumo da política padrão, na ordem:

    1. Coordenadas da borda, quando alguém as leu (hoje: nunca; ver `CoordinateRule`).
    2. Uma orientação ilegal e a outra não → a legal.
    3. Margem de confiança ≥ `decisive_margin` → a mais confiante.
    4. Senão, peões decidem por ≥ `pawn_prior_margin` filas → a que eles apontam.
    5. Senão → a mais confiante, marcada `ambiguous`.

    `decisive_margin` e `pawn_prior_margin` continuam aqui porque trinta chamadores os
    passam; eles montam a política quando `policy` não vem. Passar `policy` ignora os dois,
    que é o que um experimento regra a regra quer.

    Atenção ao que isto **não** resolve: diagrama impresso do ponto de vista das pretas. Ali
    as peças estão desenhadas para cima, e o que muda é o mapeamento casa→índice, não os
    pixels. Girar a imagem estragaria a leitura. Ver a pendência da S-13 no ROADMAP.
    """
    if mode not in ("auto", "0", "180"):
        raise ValueError(f"mode deve ser 'auto', '0' ou '180'; recebido {mode!r}.")

    def read(rotate: bool) -> BoardPrediction:
        return predict_board(
            board_rgb,
            model,
            device,
            rotate_180=rotate,
            uncertain_threshold=uncertain_threshold,
            constrained=constrained,
            tta=tta,
            normalizer=normalizer,
        )

    if mode != "auto":
        rotation = 180 if mode == "180" else 0
        return OrientedPrediction(
            prediction=read(rotation == 180),
            rotation=rotation,
            margin=0.0,
            ambiguous=False,
            reason=f"orientação imposta ({rotation}°)",
        )

    # As duas orientacoes num lote so (S-61). O resultado e identico ao de duas chamadas --
    # ha teste disso --, e o que muda e um `forward` de 128 casas em vez de dois de 64.
    de_pe, de_cabeca = (
        prediction_from_probs(matriz, uncertain_threshold=uncertain_threshold, constrained=constrained)
        for matriz in board_probabilities_batch(
            [board_rgb, cv2.rotate(board_rgb, cv2.ROTATE_180)],
            model,
            device,
            tta=tta,
            normalizer=normalizer,
        )
    )

    if policy is None:
        policy = OrientationPolicy(
            (
                CoordinateRule(),
                SingleLegalRule(),
                ConfidenceMarginRule(decisive_margin),
                PawnPriorRule(pawn_prior_margin),
                TightMarginFallback(),
            )
        )
    return policy.resolve(OrientationEvidence(upright=de_pe, flipped=de_cabeca))


# `predict_fen_from_board` foi removida junto com o último chamador. Devolvia (FEN, média
# das confianças) -- exatamente o par que a S-10 mostrou ser enganoso, e manter uma porta
# de entrada para ele só convidava a reintroduzir o problema. Use `predict_board`.
