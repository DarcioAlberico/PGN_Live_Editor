# Especificação v3 — PGN Live Editor

> Data: 2026-11-09. Complementa a [SPEC.md](SPEC.md) (v1.0 e v1.1, §0–§7) — o que
> está lá continua valendo; esta especificação acrescenta os **contratos** que a
> [AUDITORIA_2026-11.md](AUDITORIA_2026-11.md) mostrou faltar, e é a referência
> técnica do [ROADMAP_V3.md](ROADMAP_V3.md).
>
> Convenção: **DEVE** = obrigatório e testado; **NÃO DEVE** = proibido e testado;
> **PODE** = permitido. Cada contrato termina com os testes que o fixam.

## 0. Princípios que viram regra

| Princípio (ub-mode) | Regra neste projeto |
|---|---|
| Uma decisão vive num lugar | Um scanner de delimitadores (§5.1), uma extensão de lance (§5.2), uma política de revisão (§6.4), um gravador durável (§2.1), um host de worker (§4.3), um reset de sessão (§10.1). |
| Checar os dados onde entram; dentro, confiar | Arquivos, resultados de worker e saída de subprocesso são **fronteiras** (§2.2, §4.1). Lá dentro, só tipos já validados. |
| Tornar o estado ilegal impossível | `ReviewAction` enum, `RecognitionRecord` frozen, `Variation`, `MoveExtent`, `DocumentRevision` (§4–§6). |
| Idempotência | Normalizar duas vezes = uma vez; salvar duas vezes = um arquivo igual; recuperar depois de crash no meio = mesmo estado (§2, §5.6). |
| Um escritor para estado compartilhado | Só a thread da UI muda o documento e o projeto; workers devolvem valores imutáveis carimbados (§4). |
| Corrigir a causa | `except` que só esconde é proibido (§3.2). |

### 0.1 Glossário

| Termo | Definição |
|---|---|
| **Texto bruto** | O conteúdo do editor. Fonte da verdade (SPEC 7.9). |
| **Revisão** (`DocumentRevision`) | `(generation, text_hash, config_hash)` de um instante do documento. |
| **Configuração de reconhecimento** | Idioma, notação, dicionário efetivo (três camadas), tokens ignorados, FEN inicial. Entra no `config_hash`. |
| **Span** | Par `[start, end)` em caracteres do texto bruto, válido **apenas** na revisão em que foi calculado. |
| **Extensão de lance** (`MoveExtent`) | Spans do número, da SAN, do fim dos NAGs e do comentário próprio de um lance. |
| **Registro de reconhecimento** | Decisão sobre um token: proposta, ação humana, origem. Sobrevive a reanálises (§6). |
| **Sessão** | Tudo que pertence a um projeto aberto: projeto, dicionário, ignorados, PDF, caches de OCR, achados do motor, workers, recuperação. |
| **Citação** | Trecho de prosa que menciona lances de outra linha ou partida (`Compare 1. d4 d5…`). |
| **Proveniência** | De onde veio cada parte de um dado reconhecido (texto do PDF, símbolo, padrão, modelo, inferência). |
| **Órfão** | Registro antigo que a reconciliação não conseguiu alinhar a nenhum token novo. É mostrado, nunca descartado em silêncio. |

---

## 1. Arquitetura de destino (delta sobre SPEC §1)

```
pgn_live_editor/
  core/                      puro, sem Qt, sem I/O
    delimiters.py            §5.1  scanner único
    tree_walk.py             §R-604 caminhada única da árvore
    side_to_move.py          §6.5  vocabulário compartilhado
    revision.py              §4.1  DocumentRevision, SpanRef
    review/                  §6    RecognitionRecord, ReviewAction, política, reconciliação
  services/
    durable_io.py            §2.1  gravação durável
    schemas.py               §2.2  carregadores com esquema
    worker_host.py           §4.3  host de worker comum
    logging_setup.py         §3.1  excepthook + log
  ui/
    controllers/             §10.2 session, review, book, pdf_diagram, edit, view, engine, report
    main_window.py           só composição
  __main__.py                §4.4  despacho de subcomandos (--worker, --diagnostico)
```

Regra de dependência: `ui → services → core`; `core` não importa nada de `services`
nem de `ui`; `chess_diagram_ocr` só é importado por `services/diagram_ocr*.py`.
Um teste de arquitetura (`tests/test_architecture.py`) confere os imports.

