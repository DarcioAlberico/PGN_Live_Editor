import sys
from pathlib import Path


def main() -> None:
    # `--diagnostico ARQUIVO` grava o diagnostico e sai, sem abrir janela: e o
    # jeito de conferir um executavel empacotado (que nao tem console) e de
    # mandar o relatorio para suporte.
    if "--diagnostico" in sys.argv:
        from .services.diagnostics import build_diagnostic_report

        index = sys.argv.index("--diagnostico")
        target = sys.argv[index + 1] if index + 1 < len(sys.argv) else "pgn-live-editor-diagnostic.txt"
        Path(target).write_text(build_diagnostic_report(), encoding="utf-8")
        raise SystemExit(0)

    from PyQt6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
