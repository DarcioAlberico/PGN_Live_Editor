# Especificação — PGN Live Editor v1.0

> Documento de destino. Descreve **o que o programa deve ser**, não o que ele é hoje.
> Diagnóstico do estado atual: [ANALISE.md](ANALISE.md). Ordem de execução: [ROADMAP.md](ROADMAP.md).

---

## 0. Missão

**Transformar um livro de xadrez impresso em PGN confiável, no menor tempo possível, sem perder nada em silêncio.**

Três princípios que decidem qualquer dúvida de design:

1. **O texto bruto é o trabalho.** O PGN é uma saída derivada. Nunca perca o texto bruto, nunca o modifique sem o usuário pedir.
2. **Nada é corrigido em segredo.** Toda correção automática é visível, rastreável e reversível com um clique.
3. **Duvidar é melhor que adivinhar.** Diante de ambiguidade, o programa levanta um alerta — nunca escolhe sozinho e segue em frente.

O princípio 3 é a inversão mais importante em relação ao comportamento atual.

---

## 1. Arquitetura de destino

```
pgn_live_editor/
├─ core/
│  ├─ normalizer.py          # SÓ transformações inequívocas (ver §2.1)
│  ├─ candidates.py          # NOVO — resolução ambígua com contexto de tabuleiro
│  ├─ tokenizer.py
│  ├─ parser.py
│  ├─ move_validator.py
│  ├─ move_tree.py
│  ├─ pgn_headers.py
│  ├─ game_splitter.py       # NOVO — várias partidas num documento
│  ├─ text_ops.py            # NOVO — gestos de edição estrutural
│  ├─ paste_cleanup.py       # NOVO — limpeza de texto de PDF
│  ├─ issues.py              # NOVO — modelo de alerta com confiança e ações
│  └─ fen_tools.py           # NOVO — FEN/SetUp, diagramas
├─ services/
│  ├─ project_service.py     # NOVO — salvar/abrir/autosave/recuperação
│  ├─ export_service.py      # NOVO — exportação com validação de round-trip
│  ├─ dictionary_service.py  # NOVO — dicionário OCR persistente e aprendiz
│  ├─ engine_service.py      # NOVO — Stockfish opcional
│  └─ parse_worker.py        # NOVO — pipeline fora da thread de UI
├─ ui/
│  ├─ main_window.py
│  ├─ editor_panel.py
│  ├─ chess_panel.py
│  ├─ board_view.py
│  ├─ variant_builder_panel.py
│  ├─ issues_panel.py        # NOVO
│  ├─ games_panel.py         # NOVO — lista de partidas do livro
│  ├─ header_dialog.py       # NOVO
│  ├─ dictionary_dialog.py   # NOVO
│  ├─ preferences_dialog.py  # NOVO
│  └─ pdf_panel.py           # NOVO (fase 5)
└─ data/
   ├─ substitutions.json     # dicionário base por idioma
   └─ header_templates.json
```

Renomear o pacote `app` → `pgn_live_editor`, eliminar o `sys.path.append` e padronizar imports relativos dentro do pacote.

### 1.1 Fluxo de dados

```
texto bruto (fonte da verdade, nunca alterado pelo parser)
   │
   ├─ paste_cleanup ······· (opcional, explícito, undoável)
   │
   ├─ game_splitter ······· lista de segmentos [cabeçalho + corpo + offset]
   │        │
   │        └─ por segmento, em worker thread:
   │             normalizer (inequívoco) → spans preservados
   │                 └─ tokenizer
   │                      └─ parser + candidates (ambíguo, com tabuleiro)
   │                           ├─ AST  →  tabuleiro, preview, árvore
   │                           └─ issues → painel de alertas
   │
   └─ export_service → PGN validado por round-trip
```

---

## 2. Núcleo de reconhecimento

### 2.1 Normalizador: só o que é inequívoco

O normalizador atual mistura duas coisas de natureza oposta. Separá-las é a mudança arquitetural central desta spec.