---

## 2. Persistência

### 2.1 Gravação durável (`services/durable_io.py`) — R-101

```python
def write_durable(path: Path, data: bytes, *, backups: int = 2) -> None: ...
def write_durable_text(path: Path, text: str, *, backups: int = 2) -> None: ...  # UTF-8, newline="\n"
```

Algoritmo, nesta ordem:

1. Gravar em `path.with_name(f".{path.name}.{uuid4().hex}.tmp")` (mesmo diretório,
   mesmo volume), `flush()`, `os.fsync()`.
2. Se `path` existe: **copiar** `path` → `path.bak.new` e `fsync`.
3. `os.replace(tmp, path)`, com até 5 tentativas espaçadas de 100 ms em
   `PermissionError` (antivírus, indexador, OneDrive).
4. Só então rotacionar: `.bak.(n-1)` → `.bak.n` … `.bak.new` → `.bak`.
5. Em qualquer falha: apagar `tmp` e `.bak.new`; `path` e os `.bak` existentes
   **NÃO DEVEM** ter mudado; levantar `DurableWriteError(OSError)` com o caminho e
   a causa.

Usuários obrigatórios: projeto, export PGN/JSON/TXT/HTML, dicionário global,
`engine.json`, preferências, snapshot de recuperação, recentes.

**Testes:** rename final falhando (monkeypatch) → original intacto, `.bak` intactos,
nenhum `.tmp`; destino aberto por outro processo no Windows → `DurableWriteError`
sem rotação; dois saves seguidos idênticos → bytes idênticos (idempotência);
crash simulado entre os passos 2 e 3 → reabrir encontra a versão anterior inteira.

### 2.2 Carregadores com esquema (`services/schemas.py`) — R-103

- Cada arquivo lido tem um esquema declarativo: `campo → (tipo, coerção, padrão)`.
- Leitura **campo a campo**: um campo inválido volta ao padrão *dele* e gera um
  `LoadWarning(campo, valor, motivo)`; os outros campos sobrevivem.
- Estrutura impossível (raiz não é objeto, versão desconhecida maior que a atual)
  → `ProjectFormatError(ValueError)`. **Nenhum** outro tipo de exceção atravessa a
  fronteira de carga.
- Migrações explícitas por `version`: `migrate_1_to_2(payload) -> payload`, puras e
  testadas com um arquivo fixo de cada versão em `tests/fixtures/plez/`.
- Avisos de carga aparecem na barra de status e no log; nunca bloqueiam a abertura.

**Testes:** paramétrico — cada campo de `.plez/meta.json`, `state.json`,
`preferences.json`, `engine.json`, dicionário e snapshot trocado por `null`, `[]`,
`"x"`, `5`, `{}` → nenhum `TypeError/AttributeError/KeyError` escapa; os demais
campos mantêm o valor.

### 2.3 Recuperação de sessão v2 — R-102

```json
{
  "format": "pgn-live-editor/recovery", "version": 2,
  "instance": "<uuid4>", "pid": 1234, "saved_at": "2026-11-09T12:00:00Z",
  "origin_path": "C:/…/livro.plez" | null,
  "project": { …payload completo, igual ao .plez… },
  "editor": { "cursor": 812, "active_segment": 3 }
}
```

- Um arquivo por instância: `recovery/<instance>.json`, com `recovery/<instance>.lock`
  mantido aberto enquanto a instância vive.
- Na abertura, só são oferecidos snapshots **sem** lock vivo (instância morta).
- Restaurar: se `origin_path` existe, carregar a origem, comparar e sobrepor o
  `project` do snapshot (o snapshot vence campo a campo); o projeto fica *sujo*.
  Se não existe, abrir como "Sem título" com o payload completo.
- Fechar normalmente apaga **só** o snapshot da própria instância.

**Testes:** crash → recuperar → salvar preserva cabeçalhos, dicionário, partidas
concluídas, registros de diagrama e decisões; duas instâncias → a segunda não vê
nem apaga o snapshot da primeira enquanto esta vive.

---

## 3. Erros e observabilidade

### 3.1 Excepthook e log — R-005

- `__main__.main()` instala, antes de criar a `QApplication`:
  `sys.excepthook`, `threading.excepthook` e um `logging` com
  `RotatingFileHandler(app_data_dir()/"logs"/"pgn-live-editor.log", 1 MB × 5)`.
