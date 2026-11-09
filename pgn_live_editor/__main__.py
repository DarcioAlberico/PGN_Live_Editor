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

    # Antes da QApplication: sem `sys.excepthook` proprio, uma excecao num slot
    # faz o PyQt6 abortar o processo sem traceback (S-03).
    from .services import crash_safety
    from .services.project_service import app_data_dir

    crash_safety.install(app_data_dir() / "logs")

    from PyQt6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    crash_safety.add_emergency_action(window.emergency_snapshot)
    crash_safety.add_error_listener(window.show_unexpected_error)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
