# Roadmap — PGN Live Editor

> Ordem de execução derivada de [ANALISE.md](ANALISE.md) e [SPEC.md](SPEC.md).
> Critério de ordenação: **primeiro parar de perder dados, depois acelerar o fluxo, depois escalar para o livro inteiro.**

Estimativas em semanas de trabalho de uma pessoa em ritmo de projeto pessoal.

## Status

| Fase | Situação |
|---|---|
| Ganhos imediatos | ✅ concluída |
| 0 · Fundação | ✅ concluída |
| 1 · Não perder trabalho | ✅ concluída |
| 2 · Parar a corrupção silenciosa | ✅ concluída |
| 3 · Gestos de edição | ✅ concluída |
| 4 · Escala de livro | ✅ concluída |
| 5 · Assistentes | 🔶 motor entregue; dicionário e PDF pendentes |
| 6 · Polimento | 🔶 corpus entregue; o resto pendente |

240 testes passando (`uv run python -m unittest discover -s tests`), lint limpo.

---

## Ganhos imediatos (menos de 1 hora cada)

Faça isto hoje, antes de qualquer planejamento. São correções pontuais de alto impacto.

| # | Ação | Arquivo | Por quê |
|---|---|---|---|
| 1 | `git add -A && git commit` | — | o repositório tem **zero commits**; hoje não há rede de segurança nenhuma |
| 2 | Regex de número de lance aceitando N pontos | [normalizer.py:233](../app/core/normalizer.py#L233) | mata o `10... .` que já está nos seus arquivos salvos (D5) |
| 3 | Fronteira de palavra em `ch` e `mate` | [normalizer.py:43-44](../app/core/normalizer.py#L43-L44) | fim de `chances → +ances` (D2) |
| 4 | Exigir 2+ caracteres de notação no mapa de idioma | [normalizer.py:163](../app/core/normalizer.py#L163) | reduz muito `Cada → Nada` (D1) — a solução completa é a fase 2 |
| 5 | Limiar de Levenshtein proporcional: `1 if len<=3 else 2` | [move_validator.py:37](../app/core/move_validator.py#L37) | corta as reescritas absurdas `Kd5 → d5` (D4) |
| 6 | Passar `start_fen=headers.get('FEN')` ao parser | [editor_panel.py:243](../app/ui/editor_panel.py#L243) | ativa suporte a FEN, que já existe no parser (D10) |
| 7 | Não chamar `set_ranges` em `set_current_move_span` | [editor_panel.py:220](../app/ui/editor_panel.py#L220) | acaba o rehighlight do documento a cada movimento do cursor (D12) |
| 8 | `coordinates=True` no SVG do tabuleiro | [chess_panel.py:122](../app/ui/chess_panel.py#L122) | navegação muito mais fácil |
| 9 | Atalhos `←` `→` para lance anterior/próximo | [main_window.py:52](../app/ui/main_window.py#L52) | o fluxo é repetitivo, o mouse atrapalha |

---

## Fase 0 — Fundação · 1 semana

**Objetivo:** poder mudar o código sem medo.

- Commit inicial, `.gitignore` revisto, branch `main`.
- Renomear pacote `app` → `pgn_live_editor`; remover `sys.path.append` de [editor_panel.py:23](../app/ui/editor_panel.py#L23); padronizar imports relativos.
- `requires-python = ">=3.12"` coerente com `.python-version` e README.
- `ruff` + `black` + `pytest`; GitHub Actions rodando os testes no push.
- Teste de round-trip automático sobre `Trabalhos Salvos/*.pgn` (a baseline já é 82/82 e 97/97 nós — trave isso).

**Aceite:** CI verde, `uv run pgn-live-editor` funcionando após o rename, round-trip protegido por teste.

---

## Fase 1 — Não perder trabalho · 1–2 semanas

**Objetivo:** fechar o programa por engano deixa de ser uma catástrofe. *Esta é a fase de maior retorno imediato.*

- `project_service.py` e formato `.plez` (SPEC §5.1): salvar, abrir, recentes.
- **Autosave** (30 s / 200 caracteres) e recuperação de sessão.
- Menu completo: Novo · Abrir · Salvar · Salvar como · Importar PGN · Importar texto · Exportar PGN.
- **Importar PGN existente** — reabrir os arquivos de `Trabalhos Salvos/` como texto bruto e continuar editando.
- Substituir `setPlainText` por edições com `QTextCursor` + `beginEditBlock` (D11).
- Validação de round-trip **antes** de gravar (SPEC §2.7).

**Aceite:** matar o processo no meio de um capítulo e recuperar tudo ao reabrir; Ctrl+Z desfaz *Join Lines* e *Inserir Headers*.

---

## Fase 2 — Parar a corrupção silenciosa · 2 semanas

**Objetivo:** nada mais é alterado ou descartado sem o usuário ver.

- **`candidates.py`** (SPEC §2.3): tirar idioma e OCR do pré-passe e levá-los para a resolução com contexto de tabuleiro. Corrige D1, D2, D4 na raiz.
- **Token `MOVE_CANDIDATE`** (SPEC §2.2): todo token com cara de lance que não resolve vira alerta. Corrige D3 — fim do descarte silencioso.
- **`issues.py` + painel de alertas** (SPEC §2.4 e §4.2): clique navega, ações Aceitar / Ignorar / Aprender / Virar comentário, `F8` percorre.
- Chave não fechada limitada à linha (D7); sufixos `N`, `e.p.`, `‡`, `∆` (D8); lance nulo `--`.
- Destaque por confiança: verde = corrigido, amarelo = ambíguo, vermelho ondulado = não reconhecido.
- Validação de cabeçalho: `Date`, `Result`, `SetUp` coerente com `FEN` (D13).
- Testes de regressão para D1–D8.

**Aceite:** `1.e4 e5 2.4Jf3 tDc6` produz **um alerta visível**, não uma partida silenciosamente encurtada. Nenhuma palavra de prosa em português é alterada.

---

## Fase 3 — Gestos de edição · 2–3 semanas

**Objetivo:** o que o programa foi feito para fazer — colar o livro e ir marcando estrutura sem digitar delimitador nenhum.

- `text_ops.py`: `Ctrl+K` comentário, `Ctrl+9` variante **com numeração automática**, `Ctrl+Shift+K` desenvolver, parágrafo → comentário (SPEC §3.1).
- **Copiar trechos** (SPEC §3.2): variante, daqui até o fim, sem comentários, FEN, como partida com `[FEN]`, linha principal. Menu de contexto na árvore e no editor.
- **Colar linha com renumeração automática** a partir da posição do tabuleiro.
- Reorganizar (SPEC §3.3): promover / rebaixar variante, reancorar, **renumerar documento** (conserta todo `10... .` e número faltante de uma vez).
- `paste_cleanup.py` (SPEC §3.4): de-hifenização, junção preservando parágrafos, remoção de números de página e cabeçalhos repetidos, com diálogo de pré-visualização.
- Auto-fechamento de delimitadores + verificação de balanceamento.

**Aceite:** transcrever uma partida comentada de 40 lances sem digitar um único `{`, `}`, `(` ou `)`.

---

## Fase 4 — Escala de livro · 2–3 semanas

**Objetivo:** o documento passa a ser um livro, não uma partida.

- `game_splitter.py` (SPEC §2.5): corrige D6, o defeito funcional mais caro.
- **Painel de partidas**: lista, título automático a partir dos cabeçalhos, marca de concluída, progresso "12/64", clique navega.
- Suporte pleno a **fragmentos com FEN** (SPEC §2.6) — exercícios, finais, posições de estudo; marcadores de diagrama reconhecidos.
- Diálogo de cabeçalho com **templates por livro** (autor, fonte, evento, ano preenchidos uma vez e reaproveitados).
- Exportação em lote: livro inteiro num `.pgn`, ou um arquivo por partida.
- `parse_worker.py` (SPEC §5.4): parse fora da thread de UI, incremental por segmento, debounce adaptativo.

**Aceite:** colar um capítulo com 8 partidas, ver 8 entradas na lista, navegar entre elas e exportar um `.pgn` com as 8 íntegras.

**Entregue.** Notas de implementação que valem para as fases seguintes:

- O corte também aceita um separador (`=== Partida 2 ===`) — texto recém-colado de
  PDF ainda não tem cabeçalho nenhum, e sem isso o capítulo inteiro seria uma partida só.
- Os segmentos **ladrilham** o documento: todo caractere pertence a exatamente um deles.
  É o que torna "qual partida está sob o cursor" uma comparação de posição.
- `parse_worker.py` guarda dois caches. O de AST exige a posição exata (é o que
  alimenta os gestos e o cursor); o de **resumo** (`SegmentDigest`) é indexado só
  pelo texto da partida, e por isso sobrevive a uma edição acima empurrar todas as
  seguintes — as posições se corrigem com uma soma. Sem esse segundo cache, o realce
  das partidas de baixo apagava ao primeiro caractere digitado.
- Medido num livro de 64 partidas (22 KB): abrir custa ~460 ms uma vez, cada edição
  custa ~27 ms — bem abaixo do orçamento de 50 ms de digitação da SPEC §6.1.
- `extract_pgn_headers` passou a tolerar linha em branco no meio do bloco de tags.
  É PGN malformado, mas constante em OCR, e antes `[White "..."]` ia parar no
  movetext e virava prosa.

---

## Fase 5 — Assistentes · 3–4 semanas

**Objetivo:** o programa aprende com você e desconfia por você.

- `dictionary_service.py` + editor de dicionário (SPEC §5.2): três camadas, escopo explícito por substituição, teste ao vivo.
- **Aprendizado**: após 3 aceitações da mesma correção, oferecer gravá-la no dicionário do projeto.
- `engine_service.py` (SPEC §5.3): Stockfish opcional. Prioridade ao alerta **`SUSPECT_BLUNDER`** — queda brusca de avaliação num lance de linha principal não marcado com `?` é quase sempre erro de OCR, não erro do mestre. É o recurso de maior valor único do produto.
- Painel de PDF lado a lado (SPEC §5.6): seleciona no PDF, `Ctrl+Enter` manda para o cursor.
- Modo dupla visão com destaque de "o que foi corrigido" vs. "o que foi apenas confirmado".

**Aceite:** o motor aponta um lance mal lido que passou por todas as outras verificações.

**Motor entregue.** Notas de implementação:

- A regra de suspeita é **pura**: `scan_mainline` recebe uma função de avaliação, não
  um motor. A detecção inteira é testada sem Stockfish instalado, com uma busca material
  de dois lances no lugar do motor. É o que permite trocar a fonte de avaliação depois.
- Convenção de sinal única, a mesma do UCI: toda avaliação é em centipeões do ponto de
  vista de quem tem a vez. Assim a queda de um lance vira uma **soma** (`antes + depois`),
  sem troca de sinal no meio — que é onde esse tipo de código costuma errar.
- Cada posição é avaliada uma vez: a posição depois do lance N é a posição antes do N+1.
  Uma partida de 40 lances custa 41 avaliações, não 80.
- O conserto só é proposto quando é **único**. Entre os lances legais a um caractere do
  jogado, se exatamente um não perde material, ele vira candidato aceitável e grava no
  texto bruto com um clique. Havendo empate, o alerta lista e não aplica.
- `ScanFindings` amarra a varredura ao texto que a produziu — mesma ideia do `SegmentDigest`.
  Sem isso os alertas do motor sumiriam no primeiro caractere digitado.
- Falta: `dictionary_service.py` com as três camadas e o editor, aprendizado após 3
  aceitações, painel de PDF e modo dupla visão.

---

## Fase 6 — Polimento · contínuo

- ✅ **Corpus de regressão** (SPEC §6.1) — valeu exatamente o que se esperava dele.
  Quatro páginas em `tests/corpus/`, comparadas por lista de lances e contagem de nós,
  mais o `.expected.pgn` de cada uma. **No primeiro dia achou cinco defeitos** que 225
  testes verdes não achavam (D15–D19 na análise), quatro deles capazes de destruir a
  partida inteira. Todos eram invisíveis para teste sintético pelo mesmo motivo: os
  testes supunham notação inglesa e texto numa linha só, e livro nenhum é assim.
  Acrescentar uma página é largar dois arquivos na pasta — o runner acha sozinho.
- Diálogo de promoção, arrastar-e-soltar, setas `[%cal]`/`[%csl]`.
- Preferências: idioma, tema, fonte, caminho do motor, layout persistido.
- Consistência de acentuação na UI (hoje "Nao", "Simbolos", "posicao" convivem com texto acentuado) e camada de tradução.
- Empacotamento PyInstaller — executável Windows sem instalar Python.
- Exportação `.json` estrutural e `.txt` normalizado.

---

## Visão geral

| Fase | Semanas | Entrega central | Defeitos fechados |
|---|---|---|---|
| Ganhos imediatos | < 1 dia | 9 correções pontuais | parcial D1, D2, D4, D5, D10, D12 |
| 0 · Fundação | 1 | CI, rename, round-trip travado | D14 |
| 1 · Não perder trabalho | 1–2 | projeto `.plez`, autosave, importar PGN | D9, D11 |
| 2 · Parar a corrupção | 2 | `candidates.py` + painel de alertas | D1, D2, D3, D4, D7, D8, D13 |
| 3 · Gestos de edição | 2–3 | envolver, copiar trechos, renumerar | — (novas funções) |
| 4 · Escala de livro | 2–3 | múltiplas partidas, FEN, lote | D6, D10, D12 |
| ↳ *situação* | ✅ | painel de partidas, `.plez` com concluídas, exportação em lote | |
| 5 · Assistentes | 3–4 | dicionário aprendiz, motor, PDF | — (novas funções) |
| ↳ *situação* | 🔶 | motor com `SUSPECT_BLUNDER` (`Ctrl+E`); falta dicionário e PDF | |
| 6 · Polimento | contínuo | corpus, empacotamento, i18n | D13 restante |
| ↳ *situação* | 🔶 | corpus de páginas de livro | D15–D19 |

**Total até um produto completo: cerca de 12 a 16 semanas.** Mas o programa já fica substancialmente melhor no fim da **fase 1** (nunca mais perder trabalho) e a dor real de transcrever livro desaparece no fim da **fase 3**.

---

## Se houver tempo para apenas uma coisa

Faça as fases **1 e 2**. Salvar o trabalho e parar de perder lances em silêncio valem mais do que qualquer funcionalidade nova — porque um PGN em que você não confia inteiramente precisa ser conferido lance a lance, e aí o programa não economizou tempo nenhum.