- O hook: registra o traceback; tenta gravar o snapshot de recuperação (§2.3);
  mostra um diálogo não modal "Ocorreu um erro — o trabalho foi guardado", com
  botão "Abrir pasta de logs"; **NÃO DEVE** encerrar o processo.
- `--diagnostico` **não** inclui o log, só o caminho dele: a mensagem de uma
  exceção pode carregar texto do documento, e o diagnóstico é feito para ser
  enviado a terceiros (decisão de 2026-11-09, ao implementar a R-005).

### 3.2 Política de exceções

| Situação | Obrigatório |
|---|---|
| Fronteira de I/O (arquivo, subprocesso, motor, rede) | Capturar a família específica; converter para um erro do domínio com mensagem para o usuário; registrar no log. |
| Dentro de `core/` | Não capturar nada que não saiba tratar. Entrada inválida vira `Issue`, não exceção. |
| Slot Qt / callback de worker | O `WorkerHost` (§4.3) captura, registra e entrega `WorkerFailed`; o slot nunca vê exceção crua. |
| `except Exception:` | Só com `logger.exception(...)` **e** um efeito visível (mensagem, alerta, status). `pass` ou `return None` sozinhos **NÃO DEVEM** existir — um teste de lint (`ruff` `BLE001` + `S110`) aplica. |

### 3.3 Métricas — R-404

- Medições acumuladas em memória (`deque(maxlen=10_000)`), gravadas em **append**
  por uma thread de fundo a cada 10 s e no fechamento.
- Rotação por tamanho (512 KB × 3). Nenhuma leitura do arquivo no caminho de gravação.
- Falha de métrica **NÃO DEVE** afetar a operação medida: o `finally` de `measure()`
  não levanta.

---

## 4. Concorrência e estado obsoleto

### 4.1 `DocumentRevision` — R-301

```python
@dataclass(frozen=True)
class DocumentRevision:
    generation: int        # incrementa a cada edição, configure(), parse_now()
    text_hash: str         # blake2b(raw_text)[:16]
    config_hash: str       # blake2b(json canônico da configuração de reconhecimento)[:16]
```

- Todo pedido a um worker leva a revisão de origem; todo resultado volta com ela.
- O receptor (sempre na thread da UI) aplica **somente** se
  `result.revision == current_revision()`. Caso contrário descarta e, se ainda
  for útil, pede de novo.
- `parse_now()` síncrono também incrementa `generation` — o resultado assíncrono
  anterior fica automaticamente obsoleto.

### 4.2 `SpanRef` — R-302

```python
@dataclass(frozen=True)
class SpanRef:
    revision: DocumentRevision
    start: int
    end: int
    expected: str          # texto bruto naquele span, na revisão de origem
```

Antes de qualquer edição a partir de um alerta, registro ou nó da árvore:

1. Se `ref.revision == current` → aplicar.
2. Senão, se `text[start:end] == expected` → aplicar (o trecho não mudou).
3. Senão, reanalisar e relocalizar pelo `RecognitionRecord` (§6.2).
4. Se não relocalizar, **não** editar; mostrar "o trecho mudou desde a análise".

### 4.3 `WorkerHost` — R-303

Contrato único para parse, motor, OCR de diagrama, OCR do PDF inteiro e OCR de página:

- Uma thread por host, criada sob demanda; um pedido em execução, **no máximo um**
  na fila (o novo substitui o que esperava).
- `cancel()`: marca o token de cancelamento; se o trabalho é subprocesso, `terminate()`
  e, após 2 s, `kill()`. O resultado de um pedido cancelado nunca é emitido.
- `busy` é derivado: `current_request_id is not None`; só o resultado do pedido
  **corrente** o zera.
- `shutdown(timeout)`: cancela, espera; se a thread não terminou, ela é **mantida**
  (referência guardada até `aboutToQuit`) — `deleteLater` numa thread viva
  **NÃO DEVE** acontecer.
- Exceções do trabalho viram `WorkerFailed(request_id, revision, message, traceback)`.

**Testes:** fechar a janela no meio de cada tipo de trabalho, 50 vezes → sem crash;
4 pedidos de varredura em sequência rápida → 1 executado + 1 no máximo na fila;
exceção injetada no trabalho → `WorkerFailed`, host continua aceitando pedidos.