**Fica no pré-passe** (sempre seguro, sem contexto):
- Normalização Unicode NFKC, remoção de soft-hyphen (`­`) e zero-width.
- Glifos de peça `♔♕♖♗♘♙ ♚♛♜♝♞♟` → `KQRBN` / vazio.
- Variantes de traço (`–`, `—`, `−`) → `-`; aspas tipográficas.
- `0-0` → `O-O`, `0-0-0` → `O-O-O` **apenas** quando isolado por fronteira de token.
- Espaçamento de número de lance.

**Sai do pré-passe** e vai para `candidates.py` (§2.3):
- Mapeamento de idioma (`C→N`, `D→Q`, `T→R`, `L→B`, `S→N`, `A→B`).
- Substituições de OCR (`4J→N`, `tj→N`, `tD→N`, …).
- `ch → +`, `mate → #`.

> **Corrige D1, D2.** Nenhuma dessas transformações pode mais tocar prosa, porque só é tentada sobre um token que já foi classificado como candidato a lance e só é aceita se o resultado for legal na posição.

**Regex de número de lance** (corrige D5) — consumir *todos* os pontos consecutivos:

```python
MOVE_NUMBER_RE = r'\b(\d+)\s*((?:\.\s*)+)'
# 1 ponto  -> "N. "     |   2+ pontos -> "N... "
```

| Entrada | Saída exigida |
|---|---|
| `10.Nxd4` | `10. Nxd4` |
| `10...Nxd4` | `10... Nxd4` |
| `10. ... Nxd4` | `10... Nxd4` |
| `10....Nxd4` | `10... Nxd4` |
| `10 ... Nxd4` | `10... Nxd4` |

**Invariante testável:** para qualquer texto, `normalize()` não pode alterar nenhum caractere que esteja dentro de `{}` nem nenhuma palavra que não case o padrão de candidato a lance. Escrever isso como teste de propriedade.

### 2.2 Tokenizador

- **Chave não fechada** (corrige D7): `{` sem `}` fecha implicitamente no **fim da linha**, não no fim do documento. O mesmo critério vale em `_apply_outside_comments`.
- **Sufixos de livro** (corrige D8): aceitar após o SAN, sem quebrar o token — `N` (novidade), `e.p.`, `‡`, `∆`, `⌓`, `→`, `↑`, `⇆`, `N!`, `!N`. Emitir como token `SUFFIX` anexado ao lance.
- **Lance nulo**: `--`, `Z0`, `(…)` vazio → token `NULL_MOVE`, sem quebrar a árvore.
- **Token candidato a lance** (novo tipo `MOVE_CANDIDATE`): qualquer token que case `^[A-Za-z0-9][A-Za-z0-9\-+#=!?]{1,7}$` e esteja numa região de notação, mas que não seja SAN válido. É o que alimenta §2.3 e **elimina o descarte silencioso (D3)**.
- **Resultado** só é reconhecido como `RESULT` quando estiver no fim de um segmento ou seguido de fim de linha/documento — `*` no meio da prosa vira `TEXT`.

### 2.3 `candidates.py` — resolução ambígua com contexto

Substitui o `attempt_levenshtein_rescue` atual (corrige D4).

```python
@dataclass
class Candidate:
    san: str            # lance legal proposto
    confidence: float   # 0.0 – 1.0
    source: str         # 'dicionario' | 'idioma' | 'levenshtein' | 'motor'
    rationale: str      # "Cf3 → Nf3 (mapa pt)" — texto mostrado ao usuário

def resolve(raw_token: str, board: chess.Board, dictionary: Dict[str, str],
            locale: str) -> List[Candidate]:  # ordenado por confiança
```

Regras de pontuação, em ordem de prioridade:

