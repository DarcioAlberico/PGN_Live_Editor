# Roadmap v3 — PGN Live Editor

> Data: 2026-11-09. Continua a numeração do [ROADMAP.md](ROADMAP.md) (fases 0–13).
> Entrada: [AUDITORIA_2026-11.md](AUDITORIA_2026-11.md) (ids N-, L-, S-, U-, O-, Q-).
> Contratos técnicos: [SPEC_V3.md](SPEC_V3.md) (seções §).
>
> **Critério de ordem** (ub-mode): primeiro o que ajuda todas as fases seguintes;
> depois parar de perder dados; depois parar de corromper em silêncio; depois
> coerência assíncrona; depois escala; só então reestruturar e acrescentar.
> Em cada fase: **tirar complexidade antes de pôr**, uma decisão num lugar só.

## Regras de execução (valem para todo item)

1. **Teste que falha primeiro.** Cada achado CONF já tem uma reprodução; ela vira
   o teste de regressão *antes* da correção, e precisa falhar pelo motivo esperado.
2. **Unidade pequena e verificável.** Um item = um commit (ou poucos), com *por quê*,
   *o quê* e *como foi provado* na mensagem.
3. **Corrigir a causa, não esconder o crash.** `try/except` que só silencia não conta
   como correção (ver §3 da SPEC).
4. **Comportamento, não implementação.** O teste chama o código como o usuário
   chama e confere o que o usuário veria.
5. **Medir antes e depois** com as três ferramentas (R-004): CRAP, duplicação, mutação.

Tamanhos: **P** ≤ ½ dia · **M** 1–2 dias · **G** 3–5 dias.

## Status

| Fase | Tema | Itens | Situação |
|---|---|---|---|
| 14 | Rede de segurança e alavancas | 11 | ⬜ |
| 15 | Não perder trabalho — parte 2 | 9 | ⬜ |
| 16 | Parar a corrupção silenciosa — parte 2 | 13 | ⬜ |
| 17 | Coerência assíncrona (inclui o registro de revisão tipado) | 10 | ⬜ |
| 18 | Escala de livro — desempenho | 5 | ⬜ |
| 19 | Proveniência de diagramas e relatórios | 6 | ⬜ |
| 20 | Make it Right — estrutura | 8 | ⬜ |
| 21 | Dívida de testes e CI | 6 | ⬜ |
| 22 | Ferramentas do usuário | 7 | ⬜ |

---

## Fase 14 — Rede de segurança e alavancas · ~3 dias

**Objetivo:** poder mexer em tudo o que vem depois sem medo e com medida.
É a fase que menos corrige o produto — e a que mais rende, porque todas as
outras dependem dela.

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-001 | P | **Commitar os três meses de trabalho** em commits ordenados por subsistema (core → services → ui → OCR → testes/corpus → docs), cada um com a suíte verde naquele ponto. Ação do dono do repositório. | Q-01 | `git status` limpo exceto `pdf/` e `engine/`; `git log` com ≥ 6 commits novos; cada um passa `pytest` isolado. |
| R-002 | P | Higiene do repositório: tirar `.coverage` do índice; `.gitignore` ganha `.coverage`, `pdf/`, `engine/`, `*.plez.bak*`; apagar a cópia órfã `ui/search_dialog.py` e os diretórios vazios `core/` e `models/` da raiz. | Q-02 | `git ls-files` sem `.coverage`; `ls` da raiz sem `ui/ core/ models/`. |
| R-003 | P | Estabilizar o motor nos testes: `Limit(depth=…)` em vez de tempo; `popen_uci(path, timeout=30)`; uma sessão por classe de teste; marcador `slow`. Revisar se a linha da "celada" do Panov deve mesmo passar sem alerta (R-508). | Q-03 | 20 execuções seguidas de `test_stockfish_integration` sem falha com a CPU ocupada por outra suíte. |
| R-004 | M | **Construir a alavanca**: trazer `crapper`, `dryer` e `mutator` para `tools/quality/`, com alvo documentado no README e um job de CI **não bloqueante** que publica os três relatórios. O mutator ganha timeout por mutante (timeout = morto; a rodada de `layout.py` travou num laço infinito) e semente fixa. | auditoria §6 | `uv run python tools/quality/crapper.py` reproduz a tabela da auditoria §6.2; o job publica os artefatos. |
| R-005 | P | `sys.excepthook` + `threading.excepthook` + log rotativo em `app_data_dir()/logs/` (§3.1 SPEC); o hook tenta um snapshot de recuperação antes de mostrar o erro. **Não corrige nenhum achado** — torna os próximos visíveis e impede que custem o trabalho do usuário. A metade "`_in_flight` preso" do S-03 só fecha com R-303 (`WorkerFailed`). | S-03 (parcial) | Teste: exceção injetada num slot não mata o processo; o log tem o traceback; o snapshot existe. |
| R-006 | P | **Ganhos imediatos** — correções de uma linha, cada uma com seu teste: N-01 (cauda limitada no `NOT_A_MOVE_NUMBER_BEFORE`), N-09 (`½`→`1/2` antes do NFKC), L-17 (CRLF), N-15, L-09 (`_letter`), S-04 (métrica só em append). | N-01, N-09, L-17, N-15, L-09, S-04 | Cada reprodução da auditoria vira teste verde; 40 KB de descritiva normalizam em < 0,3 s (hoje 12,4 s). |
| R-007 | P | Atualizar o quadro de status do `ROADMAP.md` e uma nota em `ANALISE.md` apontando para a auditoria: "concluída" passa a significar "aceite verificado por teste". | Q-06 | Toda fase ✅ do `ROADMAP.md` cita o teste que prova seu aceite; a que não tiver passa a ⚠️ com link para o achado. |
| R-008 | M | **Subtrair antes de somar**: remover o código morto trazido do ChessVisionOFF (`save_checkpoint`, `describe_checkpoint`, `check_compatible`, `git_*`, `RecognitionOrigin`, `recheck_label`, `recognize_region`, `sample_pages`, caminhos de dataset, `atomic_io` se ficar sem uso, `save_sample`/`.dataset`). Prova: nenhum chamador (`grep` + teste de import de todos os módulos). | O-05, O-10 | Linhas de `chess_diagram_ocr/` caem; `python -c "import pgn_live_editor.chess_diagram_ocr.service"` com os extras; suíte verde. |
| R-009 | P | Testes independentes de diretório: fixtures localizam `Trabalhos Salvos/` e `tests/corpus/` a partir do próprio arquivo de teste. **Pré-requisito do mutator**, que roda sobre uma cópia. | auditoria §6.5 | `pytest` rodado de outro cwd passa; mutator sem "BASELINE FAILS". |
| R-010 | P | **Fixar as travas históricas antes de mexer em qualquer coisa**: testes de caracterização para a trava do D4 (`Kd5` nunca vira `d5`; distância máxima por tamanho; letra de peça e casa de destino preservadas), para a ordem "mais longo primeiro" das alternâncias de NAG (D20: `+-` vence `+`), para `normalizer.py:294` (número sem ponto que não é lance) e para o mapa de spans das reescritas. Sem isso, as refatorações das fases 16 e 20 podem desfazer correções antigas em silêncio. | Q-07, auditoria §6.3 | Re-aplicar as mutações da auditoria §6.3 (as do D4 incluídas) faz a suíte **falhar**; placar de `candidates.py` ≥ 70 % já nesta fase. |
| R-011 | P | Um só jeito de rodar os testes: README passa a `uv run pytest`; `unittest discover` deixa de ser documentado; o desvio de `PGN_LIVE_EDITOR_HOME` sai do `conftest.py` para um módulo importado por todo arquivo de teste (`tests/_isolation.py`), que **aborta** se o desvio não estiver ativo — vale para qualquer runner. CI troca `pip install -e` por `uv sync --frozen --extra dev` (o `uv.lock` passa a valer). | Q-08 | `python -m unittest discover -s tests` falha cedo com mensagem clara em vez de tocar o perfil real; CI usa o lockfile. |

