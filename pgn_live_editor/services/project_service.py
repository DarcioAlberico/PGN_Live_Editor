"""Persistencia do trabalho: projeto `.plez`, autosave e recuperacao de sessao.

Principio do projeto (SPEC 0): **o texto bruto e o trabalho**. O PGN e apenas
uma saida derivada. Por isso o que se salva aqui e o texto bruto integral, mais
o estado necessario para retomar exatamente de onde se parou.

Formato `.plez` (ZIP):
    meta.json        versao, titulo, autor, fonte, idioma
    raw.txt          texto bruto completo (fonte da verdade)
    headers.json     template de cabecalho do livro
    dictionary.json  substituicoes OCR aprendidas neste projeto
    state.json       cursor, alertas ignorados, layout
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.substitutions import LAYER_PROJECT, Dictionary

PROJECT_SUFFIX = ".plez"
FORMAT_VERSION = 1
MAX_RECENT_FILES = 10


def app_data_dir() -> Path:
    """Diretorio de dados do aplicativo, criado sob demanda."""
    base = Path(os.environ.get("APPDATA") or Path.home())
    directory = base / "PGN Live Editor"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def recovery_dir() -> Path:
    directory = app_data_dir() / "recovery"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass
class ProjectMeta:
    version: int = FORMAT_VERSION
    title: str = ""
    author: str = ""
    source: str = ""
    locale: str = "pt"
    saved_at: float = 0.0


@dataclass
class ProjectState:
    cursor_position: int = 0
    ignored_issue_tokens: list[str] = field(default_factory=list)
    # Indices das partidas ja conferidas. E o "12/64" da barra de status, e a
    # unica informacao do livro que o programa nao consegue deduzir do texto.
    completed_games: list[int] = field(default_factory=list)
    # Quantas vezes cada correcao ja foi aceita. Na terceira, o programa oferece
    # grava-la no dicionario -- e a parte "aprendiz" da SPEC 5.2.
    accepted_corrections: dict[str, int] = field(default_factory=dict)
    # Pagina do PDF que acompanha este trabalho, para reabrir onde parou.
    pdf_path: str = ""
    pdf_page: int = 0


@dataclass
class Project:
    """Um trabalho de transcricao em andamento."""

    raw_text: str = ""
    meta: ProjectMeta = field(default_factory=ProjectMeta)
    header_template: dict[str, str] = field(default_factory=dict)
    dictionary: Dictionary = field(default_factory=Dictionary)
    state: ProjectState = field(default_factory=ProjectState)
    path: Path | None = None

    @property
    def display_name(self) -> str:
        if self.path:
            return self.path.stem
        return self.meta.title or "Sem título"


def _read_json(archive: zipfile.ZipFile, name: str, fallback: dict) -> dict:
    try:
        payload = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        return dict(fallback)
    return payload if isinstance(payload, dict) else dict(fallback)


def save_project(project: Project, path: Path) -> Path:
    """Grava o projeto de forma atomica (escreve ao lado e so entao troca)."""
    path = path.with_suffix(PROJECT_SUFFIX) if not path.suffix else path
    path.parent.mkdir(parents=True, exist_ok=True)

    project.meta.version = FORMAT_VERSION
    project.meta.saved_at = time.time()

    handle, temp_name = tempfile.mkstemp(suffix=PROJECT_SUFFIX, dir=str(path.parent))
    os.close(handle)
    temp_path = Path(temp_name)

    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("meta.json", json.dumps(asdict(project.meta), ensure_ascii=False, indent=2))
            archive.writestr("raw.txt", project.raw_text)
            archive.writestr("headers.json", json.dumps(project.header_template, ensure_ascii=False, indent=2))
            archive.writestr(
                "dictionary.json",
                json.dumps({"entries": project.dictionary.to_list()}, ensure_ascii=False, indent=2),
            )
            archive.writestr("state.json", json.dumps(asdict(project.state), ensure_ascii=False, indent=2))
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    project.path = path
    return path


def load_project(path: Path) -> Project:
    """Le um `.plez`. Levanta ValueError se o arquivo nao for um projeto valido."""
    if not zipfile.is_zipfile(path):
        raise ValueError("O arquivo não e um projeto do PGN Live Editor.")

    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        if "raw.txt" not in names:
            raise ValueError("Projeto inválido: não contem o texto bruto (raw.txt).")

        raw_text = archive.read("raw.txt").decode("utf-8")
        meta_payload = _read_json(archive, "meta.json", {})
        state_payload = _read_json(archive, "state.json", {})

        meta = ProjectMeta(
            version=int(meta_payload.get("version", FORMAT_VERSION)),
            title=str(meta_payload.get("title", "")),
            author=str(meta_payload.get("author", "")),
            source=str(meta_payload.get("source", "")),
            locale=str(meta_payload.get("locale", "pt")),
            saved_at=float(meta_payload.get("saved_at", 0.0)),
        )
        state = ProjectState(
            cursor_position=int(state_payload.get("cursor_position", 0)),
            ignored_issue_tokens=list(state_payload.get("ignored_issue_tokens", [])),
            completed_games=[int(index) for index in state_payload.get("completed_games", [])],
            accepted_corrections={
                str(key): int(value) for key, value in dict(state_payload.get("accepted_corrections", {})).items()
            },
            pdf_path=str(state_payload.get("pdf_path", "")),
            pdf_page=int(state_payload.get("pdf_page", 0)),
        )

        return Project(
            raw_text=raw_text,
            meta=meta,
            header_template=_read_json(archive, "headers.json", {}),
            # `from_payload` le tanto o formato novo (lista com escopo) quanto
            # o antigo (mapa simples) -- projeto salvo antes da fase 5 abre.
            dictionary=Dictionary.from_payload(_read_json(archive, "dictionary.json", {}), LAYER_PROJECT),
            state=state,
            path=path,
        )


def import_text_file(path: Path) -> str:
    """Le .pgn/.txt tolerando codificacoes comuns de material digitalizado."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Autosave / recuperacao