1. **Dicionário do usuário** casa exato → `confidence = 0.99`.
2. **Mapa de idioma** produz lance legal → `0.95`.
3. **Levenshtein**, com limiar **proporcional**: `max_dist = 1 if len(token) <= 3 else 2`.
4. Penalidades que **impedem** aplicação automática:
   - troca a letra da peça (`Kd5` → `d5`, `Nc6` → `Nc3`) → teto de `0.60`;
   - troca a casa de destino → teto de `0.70`;
   - existe outro candidato à mesma distância → teto de `0.40`.
5. **Motor de xadrez** (se ativo, §5.3) como desempate entre candidatos ≥ 0.60.

**Política de aplicação:**

| Confiança | Comportamento |
|---|---|
| ≥ 0.90 | aplica, marca em verde no editor, registra `info` no painel |
| 0.60 – 0.89 | **não aplica**; marca em amarelo, `warning` no painel com botão *Aceitar* |
| < 0.60 | não aplica; marca em vermelho, `error` no painel com lista de candidatos |
| sem candidato | `error` "lance não reconhecido" — **nunca some em silêncio** |

### 2.4 Modelo de alerta (`issues.py`)

```python
@dataclass
class Issue:
    severity: Literal['info', 'warning', 'error']
    code: str                       # 'UNRECOGNIZED_MOVE', 'AUTO_CORRECTED', …
    message: str
    raw_span: Tuple[int, int]       # posição no texto bruto → clique navega
    candidates: List[Candidate]
    actions: List[str]              # 'accept' | 'ignore' | 'learn' | 'to_comment'
```

Códigos mínimos: `UNRECOGNIZED_MOVE`, `AUTO_CORRECTED`, `AMBIGUOUS_MOVE`, `ILLEGAL_MOVE`, `UNCLOSED_BRACE`, `UNBALANCED_PAREN`, `ORPHAN_VARIATION`, `BAD_MOVE_NUMBER`, `MISSING_RESULT`, `INVALID_DATE`, `SUSPECT_BLUNDER`.

### 2.5 Múltiplas partidas (`game_splitter.py`) — corrige D6

```python
@dataclass
class GameSegment:
    index: int
    headers: Dict[str, str]
    body: str
    raw_offset: int         # deslocamento no texto bruto completo
    title: str              # "13. Smyslov – Rudakovsky, Moscou 1945"
```

Regra de corte: início do documento, ou toda linha `[Tag "…"]` precedida de linha em branco. Fora isso, um separador configurável (ex.: `=== Partida N ===`) permite dividir sem cabeçalho.

O editor mantém **um** documento de texto e **uma lista** de segmentos. O tabuleiro e o preview seguem o segmento que contém o cursor. Corpo sem cabeçalho no topo do arquivo continua sendo a partida 1.

### 2.6 FEN / posição inicial — corrige D10

- `extract_pgn_headers` já lê `FEN`; o editor passa `ast = parser.parse(tokens, start_fen=headers.get('FEN', STARTING_FEN))`.
- Ao exportar, se `FEN` existe → forçar `[SetUp "1"]`; se não existe → remover `SetUp`.
- Botão **"Usar posição atual como FEN inicial"** no painel de xadrez.
- Marcadores de diagrama do livro (`{[#]}`, `[D]`, `(diagrama)`) reconhecidos como `DIAGRAM` e opcionalmente convertidos em comentário `{[#]}` padrão ChessBase.

### 2.7 Renderização e exportação

- Emitir número de lance preto após **todo** fechamento de variante e após comentário: `) 12... Be6` (corrige D13).
- Validar cabeçalhos: `Date` → `AAAA.MM.DD` com `??`; `Result` ∈ `{1-0, 0-1, 1/2-1/2, *}`; `Round` numérico ou `?`.
- **Validação obrigatória de round-trip antes de gravar:** reler o PGN gerado com `chess.pgn.read_game`, comparar contagem de nós e lista de lances da linha principal com a AST. Divergência → diálogo bloqueante com o diff, e a opção de salvar mesmo assim.

---

## 3. Edição estrutural — o coração do fluxo