> **Por que R-006 fica aqui e não na Fase 18:** N-01 torna o programa inutilizável
> num livro inteiro e a correção é uma linha. Esperar quatro fases por isso seria
> seguir o roadmap contra o usuário.

---

## Fase 15 — Não perder trabalho, parte 2 · ~1 semana

**Objetivo:** nenhuma sequência de cliques, crash, antivírus ou arquivo malformado
apaga trabalho. Ataca a premissa **P2** ("há um único estado de sessão") e **P3**
("gravar é renomear").

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-101 | M | **Gravação durável única** (`services/durable_io.py`, §2.1): temporário no mesmo diretório → `flush`+`fsync` → `.bak` por **cópia** → `os.replace` com novas tentativas curtas em `PermissionError` → rotação só depois do sucesso. Todos os gravadores passam a usá-la. | S-02, S-15 | Testes: rename final falhando deixa o arquivo original intacto e os `.bak` inalterados; arquivo aberto por outro processo → erro claro, nenhuma rotação. `grep write_text services/` só no helper. |
| R-102 | M | **Recuperação v2** (§2.3): o snapshot guarda o *payload completo* do projeto, com versão, por instância (`recovery/<uuid>.json` + lock); restaurar = carregar a origem e sobrepor só o que mudou. | S-01/U-02, S-14 | Teste: crash → recuperar → salvar preserva cabeçalhos, dicionário, partidas concluídas e registros de diagrama; duas instâncias não apagam a recuperação uma da outra. |
| R-103 | M | **Carregadores com esquema** (§2.2) para `.plez`, preferências, `engine.json`, dicionário e snapshot: cada campo validado e coagido sozinho; qualquer erro vira `ProjectFormatError(ValueError)`; campo ruim volta ao padrão *daquele campo* e gera aviso. | S-05, S-06, S-07 | Teste paramétrico com cada campo de cada arquivo trocado por `null`, lista, string e número: nenhum `TypeError` escapa; as outras preferências sobrevivem. |
| R-104 | M | **`SessionController.reset(project)`** (§10): um único caminho para novo, abrir, abrir recente, importar, recuperar e fim do OCR de PDF. Zera ignorados, dicionário, PDF, cache de OCR, achados do motor; cancela workers (com o `cancel()`/`shutdown()` que existem hoje — R-303 os torna confiáveis depois, sem mudar esta interface); reconfigura **sem** reanalisar o texto antigo. | U-01, U-03, U-08 | Teste: abrir `.plez` com 9 decisões aceitas → 9 aceitas; importar depois de um projeto com dicionário → `Zf3` vira alerta, não `Nf3`. |
| R-105 | P | Portão único de descarte: todo caminho de R-104 passa por `_confirm_discard`. | U-03 | Teste: editar → "Abrir recente" → diálogo aparece; Cancelar mantém o texto. |
| R-106 | P | Fim do reconhecimento do PDF inteiro **pergunta** (substituir / novo projeto / cancelar) e, se substituir, faz numa única operação desfazível. | U-04 | Teste: depois de substituir, `Ctrl+Z` devolve o texto anterior. |
| R-107 | P | **Checar onde entra**: surrogates solitários nascem na colagem, na importação e no resultado de OCR — sanitizar nessas três fronteiras (troca por U+FFFD + alerta `INVALID_CHARACTER` com o span). O caminho de salvar ganha captura ampla com mensagem e log como **segunda** linha de defesa, nunca como a correção. | S-13 | Colar texto com U+D800 solto gera alerta e o texto salvo relê igual; teste que força exceção ao salvar mostra erro, processo vivo, texto em memória intacto. |
| R-108 | P | Recentes: projeto que sumiu do disco **não** é tirado da lista sem avisar (provável erro transitório de rede/OneDrive); aparece marcado. | S-02 | Teste: arquivo ausente → item cinza com dica, não removido. |
| R-109 | P | Reconhecimento de PDF inteiro grava página a página (append, JSONL) para que uma queda na página 300 não perca as 299 anteriores; distinguir "falhou o OCR" de "sem camada de texto". | S-15 | Teste com processo filho morto na página N: as N−1 primeiras estão no resultado; a página N tem o motivo real. |

