# Empacotamento com PyInstaller (SPEC 6.2).
#
# O publico-alvo nao instala Python: o que ele quer e um `.exe` que abre. Este
# spec produz um executavel de pasta unica (`--onedir`), e nao um arquivo unico,
# de proposito -- o `--onefile` descompacta o Qt inteiro numa pasta temporaria a
# cada abertura, o que custa segundos de espera e costuma acordar o antivirus.
#
# Rodar da raiz do projeto:
#     uv run pyinstaller packaging/pgn-live-editor.spec --noconfirm
#
# Com o Stockfish de `engine/` junto (~100 MB a mais):
#     $env:PGN_BUNDLE_ENGINE = "1"; uv run pyinstaller packaging/pgn-live-editor.spec --noconfirm

import importlib.util
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PROJECT_ROOT = Path(SPECPATH).parent

# `substitutions.json` e `header_templates.json` sao lidos em tempo de execucao.
datas = collect_data_files("pgn_live_editor", includes=["data/*.json", "models/*.pt"])

# O adapter importa o serviço nativo sob demanda; estes módulos precisam ser
# conhecidos pelo PyInstaller mesmo que a importação aconteça dentro de uma
# thread, depois da janela já estar aberta.
diagram_ocr_modules = [
    "pgn_live_editor.chess_diagram_ocr.service",
    "pgn_live_editor.chess_diagram_ocr.config",
    "pgn_live_editor.chess_diagram_ocr.board_detection",
    "pgn_live_editor.chess_diagram_ocr.fen_utils",
    "pgn_live_editor.chess_diagram_ocr.inference",
    "pgn_live_editor.chess_diagram_ocr.pdf_io",
    "pgn_live_editor.chess_diagram_ocr.pdf_text",
    "pgn_live_editor.chess_diagram_ocr.preprocess",
    "pgn_live_editor.chess_diagram_ocr.semantics",
    "pgn_live_editor.chess_diagram_ocr.checkpoint",
    "pgn_live_editor.chess_diagram_ocr.decode",
    "pgn_live_editor.chess_diagram_ocr.model",
    "pgn_live_editor.chess_diagram_ocr.orientation",
    "pgn_live_editor.chess_diagram_ocr.detection",
    "pgn_live_editor.chess_diagram_ocr.detection.embedded",
    "pgn_live_editor.chess_diagram_ocr.detection.hybrid",
]

# OCR de texto (extra `text-ocr`): `services/text_ocr.py` importa o winrt dentro
# das funcoes, e o analisador nao segue esse caminho. Entra so se estiver
# instalado no ambiente que empacota.
text_ocr_modules = [
    name
    for name in (
        "winrt.runtime",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.windows.globalization",
        "winrt.windows.graphics.imaging",
        "winrt.windows.media.ocr",
        "winrt.windows.storage.streams",
    )
    if importlib.util.find_spec(name.split(".")[0]) is not None
]

# Stockfish junto do programa, so quando pedido: sao ~100 MB, e distribuir o
# binario obriga a levar a licenca GPL dele junto (Copying.txt, AUTHORS,
# README com o endereco do codigo-fonte). O codigo-fonte em `engine/stockfish/src`
# nao vai. O programa procura `engine/` dentro do pacote (services/engine_service.py).
engine_datas = []
if os.environ.get("PGN_BUNDLE_ENGINE") == "1":
    engine_dir = PROJECT_ROOT / "engine"
    for executable_path in sorted(engine_dir.rglob("stockfish*.exe")):
        target = str(executable_path.parent.relative_to(PROJECT_ROOT))
        engine_datas.append((str(executable_path), target))
        for license_name in ("Copying.txt", "AUTHORS", "README.md"):
            license_path = executable_path.parent / license_name
            if license_path.is_file():
                engine_datas.append((str(license_path), target))
    if not engine_datas:
        raise SystemExit("PGN_BUNDLE_ENGINE=1, mas nenhum stockfish*.exe em engine/")

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
    datas=datas + engine_datas,
    # O painel de PDF importa QtPdf por caminho que o analisador nao segue.
    hiddenimports=[
        "PyQt6.QtPdf",
        "PyQt6.QtPdfWidgets",
        "PyQt6.QtSvg",
        "PyQt6.QtSvgWidgets",
        # Os subcomandos `--worker` (R-304) sao importados por nome.
        "pgn_live_editor.services.worker_commands",
        "pgn_live_editor.services.diagram_ocr_process",
        "pgn_live_editor.services.full_pdf_recognition_process",
        "pgn_live_editor.services.ocr_warm_up",
        *diagram_ocr_modules,
        *text_ocr_modules,
    ],
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
