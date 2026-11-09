"""Camada de serviço: o pipeline de OCR sem nenhuma dependência de interface (S-31).

**O problema.** `app_tkinter._detect_and_predict_items` e o `run_ocr_for_boards` do Streamlit
implementavam o mesmo fluxo de forma independente -- detectar, prever com orientação,
inferir o lado a jogar, conferir legalidade -- e já tinham divergido. O Streamlit não
refinava o recorte pelo contorno, não guardava a matriz por casa (então nunca poderia ter o
heatmap da S-21) e não registrava a fonte de detecção da S-12. Cada ganho de precisão das
Fases 2 e 3 precisava ser reimplementado duas vezes, e a segunda vez ficava para trás.

**O que este módulo é.** `OcrService` concentra o pipeline; os dois frontends viram
apresentação. Nada aqui importa `tkinter`, `streamlit` ou `PIL` -- o critério de aceite da
S-31 é justamente poder testá-lo sem Tk, e é o que `tests/test_service.py` faz.

**O que ele devolve.** `RecognizedDiagram` no lugar do `dict[str, Any]` que as duas UIs
passavam adiante. O dicionário não era só falta de tipo: ele obrigava cada frontend a
escolher que chaves valia a pena preencher, e foi assim que as duas leituras divergiram
sem que nada quebrasse.

**O lock não é zelo.** A S-31 aponta o defeito concreto: `reload_model()` é chamada da
thread de treino e zerava o cache do modelo enquanto uma thread de OCR podia estar usando,
e o treino **reescreve o arquivo `.pt`** que uma carga concorrente estaria lendo. Aqui a
carga e o uso do modelo acontecem sob o mesmo lock, então trocar de modelo espera o
reconhecimento em andamento terminar em vez de disputar com ele.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .board_detection import NoBoardDetectedError, detect_boards
from .config import (
    BOARD_SIZE,
    DEFAULT_MAX_BOARDS,
    DEFAULT_MODEL_PATH,
    DEFAULT_ORIENTATION_MODE,
)
from .detection import DiagramCandidate, detect_diagrams_in_pdf_page
from .fen_utils import PositionCheck, check_position
from .inference import (
    BoardPrediction,
    describe_device,
    load_model,
    predict_with_orientation,
)
from .pdf_io import get_pdf_page_count, render_pdf_page
from .pdf_text import DiagramContext, contexts_for_pdf_page
from .preprocess import IDENTITY, NormalizerConfig
from .semantics import SideToMove, compose_fen, infer_side_to_move

logger = logging.getLogger(__name__)

REFINE_PAD_RATIO = 0.08
"""Folga em volta do quad antes de procurar o contorno de novo.