---

## Fase 16 — Parar a corrupção silenciosa, parte 2 · ~1,5 semana

**Objetivo:** voltar a valer o aceite da Fase 2 — *nada some e nada muda de dono sem
o usuário ver*. Ataca a premissa **P4**: primeiro o teste-guia, depois a forma dos
dados, depois as três peças compartilhadas, e só então os casos individuais.

### 16a — O teste-guia, a forma dos dados e as peças compartilhadas (fazer primeiro)

| Item | Tam. | O quê | Achados que caem juntos | Aceite verificável |
|---|---|---|---|---|
| R-212 | M | **Teste de propriedade-guia** (Hypothesis), escrito antes de tudo na fase: *todo token com cara de lance termina como lance, alerta ou comentário explícito — nunca desaparece*; *nenhum caractere de prosa muda* (pontuação incluída); *normalizar duas vezes = uma vez*. **Falha hoje** (N-02, N-04, N-09) e passa no fim da fase. Os casos N- e L- entram como exemplos fixos. `hypothesis` entra no extra `dev`. | SPEC 7.8, N-02, N-04, N-09 | No primeiro commit da fase o teste existe e **falha** com os três exemplos; no último, passa com 200 exemplos gerados no CI. |
| R-213 | M | **`Variation` dataclass** (§5.7) no lugar das três listas paralelas do `MoveNode`, com um padrão único de `anchor_after`. Fica aqui (e não na R-603) porque R-202 e R-203 mexem no mesmo `MoveNode`: mudar a forma uma vez só. | §5.7, N-06 | `grep anchor_after` só lê `Variation.anchor_after`; corpus byte a byte igual antes e depois. |
| R-201 | M | **Um scanner de delimitadores** (`core/delimiters.py`, §5.1) derivado de `COMMENT_RE`/`comment_span_end`: comentários, variantes, colchetes de comando (`[%cal]`), com suporte a comentário multilinha. `check_balance`, `scope_end`, `find_enclosing_delimiter`, `normalize_diagram_marks`, `_brackets_to_parens` e as prévias passam a consultá-lo. | N-07, L-16, (N-12 parcial) | Os 4 casos de N-07 e o L-16 viram testes verdes; nenhuma outra função de `core/` procura `{`/`}` por conta própria (`grep` só acha o scanner e o renderizador). |
| R-202 | M | **`MoveExtent`** no `MoveNode` (§5.2): `number_span`, `san_span`, `nag_end`, `own_comment_span`. Todo gesto de edição que precisa de "o fim do lance" usa só isso. | N-05, N-06, N-08, N-14 | Os exemplos da auditoria viram testes verdes; `san_end_index` só é lido dentro de `MoveExtent`. |
| R-203 | M | **Acumulador de comentários do parser** (§5.3): uma lista de partes e um índice de fronteira por lance/variante, unidos uma vez no fim. Some a lista paralela. | N-03, N-16 | `{A}` preservado no exemplo; 600 mil tokens de prosa em < 1 s. |

