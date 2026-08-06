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

1. **Colar** o texto do PDF (ou **importar** um `.pgn`/`.txt` com `Ctrl+I`).
2. **Limpar a colagem** com `Ctrl+Shift+V`: remonta palavras cortadas no fim da linha,
   junta linhas quebradas, tira numeros de pagina e cabecalhos repetidos. Tem previa.
3. Ir marcando a estrutura **sem digitar delimitador nenhum**:
   selecionar a prosa e `Ctrl+K`, selecionar os lances alternativos e `Ctrl+9`.
4. **Renumerar** com `Ctrl+R` para o programa acertar todos os numeros de lance
   a partir do tabuleiro.
5. Conferir os alertas (`F8`) e o tabuleiro.
6. **Salvar o projeto** (`Ctrl+S`) em `.plez` - guarda o **texto bruto**, que e o trabalho de verdade.
7. **Exportar PGN** (`Ctrl+Shift+E`) quando a partida estiver pronta.

O texto bruto e a fonte da verdade; o PGN e uma saida derivada. Por isso *Salvar projeto*
e *Exportar PGN* sao acoes diferentes.

### Marcar a estrutura sem digitar chaves

| Gesto | Atalho | O que faz |
|---|---|---|
| Envolver em comentario | `Ctrl+K` | a selecao vira `{...}` |
| Envolver em variante | `Ctrl+9` | a selecao vira `(...)` **ja com o numero de lance certo**, e o lance seguinte da linha principal recupera o dele |
| Desenvolver | `Ctrl+Shift+K` | tira o `{}` ou `()` em volta do cursor |
| Paragrafo vira comentario | `Ctrl+Shift+C` | pega a prosa solta sob o cursor e a envolve em chaves |
| Renumerar documento | `Ctrl+R` | recalcula todos os numeros a partir do tabuleiro |
| Promover variante | `Ctrl+Shift+Up` | a variante vira linha principal e vice-versa |
| Conferir delimitadores | `Ctrl+Shift+B` | lista `{` e `(` sem fechamento |

Digitar `{` ou `(` com texto selecionado tambem envolve a selecao; sem selecao, o
delimitador ja entra fechado. Cada gesto e **um unico Ctrl+Z**.

### Copiar trechos

O menu **Copiar** e o botao direito sobre a arvore de lances oferecem: esta linha entre
parenteses, daqui ate o fim, sem comentarios, a FEN da posicao, a linha principal, ou
um PGN completo com `[FEN]`/`[SetUp]` a partir da posicao atual.

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
| `Ctrl+Shift+V` | limpar texto colado do PDF |
| `Ctrl+K` / `Ctrl+9` | envolver em comentario / variante |
| `Ctrl+Shift+K` / `Ctrl+Shift+C` | desenvolver / paragrafo vira comentario |
| `Ctrl+R` | renumerar documento |
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
