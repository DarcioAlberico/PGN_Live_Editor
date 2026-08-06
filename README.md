# PGN Live Editor

Aplicacao desktop em PyQt para editar texto bruto de partidas e exportar PGN.

## Requisitos

- `uv` instalado
- Python 3.12 disponivel localmente

## Uso com uv

Entre na pasta do projeto:

```powershell
cd pgn_live_editor
```

Se existir um `.venv` antigo apontando para um Python removido, recrie o ambiente:

```powershell
Remove-Item -Recurse -Force .venv
uv sync
```

Executar a aplicacao:

```powershell
uv run pgn-live-editor
```

Compatibilidade com o launcher antigo:

```powershell
uv run python main.py
```

Executar os testes:

```powershell
uv run python -m unittest discover -s tests
```
