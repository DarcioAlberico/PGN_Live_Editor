# Auditoria — PGN Live Editor · 2026-11-09

> Método: princípios do **ub-mode** (Make it Work → Make it Right → Practice),
> cinco revisões independentes em paralelo (núcleo de texto, núcleo de livro,
> serviços, UI, OCR de diagramas) e as três ferramentas que o ub-mode pede —
> **crapper** (CRAP por função), **dryer** (duplicação ≥ 0,8) e **mutator**
> (teste de mutação). Como nenhuma das três existia nesta máquina, foram
> escritas para a auditoria (ver §6) e rodaram sobre uma **cópia** do projeto.
>
> Regra de evidência: **CONF** = reproduzido por um script que rodou; **PLAUS**
> = lido no código, sem reprodução. Nada aqui foi corrigido: este documento é a
> entrada do [ROADMAP_V3.md](ROADMAP_V3.md) e da [SPEC_V3.md](SPEC_V3.md).

## 0. Resumo

| Medida | Valor |
|---|---|
| Testes | 603 coletados · **601 passam** · 2 falham de forma intermitente (Stockfish, Q-03) |
| Cobertura `core/` | 93 % (meta da SPEC: 80 %) |
| Cobertura do pacote inteiro | **62 %** — `chess_diagram_ocr/` (≈ 7 mil linhas) está em **0 %** |
| `ruff check` / `ruff format` / `mypy core/` | limpos |
| `mypy` em `ui/ services/ chess_diagram_ocr/` | 76 erros em 15 arquivos |
| Funções de `core/` com CRAP ≥ 10 | 55 de 344 |
| Pares de funções duplicadas (≥ 0,8) | 65 |
| Placar de mutação (amostra) | 36 %–85 % por módulo; `candidates.py` 36 %, `normalizer.py` 40 % (§6.3) |
| Último commit | **2026-08-08** — três meses de trabalho fora do git (Q-01) |

86 ids nas tabelas abaixo: **3 críticos** (N-01, N-02 e Q-01, este de processo),
**21 altos, 37 médios, 21 baixos, 4 informativos**. Dois pares são o mesmo defeito
visto de duas camadas (S-01 = U-02, S-12 = U-06) e ficam com os dois ids para
rastreabilidade. 79 dos 86 foram reproduzidos (CONF); 7 são leitura de código (PLAUS).

**Diagnóstico em uma frase.** O núcleo reconhece bem lances isolados, mas o
programa ainda **aplica resultados sem provar que eles pertencem ao estado
atual** — do documento, do projeto, da posição no tabuleiro ou do arquivo em
disco. Quase todos os achados graves são um caso desse único premissa errada
(ver §5).

---

## 1. Núcleo de texto — `core/` (tokenizer, normalizer, parser, text_ops)

