"""Estado auditável do pipeline de importação de PDF."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

IMPORT_STAGES = ("extraction", "reading_order", "cleanup", "book", "games", "recognition")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    file_name: str
    file_sha256: str
    page: int
    block: int | None = None
    column: int | None = None
    start: int = 0
    end: int = 0
    method: str = "text-layer"


@dataclass
class ImportArtifact:
    stage: str
    text: str = ""
    source: SourceRef | None = None
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class PdfImportSession:
    """Sessão serializável de importação, independente da UI e do Qt."""

    session_id: str
    source_path: str
    source_sha256: str
    pages: list[int]
    artifacts: dict[str, ImportArtifact] = field(default_factory=dict)
    current_stage: str = "extraction"

    @classmethod
    def from_page_text(cls, pdf_path: Path, page: int, text: str) -> PdfImportSession:
        data = pdf_path.read_bytes()
        digest = _sha256(data)
        source_id = _sha256(f"{digest}:{page}".encode())[:24]
        method = "text-layer" if text.strip() else "OCR-required"
        diagnostics = [] if text.strip() else ["A página não possui camada de texto; OCR é necessário."]
        source = SourceRef(source_id, pdf_path.name, digest, page, end=len(text), method=method)
        artifact = ImportArtifact("extraction", text, source, diagnostics)
        return cls(
            _sha256(f"{source_id}:session".encode())[:24], str(pdf_path), digest, [page], {"extraction": artifact}
        )

    @property
    def requires_ocr(self) -> bool:
        artifact = self.artifacts.get("extraction")
        return bool(artifact and artifact.source and artifact.source.method == "OCR-required")

    def put(
        self, stage: str, text: str, *, source: SourceRef | None = None, diagnostics: list[str] | None = None
    ) -> None:
        if stage not in IMPORT_STAGES:
            raise ValueError(f"etapa de importação inválida: {stage}")
        self.artifacts[stage] = ImportArtifact(stage, text, source, list(diagnostics or []))
        self.current_stage = stage

    def preview(self, stage: str | None = None) -> str:
        artifact = self.artifacts.get(stage or self.current_stage)
        return artifact.text if artifact else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "pages": list(self.pages),
            "current_stage": self.current_stage,
            "artifacts": {
                key: {
                    "stage": value.stage,
                    "text": value.text,
                    "source": asdict(value.source) if value.source else None,
                    "diagnostics": list(value.diagnostics),
                }
                for key, value in self.artifacts.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PdfImportSession:
        artifacts: dict[str, ImportArtifact] = {}
        for key, value in dict(payload.get("artifacts", {})).items():
            source_payload = value.get("source")
            source = SourceRef(**source_payload) if isinstance(source_payload, dict) else None
            artifacts[str(key)] = ImportArtifact(
                str(value.get("stage", key)),
                str(value.get("text", "")),
                source,
                [str(item) for item in value.get("diagnostics", [])],
            )
        return cls(
            str(payload.get("session_id", "")),
            str(payload.get("source_path", "")),
            str(payload.get("source_sha256", "")),
            [int(page) for page in payload.get("pages", [])],
            artifacts,
            str(payload.get("current_stage", "extraction")),
        )
