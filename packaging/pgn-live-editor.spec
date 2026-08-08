# Empacotamento com PyInstaller (SPEC 6.2).
#
# O publico-alvo nao instala Python: o que ele quer e um `.exe` que abre. Este
# spec produz um executavel de pasta unica (`--onedir`), e nao um arquivo unico,
# de proposito -- o `--onefile` descompacta o Qt inteiro numa pasta temporaria a
# cada abertura, o que custa segundos de espera e costuma acordar o antivirus.
#
# Rodar da raiz do projeto:
#     uv run pyinstaller packaging/pgn-live-editor.spec --noconfirm

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PROJECT_ROOT = Path(SPECPATH).parent

# `substitutions.json` e `header_templates.json` sao lidos em tempo de execucao.
datas = collect_data_files("pgn_live_editor", includes=["data/*.json"])

# O que o programa nao usa nao precisa viajar junto: sao ~40 MB de Qt a menos.
excluded_qt = [
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtMultimedia",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtTest",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
]

analysis = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    # O painel de PDF importa QtPdf por caminho que o analisador nao segue.
    hiddenimports=["PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtSvg", "PyQt6.QtSvgWidgets"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest", *excluded_qt],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PGN Live Editor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="PGN Live Editor",
)