### 4.4 Subprocessos e build congelado — R-304

- `python -m pgn_live_editor --worker diagram-ocr|full-pdf <args>` e, no exe,
  `"PGN Live Editor.exe" --worker …` despacham **antes** de qualquer import de Qt.
- Comando do filho: `[sys.executable, "--worker", …]` quando `sys.frozen`, senão
  `[sys.executable, "-m", "pgn_live_editor", "--worker", …]`.
- Comunicação por arquivo de resultado (JSONL, §R-109) ou `stdout` **lido** por thread
  própria; `stderr` mantido (últimos 4 KB) e incluído em `WorkerFailed.message`.
- No Windows, `creationflags=CREATE_NO_WINDOW`.

---

## 5. Núcleo de reconhecimento (delta sobre SPEC §2)

### 5.1 Scanner de delimitadores único (`core/delimiters.py`) — R-201

```python
@dataclass(frozen=True)
class Region:
    kind: Literal["comment", "variation", "command", "header"]
    start: int; end: int          # end exclusivo; end == len(text) se não fechado
    closed: bool
    depth: int

def scan(text: str) -> DelimiterIndex: ...
class DelimiterIndex:
    def region_at(self, pos: int) -> Region | None
    def enclosing(self, pos: int, kind: str) -> Region | None
    def scope_end(self, pos: int) -> int
    def unbalanced(self) -> list[Region]
```

- As regras de comentário são **as de `COMMENT_RE`/`comment_span_end`** (inclusive
  comentário multilinha e o limite de chave não fechada à linha — D7/D15).
- `[%cal …]`, `[%csl …]` e `[D]` dentro de comentário são parte do comentário.
- Idempotente e O(n). É a única função do projeto que decide "isto está dentro de
  um comentário/variante".

**Testes:** os quatro casos de N-07 e o L-16; propriedade: para todo texto,
`scan` concorda com `COMMENT_RE` sobre onde começa e termina cada comentário.

### 5.2 `MoveExtent` — R-202

```python
@dataclass(frozen=True)
class MoveExtent:
    number: tuple[int, int] | None    # "12." ou "12..."
    san: tuple[int, int]
    nag_end: int                      # fim do último NAG/glifo colado ou separado
    own_comment: tuple[int, int] | None  # comentário imediatamente após nag_end
    @property
    def end(self) -> int: ...         # own_comment[1] se houver, senão nag_end
```

Regras dos gestos (todos via `MoveExtent`):

| Gesto | Região reescrita |
|---|---|
| Renumerar | **só** `number` |
| Promover/rebaixar variante com âncora | a partir de `anchor.end` (NAG e comentário ficam com o dono) |
| Parágrafo → comentário | inserção em `nag_end` |
| Anotações (`[%csl]`/`[%cal]`) | `own_comment`, criado em `nag_end` se não existir |

### 5.3 Acumulação de comentários no parser — R-203

- Uma lista `parts: list[CommentPart]` por linha e um índice de fronteira gravado
  em cada lance e em cada abertura de variante.
- "Comentários depois do lance X" = `parts[boundary(X):boundary(next)]`, unidos
  **uma vez** na renderização. Não existe segunda lista.

### 5.4 Citação (referência retroativa) — R-204

Ao encontrar um número de lance menor que o esperado:

1. Começa uma coleta de citação.
2. A coleta **termina** no primeiro destes: número de lance que retoma a sequência
   esperada (`n == esperado` ou `n == esperado+1` com cor coerente); token que é
   lance legal no tabuleiro **corrente** e vem logo após um número que retoma a
   sequência; `)`; resultado; fim do texto.
3. O trecho coletado vira comentário e gera `Issue(CITATION_COLLECTED, info)` com o
   span, para o usuário conferir.
4. O corpo coletado passa pela **mesma** função de saneamento usada por todo
   comentário gerado (`text_ops._sanitize_comment_body`, promovida a
   `core/delimiters.sanitize_comment_body`): `{` e `}` internos nunca ficam crus.
   Teste: `cf 1. d4 x }y` gera um comentário balanceado.

### 5.5 Histórico nas variantes e notação descritiva — R-207

- **Causa raiz:** o parser salva e restaura a posição como FEN (`set_board_fen`) ao
  abrir e fechar variante. O FEN preserva roque e en passant (verificado nesta
  auditoria), mas zera `move_stack` — e a descritiva usa o histórico para saber qual
  peça é "do rei" e qual foi o último lance.