### 16b — Casos que não cabem nas peças

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-204 | M | **Citação (referência retroativa)** (§5.4): a coleta para no primeiro número de lance que retoma a sequência atual ou no primeiro lance legal no tabuleiro corrente; sempre gera alerta `CITATION_COLLECTED` com o trecho. | N-02 | O exemplo da auditoria preserva `3. Bb5 a6 4. Ba4 Nf6` na linha principal e mostra um alerta. |
| R-205 | P | Tokenizador preserva o valor e o span originais quando um candidato a lance é reclassificado como TEXT. | N-04 | `This is good. Black is fine.` chega igual ao PGN. |
| R-206 | P | Contexto dos aliases de NAG `„ ‚ ƒ ‰`: só valem colados ao fim de um lance. | N-10 | `Nf3 „gute Idee“` → `Nf3 {„gute Idee“}`; `Nf3‚` continua virando NAG. |
| R-207 | M | **Histórico preservado nas variantes** (§5.5): causa raiz de L-02 é o parser guardar FEN (`set_board_fen`) ao abrir/fechar variante, o que zera `move_stack`. Guardar `board.copy(stack=True)` no lugar do FEN — um ponto só, e qualquer consumidor que dependa de histórico passa a funcionar. Junto: guarda de caixa da descritiva aplicada depois de `prepare_token`. | L-01, L-02 | `Kt-KB3`, `Q-R5ch`, `PxPe.p.` reconhecidos; `7.KN-B3` dá o mesmo lance com ou sem variante antes; `grep set_board_fen core/parser.py` vazio. |
| R-208 | P | Linha em branco é **sempre** quebra de parágrafo em `join_wrapped_lines` e em `paste_cleanup` (inclusive linha só de espaços); resultados e linhas de lance nunca são "repetidas" a remover. | L-03, N-11 | Os dois exemplos da auditoria preservam a regressão e os três `1-0`. |
| R-209 | P | Alertas para o que hoje some: NAG sem lance (`DANGLING_NAG`), `(` não fechado no fim (`UNCLOSED_VARIATION`). | N-12 | Os dois exemplos geram alerta. |
| R-210 | P | `reanchor_variation` sobre lance inválido devolve resultado de "não foi possível" em vez de exceção; `text_insertion` usa o número do último token, não `endswith`. | N-13, N-15 | Testes da auditoria. |
| R-211 | P | Regressão de livro emite `(` depois do lance **repetido** (PGN padrão), sem depender do re-ancoramento tolerante. | L-18 | PGN exportado lido por `python-chess` com a mesma árvore. |

---

## Fase 17 — Coerência assíncrona · ~1,5 semana

**Objetivo:** nenhum resultado é aplicado sem provar que pertence ao estado atual.
Ataca a premissa **P1**. As peças centrais (revisão do documento, registro tipado,
reconciliação) vêm antes dos casos.

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-301 | M | **`DocumentRevision`** (§4.1): `(generation, text_hash, config_hash)`. Todo pedido carimba; todo resultado volta carimbado; quem recebe compara e descarta o que não confere. `parse_now`, `configure` e qualquer edição incrementam `generation`. | S-12/U-06, U-09 | Teste do agente U-06: alerta ignorado não volta; 18 teclas no fim de uma partida → 0 `parse_now` síncronos. |
| R-302a | M | **`RecognitionRecord` tipado + `ReviewAction` enum** (§6.1): `altered` derivado da decisão; `comment_text` só existe em `COMMENTED`; dicionários crus só na fronteira de (de)serialização. | L-10, L-15 | `mypy --strict` no módulo; construir `COMMENTED` sem `comment_text`, ou `ACCEPTED` com `decision` vazia, levanta `ValueError` (teste); filtro "alterados" acha a decisão `Qh5`. |
| R-302b | M | **Reconciliação por alinhamento** (§6.2): `difflib.SequenceMatcher` sobre a sequência de tokens antiga × nova; uma decisão vai para no máximo um token; o que não alinha vira "órfão" visível. | L-04, U-01 | Cenário `Qz9…Qz9` da auditoria: decisões continuam nos tokens certos; nenhuma decisão aplicada duas vezes. |
| R-302 | M | **`SpanRef` verificado** (§4.2) — depende de R-302a/b para relocalizar: aceitar/comentar/revisar/gestos estruturais conferem `text[start:end] == raw_text` na revisão atual; se não conferir, reanalisam e relocalizam pelo `RecognitionRecord`; se ainda não achar, avisam. | U-05, U-15 | `5.O-O Be7` permanece intacto no cenário da auditoria. |
| R-303 | G | **`WorkerHost`** (§4.3) — uma classe só para o encanamento que hoje está copiado cinco vezes: fila de 1 com substituição, cancelamento cooperativo, `kill` do subprocesso, nunca `deleteLater` em thread viva, `busy` ligado ao `request_id` corrente, `WorkerFailed` em vez de exceção crua. Migrar parse, motor, OCR de diagrama, OCR de PDF inteiro e OCR de página. | S-03 (resto), S-08, S-09, U-14, dryer família 1 | Fechar a janela no meio de cada tipo de trabalho: sem crash em 50 repetições; 4 pedidos de varredura → 1 varredura executada; exceção injetada no parse → análise ao vivo continua funcionando. |
| R-304 | M | **Subprocesso que funciona congelado** (§4.4): subcomandos `--worker diagram-ocr` / `--worker full-pdf` despachados em `__main__` antes de criar `QApplication`; `stdout=DEVNULL`, `stderr` guardado (últimos 4 KB) para a mensagem de erro. | S-10, S-11 | No exe do PyInstaller, ler diagrama funciona e não abre segunda janela; filho que escreve 1 MB em stdout termina. |
| R-305 | P | PDF: `open_pdf` zera cache e incrementa o id de OCR; troca de página com guarda `_loading` (um só `page_changed`). | U-11, U-12 | Testes da auditoria. |
| R-306 | P | Resultado de diagrama guarda o **destino** (projeto, PDF, página, posição) no pedido; se o destino mudou, pergunta em vez de inserir no cursor atual. | U-14 | Teste: trocar de PDF com OCR em andamento → nada inserido sem confirmação. |
| R-307 | P | Higiene do editor: `rehighlightBlock` não conta como edição (guarda + `document().revision()`); `_delimiter_cache` com LRU de 256; `load_document_text` desarma o timer. | U-07, U-10 | 300 teclas → cache ≤ 256; abrir projeto = 1 análise, não 2. |
| R-308 | P | Vazamentos de Qt: menus de contexto com `WA_DeleteOnClose`/`deleteLater`; ações de recentes parentadas ao menu; `games_panel` sem `clear()` reentrante. | U-13, U-15 | Contagem de `findChildren(QMenu)` estável após 20 cliques. |

