import sys
from pathlib import Path

#: Intervalo de troca do GIL com a interface aberta (ver `main`).
UI_SWITCH_INTERVAL_S = 0.0005


def main() -> None:
    # Processo filho de OCR (R-304): despachado antes de qualquer import de Qt,
    # para nao abrir janela nem carregar DLLs do Qt junto com as do Torch.
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        from .services.worker_commands import run_worker

        raise SystemExit(run_worker(sys.argv[2:]))

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

    # A analise roda em Python numa thread de fundo e disputa o GIL com a
    # interface. Com o intervalo padrao (5 ms) cada volta do laco de eventos
    # esperava o GIL varias vezes: mediana de 15 ms por volta digitando num
    # livro de 1 MB, contra 1,4 ms com 0,5 ms (R-401).
    sys.setswitchinterval(UI_SWITCH_INTERVAL_S)
    app = QApplication(sys.argv)
    window = MainWindow()
    crash_safety.add_emergency_action(window.emergency_snapshot)
    crash_safety.add_error_listener(window.show_unexpected_error)
    window.show()
    code = app.exec()

    from .services.worker_host import WorkerHost

    if not WorkerHost.drain_abandoned(timeout_ms=5000):
        # Uma thread de trabalho ainda roda (o trabalho nao atendeu ao
        # cancelamento). Destruir o QThread vivo e qFatal no fim do
        # interpretador; sair direto e o unico fim limpo (S-08).
        import logging
        import os

        logging.getLogger(__name__).warning("Saindo com thread de trabalho ainda ativa")
        logging.shutdown()
        os._exit(code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