- **Contrato:** a pilha de variantes guarda `board.copy(stack=True)`; ao fechar a
  variante, o tabuleiro restaurado é essa cópia. Nenhum consumidor de `core/` lê
  posição a partir de FEN intermediário. (Alternativa rejeitada: um
  `DescriptiveContext` paralelo — seria uma segunda fonte de verdade sobre a mesma
  história.)
- Limpeza do token descritivo numa função: `split_token(raw) -> (core, check_kind,
  en_passant)`; a guarda de caixa roda sobre `core`; formas mistas aceitas: `Kt`,
  `ch`, `dis.ch`, `e.p.`.
- **Teste:** `7.KN-B3` resolve para o mesmo lance com e sem `(6...P-K3)` antes;
  `PxP` mantém a preferência de recaptura depois de uma variante.

### 5.6 Normalizador — R-006 / R-402

- Regiões protegidas (comentário, comando, cabeçalho) calculadas **uma vez** com o
  scanner §5.1 e passadas às passadas.
- Nenhuma regex sobre prefixo ou sufixo não limitado; olhar para trás no máximo 32
  caracteres.
- Mapa pré-NFKC: `½`→`1/2`, `⁄`→`/`; o tokenizador aceita `1/2-1/2` como resultado.
- Aliases contextuais (`„ ‚ ƒ ‰`): só colados a um fim de lance.
- **Invariante:** `normalize(normalize(t)) == normalize(t)` e o mapa de spans é
  monotônico (teste de propriedade).

### 5.7 `Variation` e anchor — R-213

```python
@dataclass
class Variation:
    moves: list[MoveNode]
    anchor_after: bool            # padrão ÚNICO: False (variante alternativa ao lance)
    span: tuple[int, int]
```

Substitui as três listas paralelas do `MoveNode`. Os quatro lugares que hoje
assumem padrões diferentes (`move_tree.py:65,152`, `text_ops.py:465`,
`pgn_tree.py:130`) passam a ler o campo.

### 5.8 Alertas novos

| Código | Severidade | Quando |
|---|---|---|
| `CITATION_COLLECTED` | info | §5.4 |
| `DANGLING_NAG` | warning | NAG sem lance anterior na linha |
| `UNCLOSED_VARIATION` | error | `(` sem `)` até o fim do texto |
| `ORPHAN_DECISION` | warning | decisão humana sem token correspondente após reconciliação (§6.2) |
| `PLAYER_ERROR_SHOWN` | info | o motor vê perda grande mas o livro responde com a refutação (R-508) |

---

## 6. Revisão e proveniência (delta sobre SPEC §7.1–7.3)

### 6.1 Registro tipado — R-302a

```python
class ReviewAction(Enum):
    PENDING = "pending"; ACCEPTED = "accepted"; REJECTED = "rejected"
    COMMENTED = "commented"; IGNORED = "ignored"

@dataclass(frozen=True)
class RecognitionRecord:
    token_id: str                     # estável, ver §6.2
    raw_text: str
    proposal: str | None
    action: ReviewAction
    decision: str | None              # obrigatório se ACCEPTED com texto diferente
    comment_text: str | None          # existe sse action == COMMENTED
    source: Provenance
    @property
    def altered(self) -> bool: return self.decision not in (None, self.raw_text)
```

Construtores validam as combinações; dicionários crus só em `to_json/from_json`.

### 6.2 Reconciliação por alinhamento — R-302b

- Entrada: sequência antiga de `(token_id, tipo, raw_text)` e a nova.
- `SequenceMatcher(None, antigos, novos, autojunk=False)` sobre `(tipo, raw_text)`;
  blocos `equal` transferem a decisão um a um; `replace` de tamanho igual transfere
  por posição **só** se o tipo coincide; o resto vira órfão (`ORPHAN_DECISION`).
- Uma decisão vai para no máximo um token; um token recebe no máximo uma decisão.
- Complexidade aceitável: O(n·m) só no trecho alterado (os prefixos e sufixos
  idênticos são cortados antes).

### 6.3 `DiagramProvenance` — R-504