---

## Fase 18 — Escala de livro: desempenho · ~4 dias

**Objetivo:** um livro de 1 MB é editável; nenhuma tecla custa mais que um quadro.

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-401 | M | **Orçamentos em teste** (§8; a máquina de referência é a do dono, com CPU e RAM gravadas no relatório): `normalize`+`tokenize`+`parse` de 1 MB de descritiva < 3 s; trabalho na thread da UI por tecla < 16 ms (p95); abrir projeto de 1 MB < 2 s. Teste de benchmark marcado `slow`, falha se passar do orçamento em 2×. | N-01, N-16 | Benchmark verde na máquina de referência; tempo registrado no relatório do CI. |
| R-402 | M | Normalizador com regiões de comentário calculadas **uma vez** e passadas às ~10 passadas (§5.6); nenhuma regex sobre prefixo não limitado. | N-01 | `grep "whole\[:"` vazio; perfil sem função > 10 % do tempo. |
| R-403 | M | Ancoragem de variante sem replay completo: cache de posição por nó (tabuleiro por ply, aproveitando a R-207) reaproveitado entre variantes. | N-16 | 500 variantes num jogo de 40 lances: < 1 s. |
| R-404 | P | Observabilidade fora do caminho quente: métricas acumuladas em memória e gravadas em append a cada N segundos por thread de fundo; rotação por tamanho. | S-04 | `analyze` com métrica ≤ 1,1 × sem métrica. |
| R-405 | P | Importar o serviço de OCR sem custo de 20 s a frio: `import pymupdf`, imports pesados só na primeira leitura, aquecimento opcional em segundo plano após a janela abrir. | O-11 | Janela abre no mesmo tempo com e sem o extra instalado. |

---

## Fase 19 — Proveniência de diagramas e relatórios · ~4 dias

**Objetivo:** cumprir de fato a SPEC 7.1–7.3 e a Fase 9: cada diagrama diz de onde
veio cada parte do FEN, e os relatórios contam o que de fato existe. (O registro
tipado e a reconciliação por alinhamento foram para a Fase 17 — R-302a/b —
porque o `SpanRef` depende deles para relocalizar.) **Pré-requisito:** R-701, para
não refatorar um subsistema com 0 % de cobertura.

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-503 | P | Diff de projeto e relatório de qualidade sobre registros tipados: chave estável, mudança de texto detectada, deduplicação por span, cascatas filtradas. | L-11, L-12 | 1 lance ruim + 2 ambíguos = "1 erro, 2 avisos"; edição sem decisão humana = "0 decisões adicionadas". |
| R-504 | M | **`DiagramProvenance`** (§6.3) carregada do serviço até o `.plez`: FEN canônico único, origem do lado a jogar, origem do roque, casas reparadas, confiança por casa, sha256 + arquitetura do modelo. O adaptador para de reconstruir FEN. | O-02, O-03, O-04, O-09, O-12 | O `.plez` guarda os campos (margem de orientação **com sinal**, avisos de detecção); o FEN do adaptador = o FEN do serviço. |
| R-505 | P | **Política de revisão numa função só** (§6.4): baixa confiança, ilegal, rei ausente, orientação ambígua, lado `default`/conflitante, roque inferido, casas reparadas → `needs_review`. | O-02, O-08 | Tabela de casos da SPEC vira teste paramétrico. |
| R-506 | P | Vocabulário de "quem joga" num módulo compartilhado (`core/side_to_move.py`) com padrões ancorados; `□` só vale no formato de exercício; texto explícito vence símbolo; teste cruzado com `nag_table`. | O-01 | `Black to move □` → pretas; `23...Rxe5□` não decide lado. |
| R-507 | P | Carga do modelo: `weights_only=True` sem fallback; sha256 conferido para o modelo embutido; caminho vindo de env/UI exige confirmação. | O-07 | Teste: checkpoint com pickle malicioso é recusado. |
| R-508 | P | Motor: "erro do jogador mostrado pelo livro" vira `info` (`PLAYER_ERROR_SHOWN`), não alerta; a regra fica num lugar só junto de `SUSPECT_BLUNDER`. | Q-03 | Linha da celada do Panov: 0 alertas, 1 info. |

---

## Fase 20 — Make it Right: estrutura · ~2 semanas, em fatias

**Objetivo:** que o próximo bug custe uma função, não uma caçada. Cada fatia é um
*refactoring* clássico: fixar o comportamento → mudar a estrutura → provar que nada
mudou (suíte + corpus + mutação no módulo tocado).

