# Especificação — PGN Live Editor v1.0

> Revisão complementar: 2026-09-10. Esta especificação preserva a fonte de
> verdade em texto bruto e adiciona o contrato operacional necessário para
> produção em escala: proveniência, estados de revisão, importação por etapas,
> políticas de exportação e critérios de observabilidade.

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
│  ├─ nag_table.py           # NOVO — tabela única de NAG e glifos (§2.8)
│  ├─ pgn_headers.py
│  ├─ game_splitter.py       # NOVO — várias partidas num documento
│  ├─ text_ops.py            # NOVO — gestos de edição estrutural
│  ├─ paste_cleanup.py       # NOVO — limpeza de texto de PDF (layout)
│  ├─ book_import.py         # NOVO — estrutura de livro impresso (§3.6)
│  ├─ layout.py              # NOVO — árvore indentada no texto bruto (§3.7)
│  ├─ outline.py             # NOVO — árvore de linhas de jogo (§4.7)
│  ├─ pgn_tree.py            # NOVO — árvore de lances (§4.8)
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
│  ├─ nag_bar.py             # NOVO — rodapé de símbolos (§4.6)
│  ├─ outline_panel.py       # NOVO — aba da árvore de linhas (§4.7)
│  ├─ pgn_tree_panel.py      # NOVO — árvore de lances no editor (§4.8)
│  ├─ book_import_dialog.py  # NOVO — prévia da conversão (§3.6)
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
- **Glifos da fonte de símbolos do livro → glifo canônico, antes do NFKC** (§2.8).
- Normalização Unicode NFKC, remoção de soft-hyphen (`­`) e zero-width.
- Glifos de peça `♔♕♖♗♘♙ ♚♛♜♝♞♟` → `KQRBN` / vazio.
- Variantes de traço (`–`, `—`, `−`) → `-`; aspas tipográficas.
- `0-0` → `O-O`, `0-0-0` → `O-O-O` **apenas** quando isolado por fronteira de token.
- Espaçamento de número de lance.

> A ordem do primeiro item é requisito, não detalhe de implementação: o NFKC
> converte `²` em `2`, `³` em `3` e `¹` em `1`. Depois dele, `Rad8³` chega ao
> tokenizador como `Rad83` e a avaliação já não existe para ser recuperada.

**Sai do pré-passe** e vai para `candidates.py` (§2.3):
- Mapeamento de idioma (`C→N`, `D→Q`, `T→R`, `L→B`, `S→N`, `A→B`).
- Substituições de OCR (`4J→N`, `tj→N`, `tD→N`, …).
- `ch → +`, `mate → #`.

> **Corrige D1, D2.** Nenhuma dessas transformações pode mais tocar prosa, porque só é tentada sobre um token que já foi classificado como candidato a lance e só é aceita se o resultado for legal na posição.

**Regex de número de lance** (corrige D5) — consumir *todos* os pontos consecutivos:

```python
MOVE_NUMBER_RE = r"\b(\d+)\s*((?:\.\s*)+)"
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
- **Sufixos de livro** (corrige D8): aceitar após o SAN, sem quebrar o token — `N` (novidade), `e.p.`, `‡`, `∆`, `⌓`, `→`, `↑`, `⇆`, `N!`, `!N`. Emitir anexado ao lance.
  - `∆`, `⌓`, `→`, `↑`, `⇆` são NAG e vêm da tabela da §2.8.
  - `N` (`$146`) é o caso perigoso: pô-lo no padrão de NAG faria todo `Nc6` do
    documento virar novidade teórica. Ele só casa **colado no fim de um lance**
    (espiada-atrás por caractere que termina um SAN) — `12.Nf4N` acha, `2.Nf3 Nc6`
    não. `N` solto continua sendo prosa: sem lance colado nele, não há o que deduzir.
  - `‡` e `†` são tipográficos, não têm NAG, e saem pela limpeza de colagem
    (§3.4, *remover marcas de nota de rodapé*), que os conta no relatório.
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
    severity: Literal["info", "warning", "error"]
    code: str  # 'UNRECOGNIZED_MOVE', 'AUTO_CORRECTED', …
    message: str
    raw_span: Tuple[int, int]  # posição no texto bruto → clique navega
    candidates: List[Candidate]
    actions: List[str]  # 'accept' | 'ignore' | 'learn' | 'to_comment'
```