É aqui que o programa deixa de ser "um parser com tabuleiro" e vira uma ferramenta de produção. Todos os gestos operam sobre o **texto bruto**, entram no histórico de desfazer do Qt e disparam reparse.

### 3.1 Envolver seleção

| Gesto | Atalho | Comportamento |
|---|---|---|
| Comentário | `Ctrl+K` | envolve a seleção em `{}`, apara espaços nas bordas, escapa `{}` internos |
| Variante | `Ctrl+9` | envolve em `()`, **insere o número de lance correto** no primeiro lance interno e **restaura o número** do lance seguinte da linha principal |
| Desenvolver | `Ctrl+Shift+K` | remove o delimitador `{}` ou `()` que contém o cursor |
| Parágrafo → comentário | `Ctrl+Shift+C` | pega o parágrafo solto sob o cursor e o anexa como comentário do lance anterior |

O `Ctrl+9` é o gesto que o fluxo do livro mais usa: você seleciona `4...Nf6 5.Be3` no meio do texto e vira `(4... Nf6 5. Be3)` já com a numeração certa, sem digitar nada.

### 3.2 Copiar trechos

Menu de contexto na **árvore de lances** e na **seleção do editor**:

| Ação | Saída |
|---|---|
| Copiar variante | `(12... Nxd4 13. Bxd4 e5)` — pronta para colar em outro ponto |
| Copiar daqui até o fim | movetext da posição atual até o fim da linha |
| Copiar sem comentários | só os lances, para conferência rápida |
| Copiar FEN | FEN da posição atual |
| Copiar como partida | PGN completo com `[FEN]` + `[SetUp "1"]` da posição atual |
| Copiar linha principal | movetext da mainline, sem variantes |

**Colar inteligente de linha:** ao colar um trecho de movetext no editor, renumerar automaticamente a partir da posição do tabuleiro no ponto de inserção.

### 3.3 Reorganizar

| Ação | Descrição |
|---|---|
| Promover variante | a variante vira linha principal; a continuação antiga vira variante |
| Rebaixar para variante | inverso |
| Mover variante | subir/descer entre variantes irmãs |
| Renumerar documento | recalcula **todos** os números de lance a partir do tabuleiro; conserta de uma vez os `10... .`, números faltando após variante e numeração errada do OCR |
| Reancorar variante | forçar a variante a partir de outro lance quando a heurística errar |

### 3.4 Limpeza de colagem (`paste_cleanup.py`)

Substitui o *Join Lines* atual (uma regex). Diálogo com pré-visualização e caixas de seleção, tudo desfazível:

- juntar linhas quebradas por largura de página, **preservando** quebras de parágrafo;
- desfazer hifenização (`ma-\nnobra` → `manobra`);
- remover números de página, cabeçalhos e rodapés que se repetem;
- remover marcas de nota de rodapé coladas em lances;
- normalizar espaços múltiplos e espaços antes de pontuação;
- remover soft-hyphen e caracteres de largura zero.

### 3.5 Correções obrigatórias de edição

- **Nunca mais `setPlainText`** (corrige D11): toda operação usa `QTextCursor` com `beginEditBlock`/`endEditBlock`, preservando desfazer, cursor e rolagem.
- Auto-fechamento de `{`, `(` e `[` com opção de desligar; `Ctrl+Shift+B` verifica balanceamento no documento inteiro e lista os pontos abertos.

---

## 4. Interface

### 4.1 Layout

```
┌ menu / barra de ferramentas ─────────────────────────────────────────────┐
├──────────────┬────────────────────────────────┬─────────────────────────┤
│ Partidas     │  Editor de texto bruto         │  Tabuleiro              │
│ (livro)      │  (destaque sintático ao vivo)  │  Preview PGN            │
│              │                                │  Construtor de variante │
│ ✓ 1. Smyslov │                                │                         │
│ ✓ 2. …       │                                │                         │
│ ▸ 3. …       │                                │                         │
├──────────────┴────────────────────────────────┴─────────────────────────┤
│ Alertas: ⚠ 3 avisos · ✖ 1 erro    [Aceitar] [Ignorar] [Aprender]        │
├─────────────────────────────────────────────────────────────────────────┤
│ status: partida 3/64 · 41 lances · FEN … · autosave há 12 s             │
└─────────────────────────────────────────────────────────────────────────┘
```