| Item | Tam. | O quê | Fonte | Aceite verificável |
|---|---|---|---|---|
| R-601 | G | **Decompor `main_window.py`** (2111 linhas) em controladores: `menus`, `session_controller` (R-104), `review_controller`, `book_controller`, `pdf_diagram_controller`, `edit_commands`, `view_settings`, `engine_controller`, `report_export_controller` (§10.2). A janela só compõe. | U-*, dryer 3–4 | `main_window.py` < 400 linhas; nenhum controlador > 400; testes de UI sem alteração. |
| R-602 | G | **Decompor `editor_panel.py`** (1168): `pgn_text_edit`, `analysis_binder` (R-301), `edit_operations`, `highlighting` (R-307), `cursor_context`, `symbol_palette_dialog`. | U-07, U-09, U-10 | idem, < 400 linhas por módulo. |
| R-603 | G | **`TolerantParser.parse`** (cc 34) em manipuladores por tipo de token sobre um objeto de estado; a pilha de 6-tuplas vira `Frame` dataclass (a `Variation` já veio na R-213). | N-03, §5.7 | `parse` cc ≤ 10; nenhuma função de `parser.py` com CRAP ≥ 10; corpus idêntico byte a byte. |
| R-604 | M | Caminhada única da árvore (`core/tree_walk.py`) para `outline`, `pgn_tree` e `move_tree`. | dryer 2, L-09 | Pares dessa família somem do dryer. |
| R-605 | M | Diálogo de salvar genérico (`_save_via_dialog(título, filtro, escritor)`) e comando de texto genérico (`_apply_text_edit(op)`) na UI. | dryer 3–4 | Pares dessa família somem do dryer. |
| R-606 | P | `json_store` para dicionário/motor/preferências (sobre R-101 e R-103); `board_input` compartilhado por `chess_panel` e `variant_builder_panel`. | dryer 5–6 | idem. |
| R-607 | M | **CRAP < 10 em `core/`**: dividir ou testar as 55 funções acima do limite, começando pelas que têm defeito confirmado (`detect_columns`, `scope_end`, `_matches`, `parse_descriptive`). `match`/tabelas onde a cadeia de `if` é uma tabela disfarçada. | auditoria §6.2 | crapper em `core/` sem linhas acima de 10 (exceções listadas com justificativa). |
| R-608 | M | **Simplificações** que dependem das fases anteriores (o código morto já saiu na R-008): o shim de tupla de `Issue` (`__getitem__/__len__`) sai; `CandidateResolver.active` deixa de remesclar as três camadas a cada chamada (cache invalidado por `config_hash`, §4.1); `refine` sem quad devolve o próprio recorte. | O-06 | Nenhum `issue[0]` no código; suíte e corpus idênticos. |

---

## Fase 21 — Dívida de testes e CI · ~1 semana (corre ao lado, a partir da 15)

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-701 | M | Job de CI **com os extras** (`diagram-ocr`) e testes portados do upstream para `decode`, `orientation`, `fen_utils`, `semantics`, `pdf_text` (com PDFs sintéticos pequenos, como `tests/pdf_fixture.py`). | Q-04, O-* | Cobertura de `chess_diagram_ocr/` ≥ 60 % no job com extras. |
| R-702 | M | **Job Windows** (`windows-latest`): suíte + teste de caminho com acento (`Sem título.plez`) + build PyInstaller + smoke do exe com `--diagnostico` e com `--worker diagram-ocr`. | Q-05, S-11 | Job verde; artefato do exe publicado. |
| R-703 | M | `mypy` em `services/` obrigatório, `ui/` em modo gradual (lista de exclusões que só diminui). | Q-05 | `mypy services` limpo; contador de erros de `ui/` no CI monotonicamente decrescente. |
| R-704 | M | Placar de mutação ≥ 80 % nos módulos de decisão (`tokenizer`, `normalizer`, `candidates`, `parser`, `descriptive`, `game_splitter`, `notation_detect`, `pdf_import`), começando pelos sobreviventes da §6.3 da auditoria (`normalizer.py:294`, mapa de spans `normalizer.py:173-186`, `_mark_notation_context`). | auditoria §6.3 | Relatório do mutator no CI acima do alvo. |
| R-706 | M | Testes de clique-caminho da UI para os fluxos da §10.1 da SPEC (abrir/novo/importar/recuperar/fechar com trabalho em andamento). | U-01..U-05 | Cada fluxo tem um teste offscreen; `main_window.py` ≥ 70 % de cobertura. |
| R-707 | P | Corpus ganha uma página por defeito N-/L- corrigido (texto + PGN esperado), e `tests/corpus/README.md` explica como acrescentar. | todos N-, L- | Corpus com ≥ 20 páginas. |

---

## Fase 22 — Ferramentas do usuário · ~1 semana

Só depois de as fases 14–17 estarem verdes: são melhorias, não proteção.