| ID | Sev. | Ev. | Local | Defeito → efeito |
|---|---|---|---|---|
| N-01 | 🔴 crítico | CONF | [normalizer.py:292-296](../pgn_live_editor/core/normalizer.py#L292) | `NOT_A_MOVE_NUMBER_BEFORE.search(whole[:start])` roda sobre **todo** o texto anterior a cada número sem ponto → custo quadrático. Medido: 10 KB 1,3 s · 20 KB 5,5 s · 40 KB **12,4 s**. Um livro de 1 MB leva horas. |
| N-02 | 🔴 crítico | CONF | [parser.py:944-950](../pgn_live_editor/core/parser.py#L944), 400-414 | Referência a um número de lance anterior (`Compare 1. d4 d5…`) engole todos os tokens até `)` ou resultado — **inclusive lances reais**, sem alerta. `…2. Nf3 Nc6 Compare 1. d4 d5 in another game. 3. Bb5 a6 4. Ba4 Nf6 1-0` → `{Compare … 3. Bb5 a6 4. Ba4 Nf6} 1-0`. |
| N-03 | 🟠 alto | CONF | [parser.py:886-890](../pgn_live_editor/core/parser.py#L886), 200-208 | Duas listas paralelas de comentários (`comments_after` × `comments_since_last_move`) divergem; o `del [-n:]` apaga o texto errado. `1. e4 e5 {A} (2. Nf3) text (1... c5) 2. Nf3` → `{A}` some. |
| N-04 | 🟠 alto | CONF | [tokenizer.py:115-123](../pgn_live_editor/core/tokenizer.py#L115) | O ponto final é removido antes de o token virar TEXT: `This is good. Black is fine.` → `{This is good Black is fine}`. Toda prosa sem chaves perde a pontuação. |
| N-05 | 🟠 alto | CONF | [text_ops.py:437-439](../pgn_live_editor/core/text_ops.py#L437) | `renumber_document` reescreve `[start_index, san_start_index)` e apaga o que houver entre número e lance: `2. {good idea} Nf3` → `2. Nf3`. |
| N-06 | 🟠 alto | CONF | [text_ops.py:475-476](../pgn_live_editor/core/text_ops.py#L475) | `promote_variation` com `anchor_after` leva o NAG e o comentário do lance-âncora para a linha rebaixada: `e5! {solid}` → `(! {solid} 2. Nf3 …)`. |
| N-07 | 🟡 médio | CONF | text_ops.py:152-212, 680-697; [fen_tools.py:25](../pgn_live_editor/core/fen_tools.py#L25) | Quatro regras diferentes de "onde termina um comentário", nenhuma igual a `COMMENT_PATTERN`: comentário de duas linhas acusado como aberto; `)` dentro de comentário aceito como fim de escopo; `unwrap_at` produz `{real Nc6`; `[D]` reescrito dentro de comentário. |
| N-08 | 🟡 médio | CONF | text_ops.py:312-319, 641 | `paragraph_to_comment` engole o NAG (`1. e4{! Some prose}`); `set_move_annotations` não acha o comentário depois de um NAG e não consegue limpar a marcação. |
| N-09 | 🟡 médio | CONF | normalizer.py:205; tokenizer.py:85 | NFKC transforma `½` em `1⁄2` (U+2044): `½-½` vira comentário e o resultado vira `*`. |
| N-10 | 🟡 médio | CONF | [nag_table.py:129-131](../pgn_live_editor/core/nag_table.py#L129) | Aliases `„ ‚ ƒ ‰` aplicados a todo o texto fora de comentário: aspas alemãs/polonesas viram `$132`. |
| N-11 | 🟡 médio | CONF | [paste_cleanup.py:63-66](../pgn_live_editor/core/paste_cleanup.py#L63), 109 | Linha só com espaços não quebra parágrafo; `_repeated_lines` apaga linhas vistas ≥ 3 vezes — inclusive o `1-0` de três partidas. |
| N-12 | 🟢 baixo | CONF | parser.py:976-978, 1035 | NAG sem lance anterior descartado em silêncio; `(` não fechado no fim do texto não gera alerta. |
| N-13 | 🟢 baixo | CONF | text_ops.py:564-566 | `reanchor_variation` sobre lance inválido derruba com `InvalidMoveError`. |
| N-14 | 🟢 baixo | CONF | text_ops.py:607-614 | `renumber_movetext` desiste com `0-0` e com `e4!`. |
| N-15 | 🟢 baixo | CONF | [text_insertion.py:26](../pgn_live_editor/core/text_insertion.py#L26) | `endswith` casa por acaso: contexto `Game 12.` com lance 2 insere ` e4` sem número. |
| N-16 | 🟡 médio | CONF | parser.py:204-205 | Concatenação repetida de prosa: 150 mil tokens 0,75 s · 600 mil 8,8 s. Ancorar variante custa ~17 ms cada (PLAUS para livros muito anotados). |

## 2. Núcleo de livro — `core/` (descritiva, book_import, revisão, busca)

| ID | Sev. | Ev. | Local | Defeito → efeito |
|---|---|---|---|---|
| L-01 | 🟠 alto | CONF | [descriptive.py:500](../pgn_live_editor/core/descriptive.py#L500) | A guarda de caixa baixa rejeita `Kt-KB3`, `Q-R5ch`, `N-B3ch`, `Q-K2dis.ch`, `PxPe.p.` — formas que o próprio módulo suporta (linhas 114, 161). |
| L-02 | 🟠 alto | CONF | descriptive.py:346-349, 580 ← [parser.py:908](../pgn_live_editor/core/parser.py#L908), 933 | O parser recria o tabuleiro com `set_board_fen` ao entrar/sair de variante, zerando `move_stack`; a descritiva depende dele. Inserir `(6...P-K3)` muda `7.KN-B3` de `Nc3` para `Nf3` **com confiança total**. |
| L-03 | 🟠 alto | CONF | [book_import.py:259](../pgn_live_editor/core/book_import.py#L259), 283 | `join_wrapped_lines` descarta linha em branco quando a linha anterior é a mais larga; a regressão `8.f4…` vira comentário sem alerta. |
| L-04 | 🟠 alto | CONF | [recognition_record.py:33-41](../pgn_live_editor/core/recognition_record.py#L33), 92-102 | Identidade do token = (tipo, texto, ocorrência). Inserir o mesmo token antes desloca **todas** as decisões humanas para o token errado; o fallback pode aplicar uma decisão duas vezes. |
| L-05 | 🟠 alto | CONF | [pdf_columns.py:22-27](../pgn_live_editor/core/pdf_columns.py#L22) | Só separa colunas com calha > 18 % da página. A4 de duas colunas → uma coluna, ordem `L1 R1 L2 R2`. Um título de largura total funde tudo. (Também reproduzido nesta auditoria.) |
| L-06 | 🟡 médio | CONF | [search.py:47-52](../pgn_live_editor/core/search.py#L47) | Busca SAN sem caixa: `bxb5` (peão) acha `Bxb5` (bispo). |
| L-07 | 🟡 médio | CONF | search.py:26-34 | Offsets calculados sobre `casefold()` e aplicados ao original (`Straße`, `İstanbul`) → destaque no lugar errado. |
| L-08 | 🟡 médio | CONF | search.py:93-111 | Lance inválido interrompe a caminhada e a posição sai duas vezes, a segunda rotulada "posição final". |
| L-09 | 🟡 médio | CONF | [outline.py:131-145](../pgn_live_editor/core/outline.py#L131), 35-38 | Rótulos do sumário não seguem o livro (`A1/A2` → `D)`); `_letter(702)` levanta `IndexError`. |
| L-10 | 🟡 médio | CONF | [review_queue.py:34-35](../pgn_live_editor/core/review_queue.py#L34) | `record_is_altered` confia na flag gravada: decisão `Qh5` aparece como não alterada; filtro "alterados" devolve 0. |
| L-11 | 🟡 médio | CONF | [project_diff.py:34-43](../pgn_live_editor/core/project_diff.py#L34) | Diff de decisões: "3 decisões adicionadas" sem decisão humana; registros sem id colapsam em `""`; decisão com texto novo e mesma ação → `changed=False`. |
| L-12 | 🟡 médio | CONF | [quality_report.py:27-53](../pgn_live_editor/core/quality_report.py#L27) | Cada lance inválido/pendente é contado duas vezes; cascatas não são filtradas; "info" listado mas não contado. |
| L-13 | 🟢 baixo | CONF | game_splitter.py:87-90 | Linha em branco antes do primeiro `=== … ===` cria "Partida 1" vazia; título do separador não é usado. |
| L-14 | 🟢 baixo | CONF | notation_detect.py:35, 77 | Partida SAN em espanhol de 24 lances (`Ag5`, `Axd6`) não é detectada (sinal `A` compartilhado com italiano). |
| L-15 | 🟢 baixo | CONF | review_queue.py:77-85, 97-100 | `accept_identical` aceita lote sem decisão; comentado → rejeitado deixa `comment_text` velho. |
| L-16 | 🟢 baixo | CONF | book_import.py:328-334 | `_brackets_to_parens` ignora `[%cal …]` e `{}`: `[9.f3 [%cal Ge4] 9...Bb7]` → `(9.f3 [%cal Ge4) 9...Bb7]`. |
| L-17 | 🟢 baixo | CONF | [layout.py:40](../pgn_live_editor/core/layout.py#L40), 104 | Com CRLF a regex de linha em branco falha e o achatamento junta parágrafos. |
| L-18 | 🟢 baixo | PLAUS | book_import.py:740-742 | `(` de regressão emitido depois do último lance, não do repetido: PGN não padrão que só funciona porque o parser tolerante re-ancora. |

## 3. Serviços — `services/` e `__main__`

| ID | Sev. | Ev. | Local | Defeito → efeito |
|---|---|---|---|---|
| S-01 | 🟠 alto | CONF | [main_window.py:2065-2071](../pgn_live_editor/ui/main_window.py#L2065) + [project_service.py:278-286](../pgn_live_editor/services/project_service.py#L278) | O snapshot de recuperação guarda só `raw_text`; a restauração põe `project.path = origem` num `Project()` vazio. Crash → recuperar → Ctrl+S **sobrescreve o `.plez` sem cabeçalhos, dicionário, partidas concluídas e registros de diagrama.** (= U-02) |
| S-02 | 🟠 alto | CONF | [project_service.py:167-177](../pgn_live_editor/services/project_service.py#L167); export_service.py:184-206 | A rotação renomeia o arquivo para `.bak` **antes** de pôr o novo no lugar. Falha no rename final (antivírus, OneDrive) → nenhum projeto no caminho. WinError 32 → rotação já rodou, backup mais antigo perdido. Nenhum `fsync`. |
| S-03 | 🟠 alto | CONF | [parse_worker.py:368-371](../pgn_live_editor/services/parse_worker.py#L368); `__main__.py` | Sem `sys.excepthook`: exceção num slot → PyQt6 chama `qFatal` → processo morre com 0xC0000409, sem traceback (`console=False`). Com hook, `_in_flight` fica `True` para sempre e a análise ao vivo morre. |
| S-04 | 🟠 alto | CONF | [observability.py:26-40](../pgn_live_editor/services/observability.py#L26), 79-87 | `record_metric` relê e reescreve o `metrics.jsonl` inteiro a cada medida (o arquivo real já está no teto de 1000 linhas): `analyze` 246 ms com métrica × 17 ms sem, **na thread da UI**. UTF-8 inválido no arquivo levanta de dentro do `finally`, quebra parse e export e mascara a exceção original. |
| S-05 | 🟡 médio-alto | CONF | project_service.py:216-232, 306-319; main_window.py:502 | `state.json` malformado (`completed_games: null`, `cursor_position: null`…) levanta `TypeError`/`AttributeError`, fora do `except (OSError, ValueError)` → S-03. O snapshot de recuperação é lido **no início** do programa. |
| S-06 | 🟡 médio | CONF | [preferences_service.py:186-189](../pgn_live_editor/services/preferences_service.py#L186) | Um valor de tipo errado (`editor_font_size: "14"`) faz voltar **todos** os padrões; o `closeEvent` grava os padrões e apaga `engine_path` e tema para sempre. |
| S-07 | 🟡 médio | CONF | engine_service.py:113-116 ← main_window.py:159 | `engine.json` com `movetime_ms: "fast"` impede o programa de abrir. |
| S-08 | 🟡 médio | CONF | engine_service.py:858-870 (idem diagram_ocr_worker, full_pdf_recognition_worker, parse_worker) | `shutdown()` espera 3–5 s e chama `deleteLater` numa thread ainda rodando → crash ao fechar durante varredura. Varredura não cancelável: 4 pedidos = 4 varreduras completas, 1 aproveitada. |
| S-09 | 🟡 médio | CONF | diagram_ocr_worker.py:108-119, 60 | `cancel()` não mata o subprocesso (até 300 s); conclusão velha zera `_busy` com outro pedido rodando; `from_record` com não-dict escapa do `except`. |
| S-10 | 🟡 médio | CONF | [full_pdf_recognition_worker.py:47-64](../pgn_live_editor/services/full_pdf_recognition_worker.py#L47) | `stdout=PIPE` nunca lido → deadlock com ~400 KB de saída; o timeout de 3600 s nunca dispara. `stderr` descartado → erro genérico. |
| S-11 | 🟡 médio | CONF (exe) | diagram_ocr_worker.py:28-31; full_pdf_recognition_worker.py:31-33 | `[sys.executable, "-m", …]` no PyInstaller executa o **próprio exe da UI**: abre uma segunda janela e o OCR estoura o timeout. |
| S-12 | 🟡 médio | CONF | parse_worker.py:412-418, 473-484 | Resultado assíncrono carrega só `request_id`, não a configuração; `parse_now` não incrementa o id. O alerta ignorado volta quando chega o resultado atrasado. Viola SPEC 7.7. (= U-06) |
| S-13 | 🟢 baixo-médio | CONF | project_service.py:160; main_window.py:576 | Surrogate solitário no texto → `UnicodeEncodeError` não capturado ao salvar → S-03. |
| S-14 | 🟢 baixo-médio | PLAUS | project_service.py:274 | Um único `recovery/session.json` global: a segunda instância sobrescreve e, ao fechar, apaga a recuperação da primeira. |
| S-15 | 🟢 baixo | PLAUS | dictionary_service.py:57-65; engine_service.py:194-202, 171; project_service.py:349-359; full_pdf_recognition_process.py:24, 79; clipboard_service.py:136-147 | Escritas não atômicas que falham em silêncio (dicionário truncado carrega vazio); falha de OCR relatada como "sem camada de texto"; páginas gravadas só no fim; tags/comentários copiados sem escapar `"`/`}`; `shutil.which` procura no diretório corrente. |

## 4. Interface — `ui/`

| ID | Sev. | Ev. | Local | Defeito → efeito |
|---|---|---|---|---|
| U-01 | 🟠 alto | CONF | [main_window.py:507-516](../pgn_live_editor/ui/main_window.py#L507), 720-733; editor_panel.py:599-603 | Ao abrir um `.plez`, `configure_recognition` reanalisa o texto **antigo** e reconcilia os registros do projeto novo contra ele: 4 de 9 decisões aceitas viraram pendentes. |
| U-02 | 🟠 alto | CONF | main_window.py:2068-2071 | = S-01, vista pela UI. |
| U-03 | 🟠 alto | CONF | [main_window.py:438](../pgn_live_editor/ui/main_window.py#L438) | "Abrir recente" não passa por `_confirm_discard`: edição não salva perdida sem pergunta. |
| U-04 | 🟠 alto | CONF | [main_window.py:896](../pgn_live_editor/ui/main_window.py#L896) | Fim do reconhecimento do PDF inteiro substitui o documento com `setPlainText` — sem confirmação, apagando o undo. Workers não são cancelados em novo/abrir. |
| U-05 | 🟠 alto | CONF | [main_window.py:1548-1564](../pgn_live_editor/ui/main_window.py#L1548); editor_panel.py:382 | Aceitar/comentar/revisar aplicam `issue.raw_start/raw_end` velhos sem conferir `text[start:end] == raw_text`. Digitar antes do alerta e aceitar dentro do debounce: `5.O-O Be7` → `5.O-Bb5Be7`. Os gestos estruturais (1224-1350) têm a mesma raiz (PLAUS). |
| U-06 | 🟡 médio | CONF | parse_worker.py:425-481; editor_panel.py:522 | = S-12, vista pela UI (cursor e partida ativa também ficam velhos). |
| U-07 | 🟢 baixo | CONF | editor_panel.py:1152 | `rehighlightBlock` emite `textChanged` sem edição: parse extra e contador de autosave avançando. |
| U-08 | 🟡 médio | CONF | main_window.py:470-484, 591-617 | Novo projeto e importação herdam tokens ignorados; a importação herda o **dicionário** do projeto anterior (`Zf3` → `Nf3` corrigido em silêncio com `project.dictionary` vazio). PDF, OCR e achados do motor também ficam. |
| U-09 | 🟡 médio | CONF | editor_panel.py:497-502 | Offsets de segmento velhos fazem o movimento do cursor disparar `parse_now` síncrono completo: 9 a cada 18 teclas. |
| U-10 | 🟡 médio | CONF | editor_panel.py:1050-1100 | `_delimiter_cache` sem limite (301 entradas após 300 teclas); `load_document_text` deixa o timer de parse armado e faz uma análise completa redundante a cada abertura. |
| U-11 | 🟡 médio | CONF | [pdf_panel.py:315-352](../pgn_live_editor/ui/pdf_panel.py#L315) | `open_pdf` não limpa `_ocr_cache` nem incrementa `_ocr_request`: a página 2 do livro B mostra — e "Enviar" envia — o OCR do livro A. |
| U-12 | 🟢 baixo | CONF | pdf_panel.py:376-428 | Uma mudança de página emite `page_changed` duas vezes (dois renders; dois OCRs PLAUS). |
| U-13 | 🟢 baixo | CONF | chess_panel.py:456; pgn_tree_panel.py:356; main_window.py:436 | Vazamentos: um `QMenu` por clique direito; `QAction` de recentes parentadas à janela. |
| U-14 | 🟡 médio | PLAUS | main_window.py:944-996, 1032; pdf_panel.py:622 | Clique em diagrama casado com o pedido do botão já em andamento → lote descartado; resultado inserido no cursor/PDF do momento da chegada; `deleteLater` em `QThread` rodando. |
| U-15 | 🟢 baixo | PLAUS | games_panel.py:136; issues_panel.py:135 | `clear()` reentrante dentro de `currentRowChanged`; seleção do painel de alertas mantida por índice entre reconstruções (Aceitar pode cair noutro alerta). |
| U-16 | 🟡 médio | CONF | [pdf_panel.py:283-312](../pgn_live_editor/ui/pdf_panel.py#L283) | "Ordenar colunas" fica sempre visível, mas depende de PyMuPDF; sem ele `except ImportError: return` — o botão não faz nada e não diz nada. O venv de desenvolvimento não tem PyMuPDF. |

## 5. OCR de diagramas — `chess_diagram_ocr/` e adaptador

| ID | Sev. | Ev. | Local | Defeito → efeito |
|---|---|---|---|---|
| O-01 | 🟠 alto | CONF | chess_diagram_ocr/pdf_text.py:230-231, 296-307, 951-953 | `□` (sinal Informator de "lance único", `$7` na nossa própria tabela) numa linha de legenda vira "brancas jogam" — e é testado antes do texto: `Black to move □` → brancas. Sai com origem `"text"`, que parece autoritativa. |
| O-02 | 🟠 alto | CONF | [services/diagram_ocr.py:47-53](../pgn_live_editor/services/diagram_ocr.py#L47), 202-222 | O adaptador descarta `side_to_move_source`, `side_conflicting`, casas reparadas, confiança por casa e identidade do modelo. Lado "chutado" sai com `needs_review=False` e `accepted` — contra a Fase 9 do roadmap. |
| O-03 | 🟡 médio | CONF | services/diagram_ocr.py:205 × semantics.compose_fen | O adaptador reconstrói o FEN (`placement lado - - 0 1`) e joga fora o que o serviço compôs e validou. Dois construtores de FEN discordando. |
| O-04 | 🟢 baixo-médio | CONF | semantics.py:185-214 | Roque inferido só pela posição (`4k3/…/4K2R` ganha `K`). Hoje escondido por O-03; volta assim que O-03 for corrigido. |
| O-05 | 🟡 médio | CONF | service.py:788 | `save_sample` importa `.dataset`, que não veio para este repositório → `ModuleNotFoundError`. |
| O-06 | 🟢 baixo | CONF | service.py:721, 819-820 | `refine=True` com quad `None` manda a página inteira ao modelo em vez do recorte (latente). |
| O-07 | 🟢 baixo | CONF | checkpoint.py:108-112 | Qualquer `TypeError` em `torch.load(weights_only=True)` refaz a carga **sem** `weights_only` (pickle). O caminho do modelo vem de variável de ambiente ou da UI. |
| O-08 | 🟢 baixo | CONF | semantics.py:154 | Os dois reis em xeque → "padrão: brancas" em vez de "posição impossível". |
| O-09 | 🟢 baixo | PLAUS | orientation.py:345 | `abs(margin)` esconde que a regra de legalidade escolheu a orientação menos confiante. |
| O-10 | ℹ️ | CONF | atomic_io.py; checkpoint.py; service.py; pdf_io.py; config.py | Código morto trazido do ChessVisionOFF (`save_checkpoint`, `describe_checkpoint`, `check_compatible`, `git_*`, `RecognitionOrigin`, `recheck_label`, `recognize_region`, `sample_pages`, caminhos de dataset). `atomic_io` está correto mas só é usado por código morto. |
| O-11 | ℹ️ | CONF | service.py | Importar o serviço custa 1,7 s quente / 20,7 s frio; `import fitz` imprime aviso de depreciação (usar `import pymupdf`). |
| O-12 | ℹ️ | PLAUS | embedded.py:426, 560; pdf_io.py:142 | Quedas silenciosas para detecção por contorno: o motivo não chega ao resultado. |
| O-13 | ℹ️ | CONF | pyproject `package-data` | O modelo de 8,8 MB vai dentro do pacote, contra o "pesos não copiados silenciosamente" da Fase 9. |

## 6. Qualidade, processo e métricas

### 6.1 Processo e infraestrutura

| ID | Sev. | Ev. | Achado |
|---|---|---|---|
| Q-01 | 🔴 crítico (processo) | CONF | Último commit em **2026-08-08**. Há 38 arquivos modificados (~6 mil linhas) e ~60 arquivos novos — subsistemas inteiros (`chess_diagram_ocr/`, `book_import`, `descriptive`, `pgn_tree`, 19 arquivos de teste, corpus) — **fora do git**. Um disco que falhe leva três meses de trabalho. |
| Q-02 | 🟢 baixo | CONF | `.coverage` está versionado; `.gitignore` não cobre `.coverage`. Na raiz há cópia idêntica e órfã de `ui/search_dialog.py` e diretórios vazios `core/` e `models/`. |
| Q-03 | 🟡 médio | CONF | Testes do Stockfish são intermitentes (falha/passa em execuções seguidas): o `initialize` do python-chess tem timeout de 10 s (estoura com o PC carregado) e a avaliação usa **tempo** (50 ms), não profundidade — resultado depende da CPU. Na mesma linha o motor marca `8.Nxd5` da "celada" do Panov. |
| Q-04 | 🟡 médio | CONF | O venv de desenvolvimento não tem `numpy`, `cv2`, `fitz`, `torch`: o subsistema de OCR de diagramas nunca roda nos testes (0 % de cobertura) e o CI não instala os extras. |
| Q-05 | 🟡 médio | CONF | CI só em `ubuntu-latest`, mas o produto é Windows-first (WinRT OCR, PyInstaller, caminhos com acento). `mypy` só cobre `core/`; 76 erros no resto. |
| Q-07 | 🟠 alto | CONF | **A trava do D4 não tem teste.** Desligar em [candidates.py:276-291](../pgn_live_editor/core/candidates.py#L276) a exigência de "mesma letra de peça" e subir a distância máxima de Levenshtein de 2 para 3 deixa a **suíte inteira** verde (601 de 601). A correção mais importante da Fase 2 (não reescrever `Kd5 → d5` em silêncio) pode ser desfeita por qualquer refatoração sem ninguém ver. Idem para a ordem "mais longo primeiro" das alternâncias de NAG (D20). |
| Q-08 | 🟡 médio | CONF (leitura) | O README manda rodar `uv run python -m unittest discover -s tests`; o `unittest` **não carrega** `tests/conftest.py`. Quem segue o README roda a suíte contra o `app_data_dir` real (preferências, dicionário global, recentes — o D22a de volta) e sem a purga de janelas que evita o crash de heap `0xc0000374`. |
| Q-06 | 🟢 baixo | CONF | `ROADMAP.md` declara as fases 0–13 concluídas; os achados acima mostram que vários critérios de aceite (SPEC 7.7, Fase 9 "lado desconhecido vai para revisão", "nenhum lance some sem alerta") não se sustentam. |

### 6.2 crapper — CRAP = cc² · (1 − cobertura)³ + cc

`core/`: 55 de 344 funções com CRAP ≥ 10. As piores:

| CRAP | cc | cob. | Função |
|---|---|---|---|
| 72,0 | 8 | 0 % | `detect_columns` — [pdf_columns.py:17](../pgn_live_editor/core/pdf_columns.py#L17) (e é o L-05) |
| 45,5 | 7 | 8 % | `PasteCleanupReport.summary` — paste_cleanup.py:48 |
| 44,8 | 35 | 80 % | `_matches` — descriptive.py:358 |
| 34,1 | 34 | 96 % | `TolerantParser.parse` — parser.py:848 |
| 30,5 | 21 | 72 % | `parse_descriptive` — descriptive.py:249 |
| 29,1 | 26 | 83 % | `render_game_movetext` — move_tree.py:126 |
| 25,0 | 25 | 97 % | `_contextual_san_alternative` — parser.py:736 |
| 24,1 | 11 | 52 % | `scope_end` — text_ops.py:185 (e é o N-07) |
| 22,6 | 5 | 11 % | `format_quality_report` — quality_report.py:64 |
| 22,0 | 22 | 100 % | `_demote_citations` — book_import.py:434 |

Fora de `core/` (cobertura do pacote inteiro): `chess_diagram_ocr/pdf_text._page_lines` 1332 (cc 36, 0 %),
`detection/hybrid.detect_diagrams` 1190 (cc 34, 0 %), `ui/main_window.export_pgn` 316 (cc 18, 3 %),
`services/clipboard_service._format_line` 213, `ui/main_window._on_diagrams_read` 194,
`ui/chess_panel._on_board_square_pressed` 164. Observação de padrão: **as funções com
mais defeitos confirmados são justamente as de CRAP mais alto** (`detect_columns`,
`scope_end`, `parse`, `_matches`).

### 6.3 mutator — amostra determinística (semente 1234), mutantes de comparação, booleano, inteiro ±1, `not`, `return None`

| Módulo | Mutantes | Mortos | Sobreviventes | Placar |
|---|---|---|---|---|
| `notation_detect.py` | 20 | 14 | 6 | 70 % |
| `pdf_import.py` | 16 | 10 | 6 | 62 % |
| `game_splitter.py` | 20 | 17 | 3 | 85 % |

Sobreviventes que apontam teste faltando (não ruído):

- `notation_detect.py:77` — trocar `or` por `and` na regra de decisão não é notado: o limiar de 1,5 × vice não tem teste de fronteira (e é a raiz de L-14).
- `notation_detect.py:73,78` — `return None` nos dois caminhos de "não sei" sobrevive: nenhum teste exige `None`.
- `pdf_import.py:53,59` — trocar o tamanho do id (`[:24]`) passa: nada fixa o formato do id de origem, que é **persistido** no `.plez`.
- `pdf_import.py:65` — `needs_ocr` invertido sobrevive.
- `game_splitter.py:60` — `start <= position < end` × `<` passa: a fronteira do segmento (posição exatamente no início) não é testada.

Segunda rodada, contra `test_core` + `test_regressions` + `test_roundtrip`:

| Módulo | Mutantes | Mortos | Sobreviventes | Placar |
|---|---|---|---|---|
| `tokenizer.py` | 25 | 13 | 12 | **52 %** |
| `normalizer.py` | 25 | 10 | 15 | **40 %** |

- `normalizer.py:294` — trocar `or` por `and` (e `0` por `1`) na **mesma linha do N-01**
  sobrevive: a regra "isto não é número de lance" não tem teste direto. O bug de
  desempenho e a falta de teste moram juntos.
- `normalizer.py:302` — `dots >= 2` × `> 2` sobrevive: `10.. e5` (dois pontos) não é testado.
- `normalizer.py:173-186, 55` — o mapeamento de spans das reescritas (`chunk_start/end`)
  e a validação de limites sobrevivem a `+`↔`-`: **ninguém confere os spans**, que são
  exatamente o que o destaque e o "Aceitar" usam (família U-05).
- `tokenizer.py:158-166` — `_mark_notation_context` (separadores antes/depois) sobrevive a
  quase toda mutação: a regra que decide se um token está "em contexto de notação" não é
  observada por nenhum teste destas três suítes.

Terceira rodada:

| Módulo | Mutantes | Mortos | Sobreviventes | Placar |
|---|---|---|---|---|
| `nag_table.py` | 20 | 12 | 8 | 60 % |
| `candidates.py` | 25 | 9 | 16 | **36 %** |
| `layout.py` | — | — | — | um mutante pôs `test_layout` em laço infinito (> 900 s); a rodada parou ali |

- `candidates.py:276-291` — **a trava histórica do D4** (`max_distance = 1 if len(core) <= 3
  else 2`, "não trocar a letra da peça", "não trocar a casa de destino") sobrevive a
  mutação em *todas* as condições: nenhuma destas suítes falha se o resgate por
  Levenshtein voltar a reescrever `Kd5 → d5`. A correção mais importante da Fase 2
  está sem teste que a segure.
- `candidates.py:230, 251, 259` — as regras de OCR de prefixo e de letra minúscula
  também não são observadas.
- `nag_table.py:181, 222, 252` — trocar a ordenação "mais longo primeiro" das
  alternâncias de regex sobrevive: nada testa que `+-` vença `+` (o D20 histórico).
- O laço infinito de `layout.py` é ele próprio um achado de ferramenta: o mutator
  precisa de timeout por mutante contado como "morto" (R-004).

O placar baixo é parcialmente efeito da amostra de suítes (o corpus mata mais), mas os
sobreviventes acima são regras sem nenhum teste dedicado — R-704 começa por eles.

### 6.4 dryer — 65 pares ≥ 0,8 (tokens normalizados)

Famílias, da mais cara à mais barata:

1. **Encanamento de worker Qt** — `__init__`/`_ensure_thread`/`shutdown` idênticos em
   `parse_worker`, `engine_service`, `diagram_ocr_worker`, `full_pdf_recognition_worker`
   e `pdf_panel` (0,82–1,00). É também onde moram S-08, S-09, S-12, U-14: **cinco cópias
   do mesmo bug**.
2. **Caminhada da árvore** — `outline.build_outline/walk/_preview_of/_move_label` ×
   `pgn_tree.build_pgn_tree/walk/_preview_of/_label_of` × `move_tree.build_move_entries`
   (0,90–0,98).
3. **Diálogos de salvar/exportar** — `_save_quality_report`, `_save_project_comparison`,
   `export_html`, `export_book`, `_export_other_format` em main_window (0,82–0,90).
4. **Gestos de variante** — `_promote_variation`, `_reanchor_variation`,
   `_demote_mainline`, `_move_variation`, `_on_annotations_changed` (0,83–0,89).
5. **Carga/gravação de JSON de configuração** — `dictionary_service` × `engine_service` (0,96–0,98).
6. **Tabuleiro clicável** — `chess_panel` × `variant_builder_panel`
   (`_on_*square_released`, `_matching_moves`) (0,92–0,95).
7. Menores: `atomic_write_text/bytes`, `app_data_dir`/`metrics_path`,
   `_first/_last_move_number`, `count_moves/count_variations`, `toggled_arrow/square`.

### 6.5 Testes que não protegem o que dizem proteger

- `test_stockfish_integration` depende da CPU (Q-03).
- ~~`test_roundtrip.test_corpus_is_available` depende do diretório corrente~~ —
  **falso positivo** (corrigido em 2026-11-09): o teste resolve `Trabalhos Salvos/` a partir do
  próprio arquivo e passa rodado de `C:\`. A falha vista vinha do mutator, que copiava
  `tests/` sem `Trabalhos Salvos/`; o defeito era da ferramenta, já corrigido nela.
- Nenhum teste de propriedade para spans, idempotência do normalizador ou invariantes de
  árvore, embora a SPEC 7.8 os exija.

---

## 7. Premissas comuns (Attack the Premise)

Os achados graves se agrupam em quatro premissas erradas. Corrigir a premissa
fecha a família inteira; corrigir caso a caso deixa a próxima cópia viva.

| Premissa errada | Achados | Quem mantém o desequilíbrio |
|---|---|---|
| **P1 — "O resultado que chegou ainda vale para o documento atual."** | S-12/U-06, U-01, U-05, U-09, U-11, U-14, S-09, L-04 | 5 workers + editor + painel de alertas + fila de revisão aplicam resultado sem conferir revisão do texto/configuração. |
| **P2 — "Há um único estado de sessão."** Na verdade há pedaços (projeto, dicionário, ignorados, PDF, cache de OCR, achados do motor, recuperação) trocados cada um por um caminho. | S-01/U-02, U-03, U-04, U-08, S-14 | `new_project`, `open`, `open_recent`, `import`, `recover`, fim do OCR de PDF — seis caminhos de troca de sessão. |
| **P3 — "Gravar é renomear."** | S-02, S-15, S-04, S-13, S-05/S-06/S-07 | seis gravadores de arquivo e quatro leitores sem esquema. |
| **P4 — "Comentário, lance e anotação têm a extensão que cada função acha que têm."** | N-03, N-05, N-06, N-07, N-08, N-02, L-16, L-17 | quatro scanners de comentário, três cálculos de "fim do lance", listas paralelas no parser. |

## 8. Registro de ações (ub-mode · Keep a Log)

| # | Ação | Motivo |
|---|---|---|
| 1 | Carregar o skill ub-mode e ler `docs/ROADMAP.md`, `SPEC.md`, `ANALISE.md` | não repetir o que já foi feito e respeitar a numeração existente |
| 2 | `ruff`, `ruff format`, `mypy` (core e resto) | linha de base de higiene |
| 3 | Suíte completa com cobertura, duas vezes (core e pacote inteiro) | medir antes de opinar; base para o crapper |
| 4 | Escrever `crapper.py`, `dryer.py`, `mutator.py` na área temporária | o ub-mode exige as três e nenhuma existia; o mutator trabalha numa cópia para nunca tocar o projeto |
| 5 | Cinco revisões paralelas com reprodução obrigatória | guardar o contexto principal e separar CONF de PLAUS |
| 6 | Rodar novamente os testes do Stockfish isolados | distinguir falha real de intermitência (Q-03) |
| 7 | Reproduzir pessoalmente N-01, N-02, N-04, N-09 e L-05 | não promover para o roadmap achado crítico sem ver com os próprios olhos |
| 8 | Nenhum arquivo de código alterado; nenhum commit | a auditoria é a entrada do roadmap; commit é decisão do dono (Q-01) |

Scripts de reprodução e as três ferramentas ficam em
`C:\Users\AMD\AppData\Local\Temp\claude\7a63c60f2d88f372\67b9c469-88d9-4a01-bb41-983bb33bfbc2\scratchpad\`
(`core_a/`, `core_b/`, `services/`, `ui/`, `ocr/`, `tools/`). O item R-004 do roadmap traz as
ferramentas para dentro do repositório.
