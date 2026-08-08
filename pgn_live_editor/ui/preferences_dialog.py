"""Preferencias do programa (SPEC 5.1).

O que muda aqui vale para todos os trabalhos, e nao para um: idioma da notacao,
tema, fonte do editor e o motor. O que e de um livro so -- cabecalho, dicionario
do projeto, PDF -- fica no `.plez`, nao aqui.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..services.engine_service import ENGINE_ENV_VAR, discover_engine_path
from ..services.preferences_service import (
    LOCALE_LABEL,
    LOCALES,
    THEME_DARK,
    THEME_LIGHT,
    Preferences,
)

THEME_LABEL = {THEME_DARK: "Escuro", THEME_LIGHT: "Claro"}


class PreferencesDialog(QDialog):
    def __init__(self, preferences: Preferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferencias")
        self.resize(560, 340)
        self._preferences = preferences

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.cmb_locale = QComboBox()
        for locale in LOCALES:
            self.cmb_locale.addItem(LOCALE_LABEL[locale], locale)
        self.cmb_locale.setCurrentIndex(max(0, LOCALES.index(preferences.locale)))
        self.cmb_locale.setToolTip(
            "Idioma da notacao dos livros. O programa ainda confirma pelo proprio "
            "documento antes de ler 'R' como Rei."
        )
        form.addRow("Notacao dos livros:", self.cmb_locale)

        self.cmb_theme = QComboBox()
        for theme, label in THEME_LABEL.items():
            self.cmb_theme.addItem(label, theme)
        self.cmb_theme.setCurrentIndex(0 if preferences.theme == THEME_DARK else 1)
        form.addRow("Tema:", self.cmb_theme)

        self.cmb_font = QFontComboBox()
        self.cmb_font.setCurrentFont(self.cmb_font.font())
        self.cmb_font.setCurrentText(preferences.editor_font_family)
        form.addRow("Fonte do editor:", self.cmb_font)

        self.spn_font_size = QSpinBox()
        self.spn_font_size.setRange(6, 48)
        self.spn_font_size.setValue(preferences.editor_font_size)
        form.addRow("Tamanho:", self.spn_font_size)

        self.chk_auto_close = QCheckBox("Fechar '{' e '(' automaticamente")
        self.chk_auto_close.setChecked(preferences.auto_close_delimiters)
        form.addRow("", self.chk_auto_close)

        engine_row = QHBoxLayout()
        self.txt_engine = QLineEdit(preferences.engine_path)
        self.txt_engine.setPlaceholderText(discover_engine_path("") or f"nenhum encontrado (veja {ENGINE_ENV_VAR})")
        btn_engine = QPushButton("Procurar...")
        btn_engine.clicked.connect(self._choose_engine)
        engine_row.addWidget(self.txt_engine, 1)
        engine_row.addWidget(btn_engine)
        form.addRow("Motor UCI:", engine_row)

        self.spn_movetime = QSpinBox()
        self.spn_movetime.setRange(10, 60_000)
        self.spn_movetime.setSingleStep(50)
        self.spn_movetime.setSuffix(" ms")
        self.spn_movetime.setValue(preferences.engine_movetime_ms)
        self.spn_movetime.setToolTip("Tempo por posicao na conferencia do motor")
        form.addRow("Tempo por lance:", self.spn_movetime)

        self.spn_threshold = QSpinBox()
        self.spn_threshold.setRange(50, 2_000)
        self.spn_threshold.setSingleStep(50)
        self.spn_threshold.setSuffix(" centipeoes")
        self.spn_threshold.setValue(preferences.engine_threshold_cp)
        self.spn_threshold.setToolTip("Queda de avaliacao a partir da qual o lance vira suspeita")
        form.addRow("Limiar de suspeita:", self.spn_threshold)

        layout.addLayout(form)

        note = QLabel(
            "O layout das janelas e guardado sozinho ao fechar o programa. "
            "Cabecalho, dicionario do projeto e PDF pertencem ao trabalho, e ficam no .plez."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8a8f98;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_engine(self):
        current = self.txt_engine.text() or str(Path.cwd())
        file_path, _filter = QFileDialog.getOpenFileName(
            self, "Executavel do motor UCI", current, "Executaveis (*.exe);;Todos os arquivos (*)"
        )
        if file_path:
            self.txt_engine.setText(file_path)

    def values(self) -> Preferences:
        """As preferencias com o que o usuario escolheu, ja saneadas."""
        return replace(
            self._preferences,
            locale=self.cmb_locale.currentData(),
            theme=self.cmb_theme.currentData(),
            editor_font_family=self.cmb_font.currentText(),
            editor_font_size=self.spn_font_size.value(),
            auto_close_delimiters=self.chk_auto_close.isChecked(),
            engine_path=self.txt_engine.text().strip(),
            engine_movetime_ms=self.spn_movetime.value(),
            engine_threshold_cp=self.spn_threshold.value(),
        ).sanitized()