Cada painel é ocultável (`F9`–`F12`) e o layout é persistido entre sessões.

### 4.2 Painel de alertas

Lista ordenada por posição no texto, com filtro por severidade. Cada linha: ícone, mensagem, trecho de contexto. Clique navega até o texto. Ações por alerta: **Aceitar correção**, **Ignorar** (silencia só aquela ocorrência), **Aprender** (grava no dicionário do usuário), **Virar comentário**. Navegação por `F8` / `Shift+F8`.

### 4.3 Destaque sintático (corrige D12)

Separar em duas operações independentes:
- `_apply_syntax_highlight(ast)` — só no reparse; ranges indexados por bloco (`dict[block_number, list]`) para eliminar a varredura O(blocos × lances);
- `_apply_current_selection()` — no movimento do cursor, só `setExtraSelections`, **sem** `rehighlight()`.

Categorias: lance da linha principal · lance de variante (mais claro por profundidade) · comentário · NAG · resultado · **corrigido automaticamente** (verde, sublinhado pontilhado) · **ambíguo** (amarelo) · **não reconhecido** (ondulado vermelho). O contraste entre "confirmado" e "corrigido" é requisito explícito do modo dupla visão.

### 4.4 Tabuleiro

- Coordenadas ativadas, inversão (`Ctrl+F`), arrastar-e-soltar com peça fantasma.
- **Diálogo de promoção** com as quatro peças (corrige D13), com `Shift+solta` = dama direto.
- Navegação: `←` `→` lance, `↑` `↓` entre variantes, `Home`/`End`, `Ctrl+←` início da variante.
- Setas e casas coloridas (`[%cal]`, `[%csl]`) para anotação visual, exportadas como comentário padrão.

### 4.5 Atalhos

| Atalho | Ação | Atalho | Ação |
|---|---|---|---|
| `Ctrl+N/O/S` | novo / abrir / salvar projeto | `Ctrl+K` | envolver em comentário |
| `Ctrl+Shift+E` | exportar PGN | `Ctrl+9` | envolver em variante |
| `Ctrl+Shift+V` | colar com limpeza | `Ctrl+Shift+K` | desenvolver |
| `F8` | próximo alerta | `Ctrl+R` | renumerar documento |
| `Ctrl+Espaço` | sugerir lance legal no cursor | `Ctrl+F` | inverter tabuleiro |
| `Ctrl+Shift+P` | próxima partida | `Ctrl+D` | inserir símbolo (paleta) |

---

## 5. Serviços

### 5.1 Projeto e persistência (corrige D9)

Arquivo único `.plez` (ZIP), portátil e versionável:

```
projeto.plez
├─ meta.json      versão, livro, autor, fonte, idioma, data
├─ raw.txt        texto bruto completo — fonte da verdade
├─ headers.json   template do livro + cabeçalhos por partida
├─ dictionary.json  substituições aprendidas neste projeto
└─ state.json     cursor, partida atual, partidas concluídas, layout
```

- **Autosave** a cada 30 s e a cada 200 caracteres digitados, em `~/.pgn_live_editor/recovery/`.
- **Recuperação de sessão** ao abrir após fechamento anormal.
- *Arquivos recentes*, *Importar PGN existente* (reabre um PGN como texto bruto para continuar editando), *Importar .txt*.
- Preferências globais em SQLite (`~/.pgn_live_editor/app.db`): idioma padrão, dicionário global, caminho do motor, tema, layout.

### 5.2 Dicionário OCR (ativa o código morto D13)