Códigos mínimos: `UNRECOGNIZED_MOVE`, `AUTO_CORRECTED`, `AMBIGUOUS_MOVE`, `ILLEGAL_MOVE`, `UNCLOSED_BRACE`, `UNBALANCED_PAREN`, `ORPHAN_VARIATION`, `BAD_MOVE_NUMBER`, `MISSING_RESULT`, `INVALID_DATE`, `SUSPECT_BLUNDER`.

### 2.5 Múltiplas partidas (`game_splitter.py`) — corrige D6

```python
@dataclass
class GameSegment:
    index: int
    headers: Dict[str, str]
    body: str
    raw_offset: int  # deslocamento no texto bruto completo
    title: str  # "13. Smyslov – Rudakovsky, Moscou 1945"
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

### 2.8 `nag_table.py` — uma tabela, três consumidores

Três partes do programa precisavam da mesma informação e cada uma tinha a sua
lista: o **tokenizador** (quais glifos são NAG), o **renderizador** (glifo → `$n`
na exportação) e a **paleta da interface** (o que oferecer). As três estavam
incompletas, cada uma de um jeito. A tabela passa a ser única.

Cada entrada carrega: `code`, `glyph`, `name`, `group`, `black_code` (quando o
número depende do lado), `safe_glyph` e `aliases`.

**Cobertura mínima obrigatória:** `$1`–`$7`, `$10`, `$13`–`$19`, `$22`/`$23`,
`$32`/`$33`, `$36`/`$37`, `$40`/`$41`, `$44`/`$45`, `$132`/`$133`,
`$138`/`$139`, `$140`–`$143`, `$145`, `$146`.

Três regras que a tabela existe para não deixar esquecer:

1. **`+-` é `$18`, não `$16`.** `±` é "claramente melhor"; `+-` é "ganha". São
   linhas diferentes na chave de símbolos de todo livro. A forma ASCII de `±` é
   `+/-`. Trocar um pelo outro rebaixa a avaliação sem aviso — eram 191 delas só
   no capítulo de amostra.
2. **Símbolo com dois números escolhe o lado pelo tabuleiro.** Compensação,
   ataque, iniciativa e contrajogo são de quem **jogou** (`$44` / `$45`);
   zugzwang é de quem está **na vez** (`$22` / `$23`). A distinção está no campo
   `side_is_mover`.
3. **Nem todo NAG cabe num glifo.** Novidade (`$146`) é a letra `N` e comentário
   de editor (`$145`) é `RR`. Entram com `safe_glyph=False`: o tokenizador não os
   procura no texto e a interface insere a forma numérica. Um `N` solto no meio
   da notação seria lido como cavalo.

**Apelidos de fonte de livro.** A fonte de símbolos da Thinkers Publishing (e da
Quality Chess, e da Everyman) reaproveita codepoints latinos:

| No PDF | Canônico | NAG | | No PDF | Canônico | NAG |
|---|---|---|---|---|---|---|
| `²` | `⩲` | `$14` | | `‚` | `→` | `$40`/`$41` |
| `³` | `⩱` | `$15` | | `ƒ` | `↑` | `$36`/`$37` |
| `µ` | `∓` | `$17` | | `„` | `⇆` | `$132`/`$133` |
| `+–` | `+-` | `$18` | | `™` | `□` | `$7` |
| `–+` | `-+` | `$19` | | `‰` | `⟳` | `$32`/`$33` |
| `©` | `©` | `$44`/`$45` | | `ʘ` | `⨀` | `$22`/`$23` |

Duas exceções deliberadas, e as duas vieram de contra-exemplos reais:

- **`¹` só vale antes do lance.** Na chave da Thinkers ele é "melhor é"
  (`¹18.Rd1`); no resto da literatura é marca de nota de rodapé, e vem colada
  depois (`Nf3¹`). A posição resolve; escolher um dos dois destruiria o outro.
- **`…` não é `∆`.** A chave diz que é "com a ideia de", mas reticência
  tipográfica é o que quase todo PDF usa para lance das pretas (`7…h6`). O NFKC
  já a converte em `...`, que é o certo.

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
| Colar comentário antes do lance | `Ctrl+Alt+A` | a área de transferência vira `{...}` **antes do número** do lance escolhido |
| Colar comentário depois do lance | `Ctrl+Alt+D` | a área de transferência vira `{...}` **depois do lance e dos símbolos dele**, antes de as variantes abrirem |

**Colar comentário no lance escolhido.** O lance é o que está selecionado na
árvore do PGN (§4.8) — botão direito nela oferece os dois lados, com o nome do
lance no menu — ou o que está sob o cursor. Os dois lados não são o mesmo gesto
visto de dois ângulos: em `7.Qd2 ( 7.Be2 … ) 7...Be7` há mais de um ponto de
inserção entre `Qd2` e `Be7`, e é o que faz o parágrafo que fala de `7...Be7`
não acabar dentro da variante que o precede. Três regras:

- a prosa vira **uma linha** — um comentário não atravessa linha em branco, e
  uma página de PDF na área de transferência vem cheia delas;
- `{}` interno vira `()`, e texto copiado já entre chaves não aninha;
- depois do lance, o comentário que já existe ali é o do **próprio** lance:
  a prosa nova é juntada a ele, e não colada como um segundo `{}`. Antes do
  lance não há junção — ali o vizinho à esquerda é do lance anterior.

Um caso vale ser sabido de antemão, porque não é escolha do gesto: quando o
lance é seguido de uma variante que o **substitui**, a prosa entre os dois é
lida como introdução da variante — é o que o PGN da editora faz
(`7. Qd2 ( {Placing the queen on d2 …} 7. Be2 … )`) e o parser reproduz.
Para que ela fique com o lance, a variante precisa ter a introdução dela.

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

### 3.6 `book_import.py` — a estrutura do livro impresso

O `paste_cleanup` (§3.4) resolve o **layout** da página. Este resolve a
**estrutura**, que é o que sobra e o que custa tempo: descobrir onde começa e
onde termina cada variante.

Um livro de repertório moderno não escreve parênteses — escreve rótulos:

```
7...h6
A) 8.Be3 Ng4 [8...b5 9.f3 transposes ...] 9.0-0-0 Nxe3 10.Qxe3 Nc6
B) 8.Bh4? falls into an elementary trap: 8...Nxe4!µ
C) 8.Bxf6 Qxf6 9.0-0-0 Nc6
C1) 10.Nb3?! This has been the most popular move ... 10...b5 11.f4 Qd8
C2) 10.Nxc6 bxc6 11.f4 Qd8 12.Bc4 Be7=
```

E o mesmo trecho em PGN é:

```
7...h6 8.Be3 ( 8.Bh4 $2 8...Nxe4 $1 $17 )
( 8.Bxf6 Qxf6 9.0-0-0 Nc6 10.Nb3 $6 ( 10.Nxc6 bxc6 11.f4 Qd8 12.Bc4 Be7 $10 )
  10...b5 11.f4 Qd8 ) 8...Ng4 ( 8...b5 9.f3 ) 9.0-0-0 Nxe3 10.Qxe3 Nc6
