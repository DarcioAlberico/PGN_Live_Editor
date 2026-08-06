# PGN Live Editor

Editor de texto assistido para transformar texto de livro de xadrez em PGN confiavel.
Cole o texto do PDF, va marcando comentarios, variantes e linha principal, e o programa
reconhece a estrutura em tempo real com tabuleiro sincronizado.

Documentacao do projeto: [analise tecnica](docs/ANALISE.md) - [especificacao](docs/SPEC.md) - [roadmap](docs/ROADMAP.md).

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

## Fluxo de trabalho

1. **Importar** (`Ctrl+I`) um `.pgn`/`.txt`, ou simplesmente colar o texto do PDF no editor.
2. Ir marcando a estrutura: `{comentarios}`, `(variantes)`, numeros de lance.
3. Acompanhar no tabuleiro e no preview o que o parser entendeu.
4. **Salvar o projeto** (`Ctrl+S`) em `.plez` - guarda o **texto bruto**, que e o trabalho de verdade.
5. **Exportar PGN** (`Ctrl+Shift+E`) quando a partida estiver pronta.

O texto bruto e a fonte da verdade; o PGN e uma saida derivada. Por isso *Salvar projeto*
e *Exportar PGN* sao acoes diferentes.

### Nada e corrigido em segredo

O painel de alertas na parte de baixo mostra tudo que o parser fez com o texto:

| Marca no editor | Significado |
|---|---|
| azul | lance entendido sem nenhuma duvida |
| verde sublinhado | corrigido com alta confianca (ex.: `Cf3` -> `Nf3`, `4Jf3` -> `Nf3`) |
| amarelo pontilhado | corrigido **em carater provisorio** -- precisa de conferencia |
| vermelho ondulado | nao reconhecido; nenhum lance foi aplicado |

Cada alerta tem quatro acoes: **Aceitar correcao** (grava no texto bruto), **Virar comentario**,
**Aprender** (guarda a correcao no dicionario do projeto) e **Ignorar**.
`F8` percorre os alertas, `F4` aceita o selecionado, `F9` mostra/oculta o painel.

Prosa nunca e alterada: `chances`, `Cada`, `Chega` e `mate` continuam exatamente como voce
escreveu. A troca de letra de peca so acontece sobre um token com cara de lance **e** so se o
resultado for legal na posicao.

### Seguranca do trabalho

- **Autosave** a cada 30 s ou a cada 200 caracteres digitados.
- **Recuperacao de sessao**: se o programa fechar sem salvar, ao reabrir ele oferece o texto de volta.
- **Conferencia na exportacao**: todo PGN gerado e relido pelo `python-chess` e comparado
  com a analise antes de ser gravado. Divergencia gera aviso, nunca um arquivo silenciosamente errado.

### Atalhos

| Atalho | Acao |
|---|---|
| `Ctrl+N` / `Ctrl+O` / `Ctrl+S` | novo / abrir / salvar projeto |
| `Ctrl+I` | importar PGN ou texto |
| `Ctrl+Shift+E` | exportar PGN |
| `Ctrl+Left` / `Ctrl+Right` | lance anterior / proximo |
| `Ctrl+F` | inverter tabuleiro |
| `F8` / `Shift+F8` | proximo / anterior alerta |
| `F4` | aceitar a correcao do alerta selecionado |
| `F9` | mostrar/ocultar o painel de alertas |
| `Ctrl+Z` | desfazer (funciona tambem em *Join Lines* e *Inserir Headers*) |

## Desenvolvimento

Executar os testes:

```powershell
uv run python -m unittest discover -s tests
```

Lint:

```powershell
uv run ruff check pgn_live_editor tests
```