Sem folga, o recorte cortaria a própria borda que o detector precisa enxergar para achar
os quatro cantos; com folga demais, ele reencontra o diagrama vizinho."""


@dataclass
class RecognizedDiagram:
    """Um diagrama e tudo que a interface precisa saber sobre ele.

    Nem todo diagrama no editor veio do OCR: a fila de revisão (S-22) e o navegador do
    dataset (S-23) abrem um tabuleiro que já tem rótulo. Por isso `prediction` e `legality`
    são opcionais, e `is_legal` distingue **três** respostas -- legal, ilegal e não
    avaliada. Colapsar as duas últimas em `False` faria a barra de status gritar "ILEGAL"
    para toda amostra vinda do dataset.

    Mutável de propósito em três campos: o usuário troca o lado a jogar (e a procedência
    vira `"manual"`) e corrige casas à mão. `edited_by_hand` é o que faz o cache de
    navegação avisar antes de descartar a página -- perder uma leitura do modelo custa
    rodar o OCR de novo, perder uma correção humana custa o trabalho da pessoa.
    """

    index: int
    board_rgb: np.ndarray
    placement: str
    """Só o campo de peças da FEN. A vez mora em `side_to_move`, e juntá-las numa string
    é o que fazia a informação de lado a jogar se perder pelo caminho (S-17)."""

    # --- sinais por casa. Existem sem predição: um item da fila traz os agregados que a
    # --- S-22 gravou, mas não a matriz (64, 13), que custaria ~5,6 MB por livro em JSON.
    min_confidence: float = 0.0
    mean_confidence: float = 0.0
    uncertain_squares: list[int] = field(default_factory=list)
    square_confidences: list[float] = field(default_factory=list)
    changed_squares: list[int] = field(default_factory=list)
    probs: np.ndarray | None = None

    legality: PositionCheck | None = None
    """`None` significa "não avaliada", não "legal"."""

    rotation: int | None = None
    orientation_ambiguous: bool = False
    orientation_reason: str = ""

    quad: list[list[float]] | None = None
    """Os quatro cantos em pixels da página renderizada. `None` no caminho da S-12, que
    localiza por bbox em vez de por contorno -- ali quem responde "onde" é `bbox_pdf`."""

    bbox_pdf: tuple[float, float, float, float] | None = None
    """Onde o diagrama está na página, em **pontos do PDF** (S-41).

    O detector híbrido já carrega isto em cada `DiagramCandidate` desde a S-12, e
    `recognize_page` o descartava: a `RecognizedDiagram` saía sabendo *o que* leu e não
    *de onde*. Pontos, e não pixels, porque a anotação do conjunto de campo precisa
    sobreviver a uma troca de DPI -- 220 hoje, 300 amanhã, e a mesma página passaria a
    ter outro sistema de coordenadas."""

    context: DiagramContext | None = None
    detection_source: str = ""

    side_to_move: str = "w"
    side_to_move_source: str = "default"
    """`"text"`, `"legality"` e `"default"` vêm da S-17; `"manual"` e `"queue"` só a
    interface produz. Por isso é `str` e não o `SideSource` da `semantics`: o conjunto
    aqui é maior que o daquele módulo."""

    side_to_move_reason: str = ""
    side_conflicting: bool = False
    edited_by_hand: bool = False

    prediction: BoardPrediction | None = None
    """A leitura completa, quando houve OCR. É de onde sai o tooltip das 3 classes."""

    # ------------------------------------------------------------------------ construtores

    @classmethod
    def from_prediction(
        cls,
        index: int,
        board_rgb: np.ndarray,
        prediction: BoardPrediction,
        *,
        side: SideToMove,
        legality: PositionCheck,
        rotation: int = 0,
        orientation_ambiguous: bool = False,
        orientation_reason: str = "",
        quad: list[list[float]] | None = None,
        bbox_pdf: tuple[float, float, float, float] | None = None,
        context: DiagramContext | None = None,
        detection_source: str = "",
    ) -> RecognizedDiagram:
        decode = prediction.decode
        return cls(
            index=index,
            board_rgb=board_rgb,
            placement=prediction.fen_board,
            min_confidence=float(prediction.min_confidence),
            mean_confidence=float(prediction.mean_confidence),
            uncertain_squares=list(prediction.uncertain_squares),
            square_confidences=[float(value) for value in prediction.square_confidences],
            changed_squares=([square for square, _argmax, _final in decode.changed_squares] if decode else []),
            probs=prediction.probs,
            legality=legality,
            rotation=rotation,
            orientation_ambiguous=orientation_ambiguous,
            orientation_reason=orientation_reason,
            quad=quad,
            bbox_pdf=bbox_pdf,
            context=context,
            detection_source=detection_source,
            side_to_move="w" if side.color else "b",
            side_to_move_source=str(side.source),
            side_to_move_reason=side.reason,
            side_conflicting=side.conflicting,
            prediction=prediction,
        )

    @classmethod
    def from_label(
        cls,
        board_rgb: np.ndarray,
        placement: str,
        *,
        side_to_move: str = "w",
        side_to_move_source: str = "default",
        side_to_move_reason: str = "",
        detection_source: str = "",
        **signals: Any,
    ) -> RecognizedDiagram:
        """Um tabuleiro que já tem rótulo: item da fila de revisão ou linha do dataset.

        Não inventa legalidade nem confiança. O que a origem souber vai em `signals`; o
        que ela não souber fica no default, que é honestamente "não sei".

        `side_to_move_source` cai em `"default"`, não em `"manual"`: os dois chamadores
        reais informam a procedência (`"queue"` e `"manual"`), e supor que um humano
        decidiu tem consequência -- é o que faz o cache de navegação avisar antes de
        descartar a página, e o aviso perderia o sentido se valesse para todo diagrama.
        """
        return cls(
            index=0,
            board_rgb=board_rgb,
            placement=placement,
            side_to_move=side_to_move if side_to_move in ("w", "b") else "w",
            side_to_move_source=side_to_move_source,
            side_to_move_reason=side_to_move_reason,
            detection_source=detection_source,
            **signals,
        )

    # ------------------------------------------------------------------ leituras derivadas

    @property
    def is_legal(self) -> bool | None:
        return self.legality.is_legal if self.legality is not None else None

    @property
    def is_fatal(self) -> bool | None:
        return self.legality.is_fatal if self.legality is not None else None

    @property
    def problems(self) -> tuple[str, ...]:
        return tuple(self.legality.problems) if self.legality is not None else ()

    @property
    def exercise_number(self) -> int | None:
        return self.context.exercise_number if self.context else None

    @property
    def caption(self) -> str:
        return self.context.caption if self.context else ""

    @property
    def side_is_white(self) -> bool:
        return self.side_to_move != "b"

    def runner_up(self, square_index: int) -> tuple[int, float] | None:
        """Segunda classe mais provável de uma casa, ou `None` sem a matriz por casa."""
        if self.prediction is None:
            return None
        return self.prediction.runner_up(square_index)

    def set_side_to_move(self, side: str, *, source: str = "manual") -> None:
        """Troca a vez e registra que a resposta passou a ser de outra procedência.

        Zera `side_conflicting` porque a discordância entre texto e posição era sobre o
        palpite automático; uma vez que um humano decidiu, não há mais duas fontes.
        """
        self.side_to_move = "b" if str(side) == "b" else "w"
        self.side_to_move_source = source
        self.side_conflicting = False

    def resolve_legality(self, placement: str | None = None) -> PositionCheck:
        """Reavalia a legalidade com a vez e o posicionamento atuais, e guarda o resultado.

        Trocar de quem é a vez pode resolver o "xeque invertido" sem mexer em nenhuma peça
        (S-17), então a legalidade não pode ficar congelada no que o OCR decidiu.
        """
        self.legality = check_position(
            compose_fen(placement if placement is not None else self.placement, self.side_is_white)
        )
        return self.legality


@dataclass(frozen=True)
class RecognitionOptions:
    """Os parâmetros que mudam o que o OCR lê, reunidos para não viajarem soltos."""

    model_path: Path = DEFAULT_MODEL_PATH
    orientation: str = DEFAULT_ORIENTATION_MODE
    max_boards: int = DEFAULT_MAX_BOARDS
    dpi: int = 220

    normalizer: NormalizerConfig = IDENTITY
    """Normalização do tabuleiro antes do corte em casas (S-39).

    Padrão `IDENTITY`, que é o pipeline anterior. Ligar etapas aqui muda o domínio que o
    modelo vê, e um checkpoint treinado sem elas não é o mesmo modelo -- ver
    `NormalizerConfig.version`."""

    refine_detected_boards: bool = False
    """Rodar o detector de contorno dentro do quad para alinhar melhor o recorte.

    Vale para imagem solta e para a página renderizada; **não** vale para candidato vindo
    da imagem embutida do PDF, que já sai alinhado pelo warp da S-12 -- ali refinar de novo
    só acrescenta erro. `recognize_page` desliga sozinha, por isso."""

    fallback_to_full_image: bool = False
    """Se nada for detectado, tratar a imagem inteira como o tabuleiro.

    Ligado no recorte de área: quem selecionou um pedaço da página com o mouse já disse
    onde está o diagrama, e recusar por não achar contorno seria ignorar essa informação."""


class OcrService:
    """O pipeline de reconhecimento, com o modelo carregado uma vez e sob lock."""

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        loader: Callable[[Path], tuple[Any, str]] = load_model,
        caption_reader: Any = None,
    ) -> None:
        """`loader` troca de onde vem o modelo, mantendo o resto do pipeline igual.

        Serve aos testes, que não têm um `.pt` para carregar, e é o encaixe por onde a
        sessão ONNX da S-30 entra quando alguém quiser o modo rápido na interface -- ela
        já devolve o mesmo par `(modelo, dispositivo)`.

        `caption_reader` é o OCR de legenda da S-43, e `None` -- o padrão -- é o pipeline
        como sempre foi. Recebido pronto, e não construído aqui: o serviço não deve saber
        que existe um extra opcional de OCR, e quem lê a configuração é a interface. Ver
        `ocr_caption.caption_reader_from_settings`.
        """
        self._lock = threading.RLock()
        self._loader = loader
        self._model: Any = None
        self._device: str | None = None
        self._loaded_path: Path | None = None
        self._requested_path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
        self._caption_reader = caption_reader

    @property
    def caption_reader(self) -> Any:
        """O leitor de legenda por OCR em uso, ou `None`. Repassado à varredura (S-43)."""
        return self._caption_reader

    # ------------------------------------------------------------------------------ modelo

    @property
    def model_path(self) -> Path:
        return self._requested_path

    @property
    def device(self) -> str | None:
        """Dispositivo do modelo carregado, ou `None` se ainda não há um."""
        return self._device

    @property
    def device_label(self) -> str:
        """Descrição em pt-BR do dispositivo, para a barra de status (S-30)."""
        return describe_device(self._device) if self._device is not None else "nenhum modelo carregado"

    @contextmanager
    def model_session(self, model_path: Path | None = None) -> Iterator[tuple[Any, str]]:
        """Empresta o modelo pela duração de um bloco, segurando o lock.

        O lock cobre o **uso**, não só a carga. É o que a S-31 pede: enquanto uma página
        está sendo reconhecida, o treino que terminou não consegue trocar o `.pt` debaixo
        dela, e duas threads não disparam duas cargas do mesmo arquivo.
        """
        with self._lock:
            yield self._ensure_model(model_path)

    def _ensure_model(self, model_path: Path | None = None) -> tuple[Any, str]:
        alvo = Path(model_path) if model_path is not None else self._requested_path
        if self._model is None or self._loaded_path != alvo:
            logger.info("Carregando modelo: %s", alvo)
            model, device = self._loader(alvo)
            self._model = model
            self._device = device
            self._loaded_path = alvo
            self._requested_path = alvo
            logger.info("Modelo carregado em %s.", describe_device(device))
        return self._model, str(self._device)

    def load(self, model_path: Path | None = None) -> tuple[Any, str]:
        """Garante o modelo carregado e devolve `(modelo, dispositivo)`."""
        with self._lock:
            return self._ensure_model(model_path)

    def invalidate_model(self, model_path: Path | None = None) -> None:
        """Descarta o modelo em memória; a próxima leitura recarrega do disco.

        Chamada ao fim do treino. Espera o reconhecimento em andamento porque pega o mesmo
        lock -- era exatamente essa a corrida que a S-31 aponta.
        """
        with self._lock:
            self._model = None
            self._device = None
            self._loaded_path = None
            if model_path is not None:
                self._requested_path = Path(model_path)
            logger.info("Cache do modelo limpo; a proxima leitura recarrega de %s.", self._requested_path)

    # -------------------------------------------------------------------------------- PDF

    def page_count(self, pdf_source: Any) -> int:
        return get_pdf_page_count(pdf_source)

    def render_page(self, pdf_source: Any, page_index: int, *, dpi: int) -> np.ndarray:
        return render_pdf_page(pdf_source, page_index, dpi=dpi)

    # ------------------------------------------------------------------- reconhecimento

    def recognize_page(
        self,
        pdf_source: Any,
        page_index: int,
        page_rgb: np.ndarray | None = None,
        *,
        options: RecognitionOptions,
        candidates: Sequence[DiagramCandidate] | None = None,
    ) -> list[RecognizedDiagram]:
        """Reconhece uma página de PDF pelo detector híbrido da S-12.

        É o caminho que a exportação usa. Ter a interface num detector e o PGN noutro
        recriaria, no recorte, o mesmo desencontro que a S-14 corrigiu na numeração: a tela
        mostrando um diagrama e o `[Diagram "2"]` apontando para outro.

        **`candidates` é para quem já detectou esta página** (S-501). O visualizador roda o
        detector para desenhar os retângulos sobre a folha; sem este parâmetro, mandar ler a
        mesma página roda tudo de novo -- render de imagem embutida, contorno, aparo de moldura
        --, e o log de uma sessão de verdade mostra as mesmas linhas de detecção duas vezes por
        página, uma por marcar e outra por ler.

        **E o que ele corrige é maior que o tempo.** Que o retângulo "3" da tela e o diagrama 3
        da lista sejam o mesmo objeto valia porque o detector é determinístico e recebia a mesma
        entrada -- uma coincidência de que ninguém tinha como se lembrar ao mexer no detector.
        Passando a lista adiante, a identidade passa a ser por construção.

        **Quem passa é quem promete**: os candidatos têm de ser desta página e do mesmo
        `max_boards`, porque é essa lista que numera os diagramas e é dela que sai o recorte
        lido. Conferir aqui não é possível -- um `DiagramCandidate` não carrega de que página
        veio --, e conferir pelo que ele carrega (contar caixas, comparar bbox) daria uma
        resposta que parece garantia e não é. Quem chama guarda a lista com a página ao lado; a
        janela em Qt faz isso em `_candidatos_desta_pagina`.
        """
        if page_rgb is None:
            page_rgb = self.render_page(pdf_source, page_index, dpi=options.dpi)

        if candidates is None:
            candidates = detect_diagrams_in_pdf_page(pdf_source, page_index, page_rgb, max_boards=options.max_boards)
        boards = [(candidate.board_rgb, None) for candidate in candidates]
        # Mesmo contexto textual que a exportação lê (S-16): a interface mostrando um lado a
        # jogar e o PGN gravando outro seria pior que não mostrar nada.
        contexts = contexts_for_pdf_page(
            pdf_source,
            page_index,
            [c.bbox_pdf for c in candidates],
            caption_reader=self._caption_reader,
        )

        return self._predict_boards(
            image_rgb=page_rgb,
            boards=boards,
            options=options,
            contexts=contexts,
            detection_sources=[candidate.source for candidate in candidates],
            # O recorte embutido já vem alinhado pelo warp da S-12; refinar de novo só soma erro.
            refine=False,
            # Onde cada diagrama está na página. O detector já sabia e o serviço jogava fora
            # -- e é o que o conjunto de campo da S-41 precisa para casar com a anotação.
            bboxes_pdf=[candidate.bbox_pdf for candidate in candidates],
        )

    def recognize_image(
        self,
        image_rgb: np.ndarray,
        *,
        options: RecognitionOptions,
        boards: Sequence[tuple[np.ndarray, np.ndarray | None]] | None = None,
    ) -> list[RecognizedDiagram]:
        """Reconhece uma imagem solta: print, foto, recorte ou página já renderizada.

        `boards` permite pular a detecção quando quem chama já sabe onde está o tabuleiro
        (a caixa "a imagem já contém somente um tabuleiro" do Streamlit).
        """
        detected = (
            list(boards) if boards is not None else detect_boards(image_rgb=image_rgb, max_boards=options.max_boards)
        )
        if options.fallback_to_full_image and not detected:
            detected = [(image_rgb, None)]

        return self._predict_boards(
            image_rgb=image_rgb,
            boards=detected,
            options=options,
            contexts=[],
            detection_sources=[],
            refine=options.refine_detected_boards,
        )

    def _predict_boards(
        self,
        *,
        image_rgb: np.ndarray,
        boards: Sequence[tuple[np.ndarray, np.ndarray | None]],
        options: RecognitionOptions,
        contexts: Sequence[DiagramContext | None],
        detection_sources: Sequence[str],
        refine: bool,
        bboxes_pdf: Sequence[tuple[float, float, float, float]] = (),
    ) -> list[RecognizedDiagram]:
        """O núcleo comum: prever, decidir orientação, inferir a vez, conferir legalidade."""
        if not boards:
            # `NoBoardDetectedError` e nao `ValueError` (S-125): quem chama precisa distinguir
            # "esta pagina nao tem diagrama" -- que e resposta, e a mais comum num livro -- de
            # "o reconhecimento quebrou". Com um `ValueError` generico a unica forma de separar
            # os dois era procurar a mensagem dentro do texto da excecao, e `app_tkinter` fazia
            # exatamente isso. A classe ja existia, e `run_main` mapeia as duas para o mesmo
            # codigo de saida 2, entao a CLI da S-126 nao muda de comportamento.
            raise NoBoardDetectedError("Nenhum tabuleiro foi detectado na imagem selecionada.")

        diagrams: list[RecognizedDiagram] = []
        with self.model_session(options.model_path) as (model, device):
            for idx, (board_rgb, quad) in enumerate(boards):
                board_for_pred, quad_for_item = refine_board_from_quad(image_rgb, quad) if refine else (board_rgb, quad)
                oriented = predict_with_orientation(
                    board_for_pred,
                    model,
                    device,
                    mode=options.orientation,  # type: ignore[arg-type]
                    normalizer=options.normalizer,
                )
                prediction = oriented.prediction
                context = contexts[idx] if idx < len(contexts) else None
                side: SideToMove = infer_side_to_move(prediction.fen_board, context)
                diagrams.append(
                    RecognizedDiagram.from_prediction(
                        idx,
                        board_for_pred,
                        prediction,
                        side=side,
                        legality=check_position(compose_fen(prediction.fen_board, side)),
                        rotation=oriented.rotation,
                        orientation_ambiguous=oriented.ambiguous,
                        orientation_reason=oriented.reason,
                        quad=quad_for_item.tolist() if quad_for_item is not None else None,
                        bbox_pdf=bboxes_pdf[idx] if idx < len(bboxes_pdf) else None,
                        context=context,
                        detection_source=detection_sources[idx] if idx < len(detection_sources) else "",
                    )
                )
        return diagrams

    # ---------------------------------------------------------------------------- dataset


def refine_board_from_quad(
    image_rgb: np.ndarray,
    quad: np.ndarray | None,
    pad_ratio: float = REFINE_PAD_RATIO,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Reprocura o contorno dentro da região do quad, para alinhar melhor o recorte.

    O detector de página acha *onde* está o diagrama; rodá-lo de novo numa vizinhança
    apertada acha *os cantos*, e é o alinhamento da grade 8×8 que decide se cada casa cai
    inteira dentro da sua imagem. Sem quad não há o que refinar, e a imagem é só redimensionada.
    """
    if quad is None:
        return cv2.resize(image_rgb, (BOARD_SIZE, BOARD_SIZE)), None

    h, w = image_rgb.shape[:2]
    x0 = max(0, int(np.floor(np.min(quad[:, 0]))))
    y0 = max(0, int(np.floor(np.min(quad[:, 1]))))
    x1 = min(w, int(np.ceil(np.max(quad[:, 0]))))
    y1 = min(h, int(np.ceil(np.max(quad[:, 1]))))
    pad = int(max(max(1, x1 - x0), max(1, y1 - y0)) * pad_ratio)

    rx0 = max(0, x0 - pad)
    ry0 = max(0, y0 - pad)
    rx1 = min(w, x1 + pad)
    ry1 = min(h, y1 + pad)
    roi = image_rgb[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return cv2.resize(image_rgb, (BOARD_SIZE, BOARD_SIZE)), None

    refined = detect_boards(image_rgb=roi, max_boards=1, warn_on_cap=False)
    if not refined:
        return cv2.resize(image_rgb[y0:y1, x0:x1], (BOARD_SIZE, BOARD_SIZE)), quad

    refined_board, refined_quad = refined[0]
    if refined_quad is not None:
        refined_quad = refined_quad + np.array([rx0, ry0], dtype=np.float32)
    return refined_board, refined_quad