```

Seis regras, cada uma desligável, com prévia e relatório:

| # | Regra | Por quê |
|---|---|---|
| 1 | **Rótulos → variantes aninhadas.** O primeiro irmão continua a linha; os demais viram `( … )` **logo depois do primeiro lance dele** | em PGN a variante é alternativa ao lance imediatamente anterior, não ao último |
| 2 | **`[ … ; … ]` → variantes irmãs** | é como o livro escreve alternativa embutida; `;` separa irmãs |
| 3 | **Prosa → `{comentário}`** | sem isto, metade do livro some do PGN exportado |
| 4 | **Mobiliário de página fora** — `Position after:`, cabeçalho corrente, número de página, `Show/Hide Solution` | não é notação nem comentário |
| 5 | **`□ 1.?` / `■ 1...?` abre partida** com separador e cabeçalho | são 176 exercícios num volume; sem isto seriam uma partida só |
| 6 | **Repetição de número de lance = alternativa** — `8.0-0-0` seguido de `8.f4!?` não é o nono lance | é como o livro marca uma linha lateral sem rótulo |

**Remontagem de parágrafo.** As linhas vêm quebradas pela largura da coluna
impressa, e a quebra carrega significado: `7...Be7` sozinho numa linha é um
lance da linha principal, enquanto uma linha de 98 caracteres continua na
seguinte. O critério é **a margem direita**, não o conteúdo. Duas guardas:
um rótulo sempre abre parágrafo, e colchete aberto nunca atravessa parágrafo.

**Citação vs. linha.** Um lance no meio de uma frase — "transposes to the
`6.Be3 e6` variation", "threatens both `Nxe6` and `Nd5`" — é assunto, não
continuação. Regra: até dois lances seguidos, cercados de prosa dos dois lados,
com a linha já começada naquele nível **ou** com mais de três palavras de prosa
antes, voltam a ser prosa. Errar para o lado do comentário preserva o texto
inteiro; errar para o lado do lance quebra a linha.

Tudo que o módulo decide sem certeza sai em `report.notes`, separado das
contagens — é a aplicação direta do princípio 3 da §0.

**Aceite:** a página do capítulo colada crua do PDF produz o **mesmo** conjunto
de lances, variantes e NAGs do PGN que a editora distribui, e todo o comentário
que o PGN não tem.

### 3.7 Disposição em árvore no texto bruto (`layout.py`)

A árvore da §4.7 mostra a estrutura **ao lado** do texto. Esta a põe **dentro**
dele, que é onde a edição acontece: uma variante por linha, recuada pela
profundidade, com o parêntese de fechar alinhado com o de abrir.

```
1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.Qd2
    ( 7.Be2 7...Be7 8.0-0 Qc7 )