`dictionary_service.py` com três camadas: **base** (embutida, por idioma) → **global do usuário** → **do projeto** (mais específica vence).

Editor com tabela `padrão → substituição`, escopo, contagem de uso e teste ao vivo contra o texto atual. Toda substituição carrega escopo explícito: `token_inteiro`, `prefixo_de_lance` ou `livre` — e `livre` exibe aviso de risco (é o que causa `chances → +ances` hoje).

**Aprendizado:** quando o usuário aceita a mesma correção 3 vezes, o programa oferece gravá-la no dicionário do projeto com um clique.

### 5.3 Motor de xadrez (opcional)

UCI (Stockfish), caminho configurável, sempre desligável, sempre fora da thread de UI.

Três usos, em ordem de valor:

1. **Detecção de erro de transcrição.** Se um lance da linha principal de um livro provoca queda de avaliação acima de um limiar (ex.: 300 cp) e o livro não o marca com `?` ou `??`, é forte indício de OCR errado, não de erro do mestre. Alerta `SUSPECT_BLUNDER`. É o uso mais valioso e nenhum conversor comum faz isso.
2. **Desempate de candidatos** entre lances legais com confiança parecida.
3. **Verificação de variantes** — avaliação rápida ao final de uma linha, para conferir se ela bate com o símbolo do livro (`±`, `∓`, `=`).

### 5.4 Parse fora da thread de UI

`parse_worker.py` em `QThread` com fila de tamanho 1 (colapsa pedidos pendentes). Debounce adaptativo: 150 ms para documentos < 20 KB, 400 ms acima disso. Parse **incremental por segmento**: só o `GameSegment` que contém a edição é reprocessado. Barra de status mostra "analisando…" sem congelar a digitação.

### 5.5 Exportação

- PGN de uma partida, de partidas selecionadas ou do livro inteiro em um arquivo.
- `.txt` normalizado (para conferência lado a lado com o livro).
- `.json` estrutural (AST completa, com spans — útil para ferramentas externas).
- Sempre com validação de round-trip (§2.7) e relatório final: partidas exportadas, alertas por severidade, lances auto-corrigidos.

### 5.6 Painel de PDF (fase 5)

`QtPdf` ao lado do editor: abrir o PDF do livro, navegar por páginas, selecionar texto e enviar ao cursor com `Ctrl+Enter`. Página atual memorizada por projeto. Botão *"próxima página → anexar"* para avançar sem sair do teclado.

---

## 6. Qualidade

### 6.1 Testes exigidos

| Camada | Conteúdo |
|---|---|
| Unidade | os 29 testes atuais + um caso de regressão para **cada** defeito D1–D14 |
| Propriedade | `normalize()` não altera nada dentro de `{}`; não altera palavra que não seja candidata a lance; `renumber()` é idempotente |
| Corpus | páginas reais de livro em `tests/corpus/*.txt` com PGN esperado em `*.expected.pgn`, comparadas por lista de lances e contagem de nós |
| Round-trip | todo PGN exportado relê no `python-chess` com zero erros e mesma contagem de nós |
| Performance | reparse de 100 KB abaixo de 150 ms; digitação nunca bloqueia acima de 50 ms |
| UI | `pytest-qt` para os gestos de §3 e a sincronização cursor ↔ tabuleiro |

### 6.2 Infraestrutura

`ruff` + `black` + `mypy` (modo gradual), `pytest`, GitHub Actions no push, cobertura mínima de 80 % em `core/`. **Primeiro commit do repositório antes de qualquer alteração.** Empacotamento com PyInstaller (executável Windows), já que o público-alvo não instala Python.

### 6.3 Métricas de sucesso

O programa está funcionando se:

- **tempo por partida de livro** cair abaixo de 8 minutos para uma partida comentada de ~40 lances;
- **zero** lances perdidos em silêncio (medido pelo corpus de regressão);
- **≥ 90 %** das correções automáticas propostas serem aceitas pelo usuário;
- round-trip perfeito em 100 % das exportações.