| Item | Tam. | O quê | Achados | Aceite verificável |
|---|---|---|---|---|
| R-801 | M | **Colunas de PDF** (§7): agrupar por sobreposição horizontal com calha mínima absoluta (≥ 8 pt) e largura real da página; blocos de largura total (títulos) separados primeiro e lidos na ordem vertical. Botão **escondido ou desabilitado com dica** quando não há PyMuPDF. | L-05, U-16 | A4 de duas colunas → 2 colunas; título não funde; sem PyMuPDF o botão diz por quê. |
| R-802 | P | Busca: SAN com caixa, offsets via `re.finditer(re.escape(q), raw, re.I)`, sem posição dupla depois de lance inválido. | L-06, L-07, L-08 | Testes da auditoria. |
| R-803 | M | Sumário com os rótulos do livro (`A1)`, `A2)`) quando o livro os traz; numeração por nível quando não traz. | L-09 | Exemplo `A1/A2` da auditoria. |
| R-804 | P | Divisor de partidas: separador inicial e separadores seguidos não criam partida vazia; título do separador vira `Event`/`White`. | L-13 | Testes da auditoria. |
| R-805 | P | Detecção de notação: sinais de espanhol além de `A` (palavras `jaque`, `enroque`, `negras`), teste de fronteira do limiar 1,5×. | L-14 | Partida espanhola de 24 lances detectada. |
| R-806 | P | Cópia para a área de transferência escapa `"` em tags e `}` em comentários; `shutil.which` do motor ignora o diretório corrente. | S-15 | PGN copiado relido por `python-chess` sem perda. |
| R-807 | P | Modelo de OCR fora do `package-data`: download opcional com sha256 conferido, ou extra separado; o diagnóstico mostra a versão do modelo. | O-13 | Wheel sem `.pt`; `--diagnostico` lista o modelo e o hash. |

---

## Ordem e paralelismo

```
14 ──► 15 ──► 16a (R-212 teste-guia → R-213 forma → R-201/202/203) ──► 16b
          └─► 17: R-301 → R-302a → R-302b → R-302 ;  R-303 → R-304
16a + 17 ──► 18 ──► 19 ──► 20 (em fatias, sempre depois do teste que fixa)
21 corre ao lado a partir da 15 (R-701 antes da 19); 22 depois da 17.
```

- **Se houver tempo para uma coisa só:** R-001 (commit) e R-006 (N-01). A primeira
  protege três meses de trabalho; a segunda torna o livro inteiro editável.
- **Se houver tempo para uma semana:** fase 14 inteira (com R-010 antes de qualquer
  refatoração) + R-101, R-102, R-104, R-204.

## Definição de pronto v3

- Todo achado CONF da auditoria tem um teste que falhava e agora passa.
- `crapper` em `core/` sem função ≥ 10 fora da lista de exceções justificadas.
- `dryer` sem par ≥ 0,8 fora da lista de exceções justificadas.
- Mutação ≥ 80 % nos módulos de decisão.
- Nenhum `except Exception: pass/return` sem log e sem mensagem (§3.2).
- Suíte verde 20 vezes seguidas (sem intermitência) em Linux **e** Windows.
- Exe empacotado lê diagrama e PDF inteiro numa máquina limpa.

---

## Revisões deste roadmap

O roadmap passou por cinco revisões, cada uma com uma lente diferente. O que cada
uma mudou fica registrado aqui para que a próxima pessoa veja o porquê da forma
final.

| Revisão | Lente | O que mudou |
|---|---|---|
| 1 | **Rastreabilidade** — todo achado tem dono? (script sobre as duas tabelas) | O-06, O-09 e O-12 estavam sem item: entraram em R-608 e R-504. A contagem de severidades do resumo da auditoria estava errada e foi refeita a partir das tabelas. |
| 2 | **Ordem e dependências** — "primeiro o que ajuda todas as fases", "forma dos dados antes da lógica", "subtrair antes de somar" | `RecognitionRecord` tipado e reconciliação por alinhamento subiram da Fase 19 para a 17 (R-302a/b), porque o `SpanRef` (R-302) depende deles. `Variation` subiu da Fase 20 para a 16a (R-213): R-202 e R-203 mexem no mesmo `MoveNode`, então a forma muda uma vez só. A remoção de código morto subiu para a Fase 14 (R-008). O teste de propriedade passou a abrir a Fase 16 como teste-guia (R-212). Testes independentes de diretório subiram para a 14 (R-009), porque o mutator depende deles. R-104 ficou explícito sobre usar o cancelamento atual até R-303 chegar. R-701 virou pré-requisito da Fase 19. |
| 3 | **Verificabilidade** — cada aceite é um teste ou um número? | A Fase 16a não tinha coluna de aceite: ganhou. R-007, R-302a e R-401 tinham aceites vagos ("coerente", "ilegal não construível", "máquina de referência") e foram trocados por condições checáveis. Na SPEC, limiares inventados (0,85; 0,1) foram trocados pelas constantes reais do código (`ACCEPT_MIN_CONFIDENCE = 0,80`) ou marcados "a calibrar", e o item 4 da §5.4, confuso, foi reescrito. |
| 4 | **Causa raiz** — algum item é um guarda que só esconde o crash? | R-207: a causa do L-02 é o parser guardar FEN; conferido que o FEN preserva roque e en passant e perde só o histórico, então a correção virou `board.copy(stack=True)` num ponto só, e o `DescriptiveContext` paralelo da SPEC foi rejeitado (seria uma segunda verdade). R-107: surrogates passaram a ser tratados onde **entram** (colagem, importação, OCR); a captura no salvar ficou como segunda linha. R-005: explicitado que o excepthook não corrige achado nenhum. |
| 5 | **Caça ao que faltou** — rodar a mutação contra a suíte inteira e reler o caminho de desenvolvimento | Desligar a trava do D4 deixa os 601 testes verdes → novo achado Q-07 e item R-010 (fixar travas históricas antes de qualquer refatoração). O README documenta `unittest discover`, que pula o `conftest.py` e roda contra o perfil real → Q-08 e R-011 (um runner só, isolamento importado por todo teste, CI com `uv sync --frozen`). O mutator precisa de timeout por mutante (um mutante de `layout.py` travou) → R-004. `hypothesis` precisa entrar no extra `dev` → R-212. |

