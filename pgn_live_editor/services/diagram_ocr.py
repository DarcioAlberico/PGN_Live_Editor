"""Ponte opcional para o OCR nativo de diagramas baseado no ChessVisionOFF_Puro.

O editor de PGN não importa Torch/OpenCV na inicialização. O núcleo foi copiado
para ``pgn_live_editor.chess_diagram_ocr`` e carregado somente quando o usuário
solicita uma leitura, mantendo o fluxo de texto leve e o OCR opcional.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NATIVE_SOURCE_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH_ENV = "PGN_DIAGRAM_OCR_MODEL"
_DLL_DIRECTORIES: list[Any] = []


class DiagramOcrUnavailable(RuntimeError):
    """O OCR opcional não pode ser carregado neste ambiente."""


@dataclass(frozen=True)
class DiagramRecognition:
    """Resultado independente da implementação nativa do OCR."""

    index: int
    placement: str
    side_to_move: str
    fen: str
    min_confidence: float
    mean_confidence: float
    uncertain_squares: tuple[int, ...]
    rotation: int | None
    orientation_ambiguous: bool
    is_legal: bool | None
    problems: tuple[str, ...]
    bbox_pdf: tuple[float, float, float, float] | None
    detection_source: str
    source_root: str

    @property
    def needs_review(self) -> bool:
        return (
            self.orientation_ambiguous
            or self.is_legal is False
            or bool(self.uncertain_squares)
            or self.min_confidence < 0.90
        )

    def pgn_headers(self) -> dict[str, str]:
        """Tags mínimas para transformar o diagrama em posição inicial PGN."""
        return {
            "SetUp": "1",
            "FEN": self.fen,
            "DiagramOCR": "1",
            "DiagramOCRConfidence": f"{self.min_confidence:.4f}",
            "DiagramOCRReview": "1" if self.needs_review else "0",
        }

    def to_record(self, *, pdf_path: str, page: int) -> dict[str, object]:
        """Serializa o resultado no formato persistido pelo projeto."""
        source_id = hashlib.sha256(f"{Path(pdf_path).resolve()}|{int(page)}|{self.index}".encode()).hexdigest()[:24]
        return {
            "source_id": source_id,
            "pdf_path": str(pdf_path),
            "page": int(page),
            "diagram": self.index,
            "fen": self.fen,
            "placement": self.placement,
            "side_to_move": self.side_to_move,
            "min_confidence": self.min_confidence,
            "mean_confidence": self.mean_confidence,
            "uncertain_squares": list(self.uncertain_squares),
            "rotation": self.rotation,
            "orientation_ambiguous": self.orientation_ambiguous,
            "is_legal": self.is_legal,
            "problems": list(self.problems),
            "bbox_pdf": list(self.bbox_pdf) if self.bbox_pdf else None,
            "detection_source": self.detection_source,
            "source_root": self.source_root,
            "review": "pending" if self.needs_review else "accepted",
        }

    @classmethod
    def from_record(cls, record: dict[str, object]) -> DiagramRecognition:
        """Reconstrói um resultado devolvido pelo processo isolado do OCR."""
        bbox = record.get("bbox_pdf")
        return cls(
            index=int(record.get("diagram", 0)),
            placement=str(record.get("placement", "")),
            side_to_move=str(record.get("side_to_move", "w")),
            fen=str(record.get("fen", "")),
            min_confidence=float(record.get("min_confidence", 0.0)),
            mean_confidence=float(record.get("mean_confidence", 0.0)),
            uncertain_squares=tuple(int(value) for value in record.get("uncertain_squares", [])),
            rotation=int(record["rotation"]) if record.get("rotation") is not None else None,
            orientation_ambiguous=bool(record.get("orientation_ambiguous", False)),
            is_legal=record.get("is_legal"),
            problems=tuple(str(value) for value in record.get("problems", [])),
            bbox_pdf=tuple(float(value) for value in bbox) if bbox else None,
            detection_source=str(record.get("detection_source", "native")),
            source_root=str(record.get("source_root", NATIVE_SOURCE_ROOT)),
        )


def configured_source_root(source_root: Path | None = None) -> Path:
    """Resolve a cópia nativa; o ambiente só permite apontar dados auxiliares."""
    if source_root is not None:
        return Path(source_root).expanduser()
    return NATIVE_SOURCE_ROOT


def _load_native_api() -> tuple[Any, Any]:
    _prepare_torch_dlls()
    try:
        service_module = importlib.import_module("pgn_live_editor.chess_diagram_ocr.service")
    except (ImportError, OSError) as exc:
        raise DiagramOcrUnavailable(
            "Não foi possível carregar o OCR nativo de diagramas. "
            "Instale as dependências opcionais com 'uv sync --extra diagram-ocr'. "
            f"Detalhe: {exc}"
        ) from exc
    try:
        return service_module.OcrService, service_module.RecognitionOptions
    except AttributeError as exc:
        raise DiagramOcrUnavailable("A versão do ChessVisionOFF_Puro não expõe a API esperada.") from exc


def _prepare_torch_dlls() -> None:
    """Registra ``torch/lib`` no carregador DLL do Windows antes da inferência.

    O Torch pode ser importado no prompt, mas falhar dentro de uma thread que
    também carregou Qt/OpenCV: desde Python 3.8 o diretório do pacote não entra
    automaticamente na busca de dependências nativas. Mantemos os objetos
    retornados por ``add_dll_directory`` vivos durante toda a sessão.
    """
    if os.name != "nt":
        return
    torch_lib = os.path.join(sysconfig.get_paths()["purelib"], "torch", "lib")
    if not os.path.isdir(torch_lib) or any(getattr(handle, "name", "") == torch_lib for handle in _DLL_DIRECTORIES):
        return
    try:
        _DLL_DIRECTORIES.append(os.add_dll_directory(torch_lib))
    except (FileNotFoundError, OSError):
        # O import seguinte produz o diagnóstico original, que o adaptador
        # transforma em uma mensagem acionável.
        return


class DiagramOcrAdapter:
    """Adaptador do OCR nativo para o domínio do editor PGN."""

    def __init__(self, source_root: Path | None = None, model_path: Path | None = None) -> None:
        self.source_root = configured_source_root(source_root)
        configured_model = os.environ.get(MODEL_PATH_ENV, "").strip()
        self.model_path = model_path or (Path(configured_model).expanduser() if configured_model else None)
        self._service: Any = None
        self._options_type: Any = None

    def _api(self) -> tuple[Any, Any]:
        if self._service is None or self._options_type is None:
            service_type, options_type = _load_native_api()
            self._service = service_type(model_path=self.model_path) if self.model_path else service_type()
            self._options_type = options_type
        return self._service, self._options_type

    def available(self) -> tuple[bool, str]:
        try:
            self._api()
        except DiagramOcrUnavailable as exc:
            return False, str(exc)
        return True, ""

    def recognize_pdf_page(self, pdf_path: Path, page_index: int, *, dpi: int = 220) -> list[DiagramRecognition]:
        service, options_type = self._api()
        options = options_type(dpi=dpi, **({"model_path": self.model_path} if self.model_path else {}))
        try:
            results = service.recognize_page(Path(pdf_path), int(page_index), options=options)
        except (OSError, ValueError, RuntimeError) as exc:
            if exc.__class__.__name__ == "NoBoardDetectedError":
                return []
            raise DiagramOcrUnavailable(
                _torch_error_message(f"Falha no OCR da página {page_index + 1}: {exc}")
            ) from exc
        return [self._adapt(item, source_root=self.source_root) for item in results]

    def recognize_image(self, image: Any, *, dpi: int = 220) -> list[DiagramRecognition]:
        service, options_type = self._api()
        options = options_type(dpi=dpi, **({"model_path": self.model_path} if self.model_path else {}))
        try:
            results = service.recognize_image(image, options=options)
        except (OSError, ValueError, RuntimeError) as exc:
            raise DiagramOcrUnavailable(_torch_error_message(f"Falha no OCR da imagem: {exc}")) from exc
        return [self._adapt(item, source_root=self.source_root) for item in results]

    @staticmethod
    def _adapt(item: Any, *, source_root: Path) -> DiagramRecognition:
        placement = str(item.placement)
        side = str(item.side_to_move) if str(item.side_to_move) in {"w", "b"} else "w"
        fen = f"{placement} {side} - - 0 1"
        bbox = item.bbox_pdf
        return DiagramRecognition(
            index=int(item.index),
            placement=placement,
            side_to_move=side,
            fen=fen,
            min_confidence=float(item.min_confidence),
            mean_confidence=float(item.mean_confidence),
            uncertain_squares=tuple(int(square) for square in item.uncertain_squares),
            rotation=int(item.rotation) if item.rotation is not None else None,
            orientation_ambiguous=bool(item.orientation_ambiguous),
            is_legal=item.is_legal,
            problems=tuple(str(problem) for problem in item.problems),
            bbox_pdf=tuple(float(value) for value in bbox) if bbox is not None else None,
            detection_source=str(item.detection_source),
            source_root=str(source_root),
        )


def _torch_error_message(message: str) -> str:
    if "c10.dll" not in message and "DLL" not in message:
        return message
    return (
        f"{message}\n\n"
        "O Torch não conseguiu carregar uma DLL. Feche outras instâncias do editor, "
        "execute 'uv sync --extra diagram-ocr' neste projeto e tente novamente. "
        "Se persistir, instale o Microsoft Visual C++ Redistributable x64 e confirme "
        "que Python e o ambiente virtual são 64 bits."
    )
