"""Preferencias globais em `app.db` (SPEC 5.1).

Sao as escolhas que valem para **o programa**, nao para um trabalho: idioma da
notacao, tema, fonte do editor, caminho do motor e o layout das janelas. Ficam
em SQLite no diretorio de dados do aplicativo, como a spec pede.

SQLite para uma duzia de chaves parece exagero, e para o conteudo e mesmo. Vale
por outra razao: gravacao atomica. O programa grava preferencia ao fechar a
janela, que e exatamente quando a maquina pode ser desligada no meio -- e um
JSON truncado nessa hora perde tudo, enquanto o SQLite ou grava ou nao grava.

Os valores sao JSON numa unica tabela `chave -> valor`. Não há esquema para
migrar: chave que nao existe devolve o padrao, chave desconhecida e ignorada.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .project_service import app_data_dir

DATABASE_FILE = "app.db"

THEME_DARK = "escuro"
THEME_LIGHT = "claro"
THEMES = (THEME_DARK, THEME_LIGHT)

LOCALES = ("pt", "en", "es", "de", "fr", "it")
LOCALE_LABEL = {
    "pt": "Português (C, D, T, B, R)",
    "en": "Inglês (N, Q, R, B, K)",
    "es": "Espanhol (C, D, T, A, R)",
    "de": "Alemão (S, D, T, L, K)",
    "fr": "Francês (C, D, T, F, R)",
    "it": "Italiano (C, D, T, A, R)",
}
# Nome curto, para a caixa do editor, e o que cada idioma aceita -- a dica da
# caixa mostra os dois sistemas porque o mesmo livro antigo pode usar os dois.
LOCALE_NAME = {
    "pt": "Português",
    "en": "Inglês",
    "es": "Espanhol",
    "de": "Alemão",
    "fr": "Francês",
    "it": "Italiano",
}
LOCALE_EXAMPLES = {
    "pt": "algébrica Cf3, Dxd5, Tfe1 · descritiva P4R, C3BR, PBDxP",
    "en": "algébrica Nf3, Qxd5, Rfe1 · descritiva P-K4, N-KB3, QPxB",
    "es": "algébrica Cf3, Dxd5, Tfe1 · descritiva P4R, C3AR, PADxP",
    "de": "algébrica Sf3, Dxd5, Tfe1",
    "fr": "algébrica Cf3, Dxd5, Tfe1",
    "it": "algébrica Cf3, Dxd5, Tfe1",
}


@dataclass
class Layout:
    """Onde estava cada coisa quando a janela fechou."""

    geometry: str = ""
    window_state: str = ""
    horizontal_splitter: str = ""
    vertical_splitter: str = ""
    games_panel_visible: bool = False
    pdf_panel_visible: bool = False
    issues_panel_visible: bool = True


@dataclass
class Preferences:
    locale: str = "pt"
    interface_locale: str = "pt"
    notation_locale: str = "pt"
    dictionary_locale: str = "pt"
    theme: str = THEME_DARK
    editor_font_family: str = "Consolas"
    editor_font_size: int = 11
    engine_path: str = ""
    engine_movetime_ms: int = 200
    engine_threshold_cp: int = 300
    engine_include_variations: bool = True
    auto_close_delimiters: bool = True
    layout: Layout = field(default_factory=Layout)

    @property
    def effective_notation_locale(self) -> str:
        """Compatibility view for projects created with the single locale."""
        return self.locale if self.notation_locale == "pt" and self.locale != "pt" else self.notation_locale

    @property
    def effective_dictionary_locale(self) -> str:
        return self.locale if self.dictionary_locale == "pt" and self.locale != "pt" else self.dictionary_locale

    def sanitized(self) -> Preferences:
        """Valor fora do conjunto conhecido volta ao padrao, sem reclamar."""
        base = Preferences()
        return replace(
            self,
            locale=self.locale if self.locale in LOCALES else base.locale,
            interface_locale=self.interface_locale if self.interface_locale in LOCALES else base.interface_locale,
            notation_locale=self.notation_locale if self.notation_locale in LOCALES else base.notation_locale,
            dictionary_locale=self.dictionary_locale if self.dictionary_locale in LOCALES else base.dictionary_locale,
            theme=self.theme if self.theme in THEMES else base.theme,
            editor_font_size=self.editor_font_size if 6 <= self.editor_font_size <= 48 else base.editor_font_size,
            engine_movetime_ms=max(10, min(self.engine_movetime_ms, 60_000)),
            engine_threshold_cp=max(50, min(self.engine_threshold_cp, 2_000)),
        )


def database_path() -> Path:
    return app_data_dir() / DATABASE_FILE


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path())
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    except sqlite3.Error:
        # Banco corrompido levanta **aqui**, com a conexao ja aberta: sem este
        # fechamento o arquivo ficaria preso ate o programa morrer.
        connection.close()
        raise
    return connection


def read_all() -> dict[str, Any]:
    try:
        with closing(_connect()) as connection:
            rows = connection.execute("SELECT key, value FROM preferences").fetchall()
    except (sqlite3.Error, OSError):
        return {}

    values: dict[str, Any] = {}
    for key, payload in rows:
        try:
            values[key] = json.loads(payload)
        except ValueError:
            continue
    return values


def write_all(values: dict[str, Any]) -> None:
    try:
        with closing(_connect()) as connection, connection:
            connection.executemany(
                "INSERT INTO preferences (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()],
            )
    except (sqlite3.Error, OSError, TypeError):
        # Preferencia nao vale travar o programa nem perder trabalho.
        pass


def load_preferences() -> Preferences:
    stored = read_all()
    base = Preferences()

    layout_payload = stored.get("layout")
    layout = (
        Layout(
            **{
                key: layout_payload[key]
                for key in Layout().__dataclass_fields__
                if isinstance(layout_payload, dict) and key in layout_payload
            }
        )
        if isinstance(layout_payload, dict)
        else Layout()
    )

    values = {key: stored[key] for key in base.__dataclass_fields__ if key in stored and key != "layout"}
    # Version 1 stored one locale for three independent concerns.  Migrate it
    # explicitly instead of silently coupling the new preferences again.
    if "locale" in stored:
        legacy = stored["locale"]
        values.setdefault("interface_locale", legacy)
        values.setdefault("notation_locale", legacy)
        values.setdefault("dictionary_locale", legacy)
    try:
        return replace(base, **values, layout=layout).sanitized()
    except TypeError:
        return base


def save_preferences(preferences: Preferences) -> None:
    payload = asdict(preferences.sanitized())
    write_all(payload)