### Matriz de rastreabilidade (achado → item)

Gerada a partir das duas tabelas; um achado sem item seria um bug sem dono.

| Achado | Severidade | Itens |
|---|---|---|
| N-01 | 🔴 crítico | R-006, R-401, R-402 |
| N-02 | 🔴 crítico | R-204, R-212 |
| N-03 | 🟠 alto | R-203, R-603 |
| N-04 | 🟠 alto | R-205, R-212 |
| N-05 | 🟠 alto | R-202 |
| N-06 | 🟠 alto | R-202, R-213 |
| N-07 | 🟡 médio | R-201 |
| N-08 | 🟡 médio | R-202 |
| N-09 | 🟡 médio | R-006, R-212 |
| N-10 | 🟡 médio | R-206 |
| N-11 | 🟡 médio | R-208 |
| N-12 | 🟢 baixo | R-201, R-209 |
| N-13 | 🟢 baixo | R-210 |
| N-14 | 🟢 baixo | R-202 |
| N-15 | 🟢 baixo | R-006, R-210 |
| N-16 | 🟡 médio | R-203, R-401, R-403 |
| L-01 | 🟠 alto | R-207 |
| L-02 | 🟠 alto | R-207 |
| L-03 | 🟠 alto | R-208 |
| L-04 | 🟠 alto | R-302b |
| L-05 | 🟠 alto | R-801 |
| L-06 | 🟡 médio | R-802 |
| L-07 | 🟡 médio | R-802 |
| L-08 | 🟡 médio | R-802 |
| L-09 | 🟡 médio | R-006, R-604, R-803 |
| L-10 | 🟡 médio | R-302a |
| L-11 | 🟡 médio | R-503 |
| L-12 | 🟡 médio | R-503 |
| L-13 | 🟢 baixo | R-804 |
| L-14 | 🟢 baixo | R-805 |
| L-15 | 🟢 baixo | R-302a |
| L-16 | 🟢 baixo | R-201 |
| L-17 | 🟢 baixo | R-006 |
| L-18 | 🟢 baixo | R-211 |
| S-01 | 🟠 alto | R-102 |
| S-02 | 🟠 alto | R-101, R-108 |
| S-03 | 🟠 alto | R-005, R-303 |
| S-04 | 🟠 alto | R-006, R-404 |
| S-05 | 🟡 médio-alto | R-103 |
| S-06 | 🟡 médio | R-103 |
| S-07 | 🟡 médio | R-103 |
| S-08 | 🟡 médio | R-303 |
| S-09 | 🟡 médio | R-303 |
| S-10 | 🟡 médio | R-304 |
| S-11 | 🟡 médio | R-304, R-702 |
| S-12 | 🟡 médio | R-301 |
| S-13 | 🟢 baixo-médio | R-107 |
| S-14 | 🟢 baixo-médio | R-102 |
| S-15 | 🟢 baixo | R-101, R-109, R-806 |
| U-01 | 🟠 alto | R-104, R-302b, R-706 |
| U-02 | 🟠 alto | R-102, R-706 |
| U-03 | 🟠 alto | R-104, R-105, R-706 |
| U-04 | 🟠 alto | R-106, R-706 |
| U-05 | 🟠 alto | R-302, R-706 |
| U-06 | 🟡 médio | R-301 |
| U-07 | 🟢 baixo | R-307, R-602 |
| U-08 | 🟡 médio | R-104 |
| U-09 | 🟡 médio | R-301, R-602 |
| U-10 | 🟡 médio | R-307, R-602 |
| U-11 | 🟡 médio | R-305 |
| U-12 | 🟢 baixo | R-305 |
| U-13 | 🟢 baixo | R-308 |
| U-14 | 🟡 médio | R-303, R-306 |
| U-15 | 🟢 baixo | R-302, R-308 |
| U-16 | 🟡 médio | R-801 |
| O-01 | 🟠 alto | R-506, R-701 |
| O-02 | 🟠 alto | R-504, R-505, R-701 |
| O-03 | 🟡 médio | R-504, R-701 |
| O-04 | 🟢 baixo-médio | R-504, R-701 |
| O-05 | 🟡 médio | R-008, R-701 |
| O-06 | 🟢 baixo | R-608, R-701 |
| O-07 | 🟢 baixo | R-507, R-701 |
| O-08 | 🟢 baixo | R-505, R-701 |
| O-09 | 🟢 baixo | R-504, R-701 |
| O-10 | ℹ️ | R-008, R-701 |
| O-11 | ℹ️ | R-405, R-701 |
| O-12 | ℹ️ | R-504, R-701 |
| O-13 | ℹ️ | R-701, R-807 |
| Q-01 | 🔴 crítico (processo) | R-001 |
| Q-02 | 🟢 baixo | R-002 |
| Q-03 | 🟡 médio | R-003, R-508 |
| Q-04 | 🟡 médio | R-701 |
| Q-05 | 🟡 médio | R-702, R-703 |
| Q-06 | 🟢 baixo | R-007 |
| Q-07 | 🟠 alto | R-010 |
| Q-08 | 🟡 médio | R-011 |