```python
@dataclass(frozen=True)
class DiagramProvenance:
    fen: str                                   # o FEN canônico do serviço — único
    side_to_move: Literal["w","b"]
    side_source: Literal["text","symbol","pattern","legality","default"]
    side_conflicting: bool
    castling: str
    castling_source: Literal["text","inferred","none"]
    repaired_squares: tuple[str, ...]
    square_confidence: Mapping[str, float]
    model_sha256: str
    model_arch: str
    orientation_margin: float                  # com sinal
```

O adaptador **NÃO DEVE** reconstruir FEN. Roque inferido só entra no FEN depois
de confirmado pelo usuário; até lá o FEN exportado usa `-` e a revisão mostra a
inferência.

### 6.4 Política de revisão — R-505

`needs_review(p: DiagramProvenance, legality: Legality, confidence: float) -> list[Reason]`,
pura, uma linha por regra:

| Regra | Razão |
|---|---|
| `confidence < ACCEPT_MIN_CONFIDENCE` (hoje 0,80 em `chess_diagram_ocr/config.py:47`) | `LOW_CONFIDENCE` |
| posição ilegal / rei ausente / dois reis em xeque | `ILLEGAL` / `MISSING_KING` / `IMPOSSIBLE` |
| `orientation_margin < ORIENTATION_MIN_MARGIN` (inclui margem negativa; valor inicial 0,10, a calibrar com o corpus de diagramas antes de fixar) | `AMBIGUOUS_ORIENTATION` |
| `side_source == "default"` ou `side_conflicting` | `SIDE_UNKNOWN` |
| `castling_source == "inferred"` | `CASTLING_INFERRED` |
| `repaired_squares` não vazio | `REPAIRED` |

`needs_review` é verdadeiro sse a lista não é vazia; as razões aparecem na fila.

### 6.5 Vocabulário de "quem joga" — R-506

- `core/side_to_move.py` exporta os padrões por idioma (PT/EN/ES/DE/FR) e os
  símbolos; usado por `book_import` e `chess_diagram_ocr/pdf_text`.
- Precedência: texto explícito ("Black to move", "Juegan negras") > símbolo em
  formato de exercício ancorado (`^[□■]\s*1\s*\.{1,3}\s*\?$`) > padrão > legalidade
  > padrão-brancas (`default`, que **sempre** pede revisão).
- Teste cruzado: nenhum glifo de `nag_table` decide lado fora do formato ancorado.

---

## 7. Colunas de PDF — R-801

1. Largura da página = `page.rect.width` (não o máximo dos blocos).
2. Blocos com largura ≥ 60 % da página são **faixas** (títulos, diagramas centrados):
   separados e usados como cortes verticais.
3. Entre cortes, agrupar blocos por **sobreposição horizontal**; dois grupos são
   colunas distintas se a calha entre eles ≥ `max(8 pt, 2 % da largura)`.
4. Ordem de leitura: para cada faixa vertical entre cortes, colunas da esquerda para
   a direita, blocos de cima para baixo; depois o corte; repetir.
5. Sem PyMuPDF o botão fica desabilitado com a dica "instale o extra diagram-ocr".

**Testes:** A4 (595 pt) com colunas 40–287 e 308–555 → 2 colunas na ordem
`L1 L2 R1 R2`; título de largura total no topo → título, depois as colunas;
página de uma coluna → 1 coluna.

---

## 8. Orçamentos de desempenho — R-401

| Operação | Entrada | Orçamento (máquina de referência) |
|---|---|---|
| `normalize`+`tokenize`+`parse` | 1 MB descritiva EN | ≤ 3 s |
| idem | 1 MB SAN com 2 000 variantes | ≤ 3 s |
| Trabalho na thread da UI por tecla | documento de 1 MB | p95 ≤ 16 ms, máx ≤ 50 ms |
| Abrir projeto | `.plez` de 1 MB | ≤ 2 s até editar |
| Gravação de métrica | qualquer | ≤ 1 ms na thread chamadora |
| Abrir a janela | com ou sem extras | ≤ 2 s (sem importar OCR) |

O benchmark roda no CI marcado `slow`; falha se passar de 2× o orçamento (margem
para máquinas de CI), e registra o tempo para acompanhar tendência.

---

## 9. Portões de qualidade