# --------------------------------------------------------------------------


@dataclass
class RecoverySnapshot:
    raw_text: str
    origin_path: Path | None
    saved_at: float
    snapshot_path: Path

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.saved_at)


def _recovery_file() -> Path:
    return recovery_dir() / "session.json"


def write_recovery_snapshot(raw_text: str, origin_path: Path | None) -> Path:
    """Grava o texto bruto atual para sobreviver a um fechamento anormal."""
    target = _recovery_file()
    payload = {
        "version": FORMAT_VERSION,
        "raw_text": raw_text,
        "origin_path": str(origin_path) if origin_path else "",
        "saved_at": time.time(),
    }

    handle, temp_name = tempfile.mkstemp(suffix=".json", dir=str(target.parent))
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(target)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return target


def read_recovery_snapshot() -> RecoverySnapshot | None:
    target = _recovery_file()
    if not target.is_file():
        return None

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    raw_text = payload.get("raw_text", "")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    origin = payload.get("origin_path") or ""
    return RecoverySnapshot(
        raw_text=raw_text,
        origin_path=Path(origin) if origin else None,
        saved_at=float(payload.get("saved_at", 0.0)),
        snapshot_path=target,
    )


def clear_recovery_snapshot() -> None:
    _recovery_file().unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Arquivos recentes
# --------------------------------------------------------------------------


def _recent_file() -> Path:
    return app_data_dir() / "recent.json"


def load_recent_files() -> list[Path]:
    target = _recent_file()
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    return [Path(entry) for entry in payload if isinstance(entry, str) and Path(entry).is_file()]


def remember_recent_file(path: Path) -> list[Path]:
    entries = [entry for entry in load_recent_files() if entry != path]
    entries.insert(0, path)
    entries = entries[:MAX_RECENT_FILES]
    try:
        _recent_file().write_text(
            json.dumps([str(entry) for entry in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return entries