7...Be7
    ( 7...h6 8.Be3
        ( 8.Bh4? 8...Nxe4!∓ )
        ( 8.Bxf6 Qxf6 9.0-0-0 Nc6 )
    8...Ng4 9.0-0-0 )
8.0-0-0
```

`Ctrl+T` dispõe, `Ctrl+Shift+T` volta ao movetext corrido. Os dois são gestos
explícitos, num único bloco de desfazer — o texto bruto é a fonte da verdade
(§0, princípio 1) e não se reformata sozinho.

Três garantias, e as três são testadas:

1. **Nada muda além de espaço em branco.** A transformação mexe só nos
   intervalos *entre* os átomos — comentário, lance, parêntese. Tirar todo o
   espaço do antes e do depois tem de dar exatamente a mesma coisa.
2. **Comentário é intocável**, com os espaços e as quebras que tiver dentro.
3. **É idempotente e reversível**: dispor duas vezes não muda nada, e voltar a
   corrido devolve o texto de partida.

**Com delimitador aberto o gesto se recusa a rodar** e aponta onde ele está. Um
`{` sem fechamento vale até o fim da linha (§2.2); juntar linhas em volta dele
muda o que o comentário engole, e não há leitura certa a adivinhar. O bloco de
cabeçalhos e os separadores de partida não são tocados.

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

Categorias: lance da linha principal · lance de variante · comentário · NAG · resultado · **corrigido automaticamente** (verde, sublinhado pontilhado) · **ambíguo** (amarelo) · **não reconhecido** (ondulado vermelho). O contraste entre "confirmado" e "corrigido" é requisito explícito do modo dupla visão.

**Cor por profundidade.** O span de realce carrega o **número** do nível, não um
"é variante ou não": com quatro níveis de aninhamento — o normal num livro de
repertório — saber apenas que um lance *está* numa variante não ajuda a achar
em qual. Cada nível tem um matiz próprio (`theme.DEPTH_COLORS`), e não o mesmo
azul clareando: tons do mesmo matiz deixam de se distinguir por volta do
terceiro, que é onde a pergunta começa a valer. Passando do fim da paleta a cor
se repete — o recuo do texto (§3.7) continua contando a profundidade exata.

Só a linha principal vai em negrito. E **corrigido, duvidoso e não reconhecido
mantêm a sua própria cor**, não a da profundidade: "o programa mexeu aqui" vale
mais do que "isto é uma sub-variante". Na dupla visão a cor de profundidade
some junto com o resto do que foi apenas confirmado.

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
| `Ctrl+Shift+L` | converter texto de livro | `F6` | mostrar/ocultar rodapé de símbolos |
| `F7` | árvore de variantes | `Ctrl+Shift+Enter` | converter e mandar (painel de PDF) |
| `Ctrl+T` | dispor em árvore no editor | `Ctrl+Shift+T` | voltar a texto corrido |
| `F5` | mostrar/ocultar a árvore do PGN | `Ctrl+Alt+A` | colar comentário antes do lance |
| `Ctrl+Alt+D` | colar comentário depois do lance | | |

### 4.6 Rodapé de símbolos

Barra fixa no pé do editor com **todos** os NAG da §2.8, agrupados como a chave
de símbolos de um livro: lance · avaliação · dinâmica · intenção. Clicar insere
no cursor; um interruptor `$n` troca a inserção do glifo (`±`) pelo código
(`$16`) para quem revisa um PGN já numerado.

O motivo de ser rodapé e não diálogo: anotar é o gesto mais repetido da
transcrição, e o custo não está em achar o símbolo — está em abrir e fechar a
janela. `F6` esconde a barra para quem não quer.

### 4.7 Árvore indentada de variantes

Segunda aba ao lado do preview de PGN. **Uma linha por linha de jogo**,
recolhível, indentada pela profundidade, com o rótulo na convenção do livro:
`A)`, `B)`, `C1)`, `B1.2)`. Colunas: variante e prévia · nº de lances · o lance
de onde ela se desprende. Clicar leva o cursor ao primeiro lance daquela linha
no texto bruto.

A correspondência com a página impressa é literal e deliberada: num ponto de
ramificação, o livro chama a **continuação** de `A)` e as alternativas de `B)`,
`C)`. Os rótulos aqui seguem o mesmo deslocamento — sem isso, todo rótulo sairia
um fora do que está impresso, que é pior do que não ter rótulo nenhum. O
contador corre pela linha inteira, e não por lance: duas ramificações na mesma
linha nunca geram dois `B)`.

*Copiar árvore* dá a mesma estrutura em texto indentado, para conferir ao lado
da página.

### 4.8 Árvore do PGN, dentro do editor (`pgn_tree.py`)

**Um item por lance**, com as variantes penduradas no lance de que são
alternativa, recolhíveis. É a vista em que variante dentro de variante se vê
sem ler nada, e ela mora **junto do texto bruto** — num divisor redimensionável
sob o editor, `F5` esconde — porque a pergunta que ela responde ("em que
variante eu estou?") aparece enquanto se digita, e olhar para o outro extremo
da janela a cada dúvida custa mais do que a resposta vale.

```
7. Qd2
  └ B) 7. Be2 7... Be7 8. O-O Qc7 …          ← variante recolhida, com prévia
        7. Be2                {Placing the queen on d2 …}
        7... Be7