| Portão | Alvo | Onde |
|---|---|---|
| `ruff check` + `ruff format --check` | limpo (com `BLE001`, `S110`) | CI, bloqueante |
| `mypy` | `core/` e `services/` limpos; `ui/` com contador decrescente | CI, bloqueante para core/services |
| Cobertura | `core/` ≥ 90 %; pacote ≥ 75 %; `chess_diagram_ocr/` ≥ 60 % no job com extras | CI |
| **crapper** | nenhuma função de `core/` com CRAP ≥ 10 fora de `tools/quality/crap_allowlist.txt` (cada linha com justificativa) | CI, bloqueante após R-607 |
| **dryer** | nenhum par ≥ 0,8 fora de `dry_allowlist.txt` | CI, bloqueante após R-605/R-606 |
| **mutator** | ≥ 80 % nos módulos de decisão (amostra fixa de 40 mutantes, semente fixa) | CI noturno |
| Intermitência | suíte 20× seguidas sem falha | antes de cada release |
| Plataformas | Linux e Windows; exe congelado com smoke | CI |
| Propriedades | §5.1, §5.6, R-212 com 200 exemplos | CI |

---

## 10. Interface: sessão e decomposição

### 10.1 Ciclo de vida da sessão — R-104/R-105

```python
class SessionController:
    def request_switch(self, target: SwitchTarget) -> bool   # passa pelo portão de descarte
    def reset(self, project: Project, *, text: str | None = None) -> None
```

`SwitchTarget` ∈ {novo, abrir caminho, abrir recente, importar PGN, importar texto,
recuperar, resultado do PDF inteiro}. Todos chamam `request_switch`, que:

1. se a sessão está suja, pergunta (Salvar / Descartar / Cancelar);
2. cancela todos os `WorkerHost` da sessão (§4.3);
3. fecha PDF e zera caches de OCR e achados do motor;
4. instala dicionário e ignorados **do projeto novo** (nunca herda);
5. carrega o texto e só então configura o reconhecimento, com **uma** análise;
6. reconcilia registros contra o texto **novo** (§6.2);
7. incrementa `generation` (§4.1).

**Testes de clique-caminho** (R-706), um por linha:

| Fluxo | Esperado |
|---|---|
| editar → abrir recente | pergunta; Cancelar mantém |
| abrir `.plez` com 9 decisões aceitas | 9 aceitas |
| projeto com dicionário → importar texto | dicionário vazio; `Zf3` é alerta |
| OCR do PDF inteiro termina durante edição | pergunta; substituir é desfazível |
| crash → recuperar → salvar | nada do projeto se perde |
| fechar com motor, OCR e parse rodando | sem crash, snapshot apagado só desta instância |

### 10.2 Decomposição — R-601/R-602

| Módulo | Responsabilidade | Hoje em |
|---|---|---|
| `controllers/session.py` | §10.1, autosave, recuperação, fechar | main_window 445-620, 2022-2111 |
| `controllers/review.py` | alertas, fila de revisão, dicionário, com `SpanRef` | 625-711, 741-753, 1548-1585, 1709-1762 |
| `controllers/book.py` | análise → painel de partidas e status | 712-792 |
| `controllers/pdf_diagram.py` | PDF, OCR de diagrama, PDF inteiro | 803-1070 |
| `controllers/edit_commands.py` | gestos estruturais, sempre via revisão atual | 1072-1438 |
| `controllers/view.py` | preferências, layout, dupla visão | 1450-1546 |
| `controllers/engine.py` | motor | 1586-1708 |
| `controllers/report_export.py` | relatórios e exportações, um diálogo genérico | 1100-1200, 1763-2017 |
| `menus.py` | registro de ações e recentes | 285-438 |
| `editor/pgn_text_edit.py`, `analysis_binder.py`, `edit_operations.py`, `highlighting.py`, `cursor_context.py`, `symbol_palette_dialog.py` | partes do `editor_panel` | editor_panel 74-1168 |

Regra: controladores não se conhecem entre si; conversam por sinais da janela ou
pelo `SessionController`. Nenhum módulo de `ui/` com mais de 400 linhas.

---

## 11. Rastreabilidade

Cada achado CONF da auditoria aponta para um item do roadmap, e cada item aponta
para uma seção desta spec. A matriz completa está no fim do roadmap
(*Revisão 1*). Um achado sem item é um bug sem dono: o CI de documentação
(`tests/test_docs.py`) confere que todo id `N-/L-/S-/U-/O-/Q-` citado na auditoria
aparece no roadmap.
