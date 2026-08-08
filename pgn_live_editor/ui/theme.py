"""Duas paletas: escura e clara.

As cores do realce estavam espalhadas pelo codigo da interface, todas escolhidas
para fundo escuro. Um tema claro nao e trocar o fundo -- e trocar **o conjunto**,
porque verde-claro sobre branco nao se le. Por isso as duas paletas moram aqui
juntas, com as mesmas chaves, e quem desenha pergunta a paleta em vez de
escrever a cor.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

from ..services.preferences_service import THEME_DARK, THEME_LIGHT

#: Chaves usadas pelo realce do editor e pelos paineis.
PALETTES: dict[str, dict[str, str]] = {
    THEME_DARK: {
        "window": "#1b1d22",
        "base": "#22252b",
        "text": "#e6e6e6",
        "muted": "#8a8f98",
        "board_background": "#1b1d22",
        # Realce do editor
        "move": "#78aefc",
        "move_variation": "#9fc4ff",
        "fixed": "#95e1a7",
        "fixed_underline": "#5bb974",
        "review": "#f0c05a",
        "error_text": "#ffd5d5",
        "error_underline": "#ff6666",
        "confirmed_dimmed": "#5a6070",
        "current_move": "#4b5568",
    },
    THEME_LIGHT: {
        "window": "#f4f5f7",
        "base": "#ffffff",
        "text": "#1d1f24",
        "muted": "#6b7280",
        "board_background": "#e8e9ec",
        "move": "#1a56b8",
        "move_variation": "#4a7fd4",
        "fixed": "#1c7a3c",
        "fixed_underline": "#1c7a3c",
        "review": "#9a6a00",
        "error_text": "#8b1a1a",
        "error_underline": "#c62828",
        "confirmed_dimmed": "#a3a8b3",
        "current_move": "#cfd8e8",
    },
}


def palette_of(theme: str) -> dict[str, str]:
    return PALETTES.get(theme, PALETTES[THEME_DARK])


def color(theme: str, key: str) -> QColor:
    return QColor(palette_of(theme)[key])


def apply_theme(app, theme: str) -> None:
    """Aplica a paleta ao aplicativo inteiro."""
    colors = palette_of(theme)

    qt_palette = QPalette()
    qt_palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
    qt_palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    qt_palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
    qt_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["window"]))
    qt_palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    qt_palette.setColor(QPalette.ColorRole.Button, QColor(colors["window"]))
    qt_palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    qt_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["base"]))
    qt_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
    app.setPalette(qt_palette)