7... Be7
  └ C) 7... h6 8. Be3 Ng4 9. O-O-O …
        8. Be3
          └ C2) 8. Bh4 8... Nxe4
          └ C3) 8. Bxf6 Qxf6 9. O-O-O …
```

Quatro regras:

1. **A variante aparece sob o lance de que é alternativa**, e não sob o lance de
   onde a posição parte. A AST guarda `7.Qd2 ( 7.Be2 … )` em `6...e6` — a
   posição de onde a variante sai — mas quem abre a árvore atrás da alternativa
   a `7.Qd2` olha debaixo de `7.Qd2`. É o que `variation_anchor_after` resolve.
2. **O rótulo é o mesmo da §4.7 e do livro** (`B`, `C1`, `B1.2`). Duas árvores
   da mesma partida chamando a mesma variante por nomes diferentes seriam
   piores do que uma árvore só.
3. **A cor é a mesma do realce do texto** (§4.3), inclusive as de corrigido,
   duvidoso e não reconhecido.
4. **Número de lance em todos os itens.** Aqui cada lance é uma linha, e `Nc6`
   sozinho numa linha não diz onde está — o oposto da prévia de uma variante
   recolhida, que é uma corrida horizontal e onde o número só atrapalharia.

Sincronia nos dois sentidos, com trava contra laço: mover o cursor seleciona o
lance, clicar no lance leva o cursor até ele.

#### Disposição

Duas colunas: **Lance** (número, lance e símbolo, na cor do realce) e
**Comentário** (a prosa do lance, em tom apagado — ela acompanha o lance, não
disputa com ele). A dica de cada linha diz o que está **antes** e o que está
**depois** do lance, que é a informação de que os gestos de colar comentário
precisam. A linha de uma variante é um rótulo, não um lance: vai em itálico, com
a prévia no `Lance` e, no `Comentário`, a prosa com que a variante abre — o
único comentário que uma linha recolhida pode mostrar sem mentir. O recuo por
nível é menor que o padrão do Qt de propósito: um livro de repertório chega a
quatro ou cinco níveis, e com o recuo padrão o quinto começa onde a coluna de
comentário devia estar.

**A árvore nasce de novo a cada reanálise, mas não se comporta como se
nascesse.** Reconstruir é mais barato do que descobrir o que mudou no texto, só
que uma árvore que volta ao estado inicial a cada tecla é inútil: recolhe a
variante que se estava lendo e rola para outro lugar no meio de uma frase. O que
o usuário abriu, fechou, selecionou e onde deixou a rolagem atravessa a
reconstrução. Ramo que nasceu agora — porque a variante acabou de ser digitada —
não tem estado guardado, e **não ter estado não é o mesmo que ter sido
fechado**: ele abre pela regra do padrão.

#### As três vistas, e para que serve cada uma

| Vista | Pergunta que responde | Onde |
|---|---|---|
| Preview PGN | como a partida **sai** | aba, ao lado do tabuleiro |
| Árvore de variantes (§4.7) | quantas **linhas** existem e onde cada uma começa | aba, ao lado do tabuleiro |
| Árvore do PGN (§4.8) | onde cada **lance** está | sob o editor |
| Recuo no texto (§3.7) | a mesma estrutura, **editável** | no próprio texto bruto |

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

**`PGN_LIVE_EDITOR_HOME`** desvia o diretório de dados inteiro. Serve a três
coisas, nesta ordem de importância: **teste** (sem isso a suíte lê e escreve o
perfil real de quem a roda, e uma preferência gravada num dia derruba um teste
no outro — só naquela máquina), instalação portátil, e diagnóstico com perfil
limpo.

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

**`Ctrl+Shift+Enter` — converter e mandar.** Passa a seleção pelo conversor de
livro (§3.6) com prévia antes de inserir. É o caminho normal para uma página de
repertório; `Ctrl+Enter` continua mandando cru, para um trecho solto.

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

---

## 7. Especificação v1.1 — confiança, origem e operação

### 7.1 Modelo de dados de reconhecimento

O AST atual descreve o que foi reconhecido; para revisão de OCR isso não é
suficiente. Cada token reconhecível ou suspeito deve ter um registro estável:

```text
RecognitionRecord
├─ id estável e analysis_version
├─ source: game_id, página, coluna, offsets bruto e normalizado
├─ raw_text e normalized_text
├─ interpretation: SAN/NAG/comentário/estrutura/resultado
├─ candidates: alternativas e motivos de legalidade/contexto
├─ confidence: confirmed | high | medium | low | rejected
├─ action: pending | accepted | rejected | commented | ignored
└─ rule_id / dictionary_layer / timestamp da decisão
```

Spans são posições no texto bruto e nunca devem ser usados como identidade
permanente: após edição, a identidade deve ser reconciliada pelo `source_id`,
contexto e conteúdo, ou marcada como obsoleta. Resultado de uma análise antiga
não pode ser aplicado sobre texto cujo hash mudou.

### 7.2 Contrato de segurança da interpretação

- O parser não descarta tokens com aparência de lance, número, NAG ou resultado;
  cria `UNRECOGNIZED_CANDIDATE` ou `AMBIGUOUS_CANDIDATE`.
- Correção automática só pode ser aplicada quando a regra é explícita, o lance
  é legal na posição correta e a confiança supera o limiar configurado.
- Levenshtein é gerador de candidatos, nunca autorização isolada; legalidade,
  turno, número, idioma, posição e vizinhança devem pontuar a decisão.
- A UI deve mostrar original → interpretação e permitir aceitar, escolher outra,
  rejeitar, comentar ou ignorar a ocorrência.
- “Ignorar” silencia o alerta atual e fica persistido no projeto; não altera o
  texto nem autoriza a mesma regra em ocorrências futuras.

### 7.3 Importação de PDF em etapas

Uma importação deve manter artefatos intermediários, mesmo que a interface
ofereça um botão único:

`PDF → extração → regiões/colunas → limpeza → estrutura do livro → partidas →
reconhecimento chess → revisão → exportação`

Cada etapa precisa de prévia, contagem e opção de desfazer. A camada PDF deve
registrar arquivo, hash, página, região, texto extraído e método (`text-layer`
ou `OCR`). Se não houver camada de texto, o produto deve mostrar “OCR
necessário” e não converter a imagem em uma sequência vazia silenciosamente.

O OCR de diagramas deve executar em processo nativo isolado do processo Qt da
interface. Isso evita conflitos de DLL/OpenMP entre PyQt6, OpenCV e Torch no
Windows; o contrato entre os processos é JSON validado, contendo FEN,
confianças, proveniência e estado de revisão.

### 7.4 Estado de partida e isolamento

Cada segmento de partida deve possuir `game_id`, intervalo no documento, FEN
inicial efetivo, cabeçalhos, estado de conclusão e contagens independentes.
Editar uma partida invalida apenas seu parse/preview, mantendo resumos e
decisões das outras. Mover, dividir ou unir segmentos deve ser uma operação
transacional e registrar a alteração no histórico do projeto.

### 7.5 Política de exportação

O diálogo de exportação deve permitir:

1. exportar somente partidas selecionadas ou o livro inteiro;
2. incluir todos os comentários, somente comentários aceitos ou nenhum;
3. bloquear pendências, exportar com confirmação, ou emitir PGN de quarentena;
4. validar round-trip, árvore completa, FEN, cabeçalhos, resultados e NAGs;
5. gerar relatório e backup quando o destino já existir.

O `.json` estrutural continua sendo a saída de auditoria. O PGN é uma saída
derivada e nunca deve ser tratado como substituto do texto bruto e dos estados
de revisão.

### 7.6 Interação e produtividade

O ponto ativo deve ser único e compartilhado entre editor, árvore, tabuleiro e
fonte. Toda ação estrutural deve:

- operar em uma transação de undo;
- preservar seleção/cursor tanto quanto possível;
- mostrar uma mensagem curta com o efeito e a contagem;
- funcionar por teclado e menu de contexto;
- recusar-se com explicação quando a estrutura estiver ambígua.

Adicionar modo de inserção de lance legal, revisão somente dos itens pendentes,
ações em lote por regra e retorno direto à página de origem. O programa deve
preferir deixar prosa intocada a transformar uma citação ambígua em lance.

### 7.7 Performance e concorrência

O orçamento é <50 ms de resposta da edição, <150 ms para reparse de 100 KB e
zero congelamento durante extração, motor ou exportação. Parse, PDF, motor e
exportação devem ser canceláveis. Cada resultado assina `request_id` e hash do
texto; resultados atrasados são descartados. Preview HTML e realce devem ser
atualizados apenas quando a versão correspondente estiver vigente.

### 7.8 QA mínimo para release

Além dos testes atuais, cada release deve incluir:

- corpus por idioma, fonte PDF, coluna, página escaneada, variantes profundas,
  FEN e comentários multilinha;
- testes de propriedade para spans, idempotência, undo e invariantes de árvore;
- testes de mutação/“lance parecido mas errado” para garantir que o alerta não
  desaparece;
- teste de carga com livro grande e teste de cancelamento;
- round-trip com pelo menos dois leitores externos ou fixtures equivalentes;
- teste de instalação em máquina limpa e migração de versões `.plez`;
- execução documentada via `uv sync` antes de `pytest`; dependências ausentes
  devem produzir diagnóstico de ambiente, não ser confundidas com falha do
  produto.

### 7.9 Decisões de produto que ficam fixas

- Texto bruto é a fonte da verdade; AST e PGN são derivados.
- Nenhuma heurística silenciosa prevalece sobre evidência de origem.
- Legalidade valida uma interpretação, mas não prova que ela é o lance impresso.
- O motor é assistente de suspeita, nunca autoridade para alterar o livro.
- A unidade de trabalho é a partida/segmento, mas a unidade de auditoria é o
  token com span e origem.
- “Pronto” significa sem pendências críticas ou com uma decisão explícita de
  exportá-las em quarentena.
