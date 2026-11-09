# Roadmap — PGN Live Editor

> **2026-11-09:** a auditoria [AUDITORIA_2026-11.md](AUDITORIA_2026-11.md) encontrou
> 86 achados (3 críticos) que reabrem critérios de aceite de fases marcadas como
> concluídas abaixo. A continuação — fases 14 a 22 — está em
> [ROADMAP_V3.md](ROADMAP_V3.md), com os contratos em [SPEC_V3.md](SPEC_V3.md).

> Revisão do produto: 2026-09-10. As fases 0–7 abaixo representam o histórico
> já implementado. A partir da fase 8 o roadmap passa a tratar o editor como
> uma ferramenta de produção: cada decisão automática deve ser rastreável,
> revisável e reversível.

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
| 5 · Assistentes | ✅ concluída |
| 6 · Polimento | ✅ concluída |
| 7 · Livro de repertório | ✅ concluída |
| 8 · Confiança e revisão | ✅ concluída |
| 9 · Pipeline PDF profissional | ✅ núcleo concluído |
| 10 · Edição estrutural acelerada | ✅ núcleo concluído |
| 11 · Qualidade chess-first | ✅ concluída |
| 12 · Escala e observabilidade | ✅ concluída |
| 13 · Operação e suporte pós-produção | ✅ núcleo concluído |

Testes passando, cobertura acima de 90 % em `core/`, lint, formatação e tipos limpos.
O CI roda os quatro (`ruff check`, `ruff format --check`, `mypy`, `coverage`).

### Entregas recentes das fases 8–9

- OCR de diagramas nativo, com modelo local, proveniência, FEN, confiança,
  legalidade e fila de revisão persistente.
- Reconhecimento de página e de PDF inteiro em processo isolado do Qt, com
  progresso, cancelamento, relatório por página e tolerância a falhas isoladas.
- Duplo clique no diagrama do PDF para abrir a posição no tabuleiro e gravar
  `[SetUp]`/`[FEN]` no cabeçalho da partida sob o foco.
- Extração integral de texto e diagramas no editor, headers PGN completos e
  filtros da fila por estado, confiança e busca.
- Normalização de comentários de PDF e backup `.bak` antes de sobrescrever PGN.
- Inserção assistida de lance legal com `Ctrl+Space`, seleção de SAN e uma
  única operação de undo.

### Verificações de ambiente recomendadas após a conclusão

- Comparação automatizada em instalações reais de Scid, ChessX e ChessBase.
- Teste do instalador PyInstaller em uma máquina Windows limpa, sem o ambiente
  de desenvolvimento e sem dependências previamente carregadas.

---

## Ganhos imediatos — histórico da fase inicial

Este bloco registra as correções que originaram o projeto. Como as fases 0–7
estão marcadas como concluídas, ele é referência histórica, não uma fila aberta.

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
**Fase entregue por inteiro.** O que veio depois do motor:

- `dictionary_service.py` com as três camadas (embutida → minha → deste projeto) e
  **escopo explícito** por substituição: token inteiro, início do lance ou qualquer
  trecho. O escopo era a peça que faltava — é o que separa `Bh5 → Bb5` de uma regra
  livre que mexe em trinta lances. O editor (`Ctrl+Shift+I`) mostra as três camadas
  juntas com a coluna **Alcance**: quantos tokens do texto aberto agora cada regra
  pega, antes de gravá-la.
- **Aprendizado por repetição**: o programa conta as aceitações manuais e na
  terceira oferece gravar a regra. Uma vez é acaso, duas é coincidência, três é o
  scanner fazendo sempre a mesma coisa.
- **Painel de PDF** (`F11`): a página à esquerda, o texto extraído dela embaixo,
  `Ctrl+Enter` manda a seleção para o cursor. Página e caminho ficam no `.plez`.
  Duas limitações do Qt decidiram o desenho e estão documentadas no módulo: o
  `QPdfView` não deixa selecionar texto na página, e carregar o PDF por caminho
  **prende o arquivo** do usuário pelo resto da sessão (por isso ele é lido para a
  memória).
- **Dupla visão** (`F12`): o realce apaga tudo que o programa apenas confirmou e
  deixa aceso o que ele mexeu. Numa partida de 80 lances com três correções, são
  as três que saltam — que é a única revisão que vale a pena fazer.

---

## Fase 6 — Polimento · contínuo

- ✅ **Corpus de regressão** (SPEC §6.1) — valeu exatamente o que se esperava dele.
  Quatro páginas em `tests/corpus/`, comparadas por lista de lances e contagem de nós,
  mais o `.expected.pgn` de cada uma. **No primeiro dia achou cinco defeitos** que 225
  testes verdes não achavam (D15–D19 na análise), quatro deles capazes de destruir a
  partida inteira. Todos eram invisíveis para teste sintético pelo mesmo motivo: os
  testes supunham notação inglesa e texto numa linha só, e livro nenhum é assim.
  Acrescentar uma página é largar dois arquivos na pasta — o runner acha sozinho.
- ✅ **Tabuleiro**: diálogo de promoção (`Shift` promove a dama direto), arrastar-e-soltar
  com peça fantasma, e setas e casas coloridas `[%cal]`/`[%csl]`. O programa passa a
  **desenhar o que o livro escreveu** — a metade mais valiosa — e a escrever o que se
  desenha nele, sempre dentro do comentário, no texto bruto.
- ✅ **Preferências** (`Ctrl+,`): idioma, tema (escuro e claro, com as duas paletas
  completas), fonte do editor, motor e layout, em `app.db`. SQLite não pelo conteúdo,
  mas porque a gravação acontece **ao fechar a janela** — e um JSON truncado nessa hora
  perde tudo.
- ✅ **Acentuação da interface**: todo texto que aparece na tela. Docstring e comentário
  ficaram em ASCII de propósito — a linha divisória é o que o usuário lê.
- ✅ **Empacotamento PyInstaller**: `packaging/pgn-live-editor.spec`, executável de
  109 MB construído e aberto de verdade.
- ✅ **Exportação `.json` estrutural e `.txt` normalizado**. O `.json` é a única saída
  que não perde nada: span no texto bruto, confiança e o que o programa leu antes de
  corrigir. O `.txt` é o oposto — só número e lance, para passar o olho ao lado da
  página impressa.

---

## Fase 7 — Livro de repertório · 1–2 semanas

**Objetivo:** a página de um livro moderno entra inteira, com estrutura e
comentário, sem digitar um parêntese.

O disparo foi um livro real: *The Najdorf Bg5 Revisited*, Lukasz Jarmula,
Thinkers Publishing 2021 — 746 páginas, 275 partidas, 515 rótulos de variante,
376 colchetes. Colar uma página dele no editor da fase 6 dava movetext quebrado
e prosa perdida. Três coisas faltavam, e uma quarta estava errada.

- ✅ **`nag_table.py` — tabela única** (SPEC §2.8). O tokenizador, o
  renderizador e a paleta tinham cada um a sua lista de símbolos, e as três
  estavam incompletas de um jeito diferente. Agora é uma tabela com 27 entradas,
  três consumidores.
- ✅ **Correção: `+-` é `$18`, não `$16`.** `±` é "claramente melhor"; `+-` é
  "ganha". O normalizador reescrevia `+-` como `±`, e com isso **191 avaliações
  do livro viravam meia avaliação** — sem alerta nenhum, porque nada estava
  quebrado, só rebaixado. É o pior tipo de defeito que esta SPEC existe para
  impedir. A forma ASCII de `±` passa a ser `+/-`.
- ✅ **Símbolo de livro antes do NFKC.** A fonte de símbolos da Thinkers
  reaproveita codepoints latinos, e o NFKC converte `²` em `2`, `³` em `3`,
  `¹` em `1`. Colar a página crua **destruía** 212 avaliações antes de qualquer
  análise: `Rad8³` chegava ao tokenizador como `Rad83`. A conversão passou para
  o começo do pré-passe, com os spans preservados — o realce continua apontando
  para o caractere certo do texto bruto.
- ✅ **`book_import.py` — estrutura** (SPEC §3.6): rótulos `A)`/`B1)`/`B1.2)`
  viram variantes aninhadas, `[ … ; … ]` vira variante, a prosa vira comentário,
  mobiliário de página sai, exercício vira partida.
- ✅ **`outline.py` + aba da árvore** (SPEC §4.7): uma linha por linha de jogo,
  indentada, com o rótulo do livro. Clicar navega.
- ✅ **Rodapé de símbolos** (SPEC §4.6): todos os NAG a um clique, sempre
  visível, com interruptor glifo ⇄ `$n`.

**Aceite — e foi medido.** A página do capítulo 1 colada crua do PDF produz
**exatamente** o mesmo conjunto de lances, variantes e NAGs do PGN que a editora
distribui: 84 nós, linha principal idêntica, árvore idêntica — mais todo o
comentário, que o PGN da editora não tem. Está travado no corpus como
`najdorf_bg5_7qd2`.

Notas de implementação que valem para quem mexer nisto depois:

- **A regra que faz o resto funcionar** é onde a variante entra: o primeiro
  irmão de um grupo *continua* a linha, e os demais viram parênteses **logo
  depois do primeiro lance dele**, não no fim. Em PGN a variante é alternativa
  ao lance imediatamente anterior; pendurá-la no fim da linha ancora tudo trinta
  lances adiante e a partida sai plausível e errada.
- **A quebra de linha do PDF carrega significado, e o sinal é a margem
  direita.** `7...Be7` sozinho numa linha é lance da linha principal; uma linha
  de 98 caracteres continua na seguinte. Duas armadilhas: perguntar isso à linha
  *já remontada* faz o parágrafo crescer e engolir o documento inteiro (a
  pergunta é sempre sobre a última linha **física**), e um colchete aberto tem
  de segurar o parágrafo mesmo quando a linha parece terminada — `15.Bxf6
  [15.Bb7 Rb8=` cabe na margem e acaba em `=`, e mesmo assim continua.
- **Lance no meio de uma frase é assunto, não continuação.** "transposes to the
  6.Be3 e6 variation" não é uma linha a jogar. A regra é de *corrida*, não de
  token: até dois lances cercados de prosa dos dois lados, com a linha já
  começada. O contra-exemplo que a contém veio do mesmo livro — `[After
  16.Bg2?! e5!µ White doesn't...]` tem prosa dos dois lados e é linha de
  verdade, porque `16.Bg2` é o primeiro lance daquele nível. Errar para o lado
  do comentário preserva o texto; errar para o lado do lance quebra a linha.
- **`¹` resolve-se pela posição.** Antes do lance é "melhor é" (chave da
  Thinkers); colado depois é nota de rodapé (todo o resto). Escolher um dos dois
  destruiria o outro em silêncio.
- **`…` não é `∆`**, embora a chave do livro diga que sim: reticência
  tipográfica é lance das pretas em quase todo PDF de xadrez, e trocá-la
  destruiria a numeração.
- O rótulo da árvore é **deslocado de um** em relação à variante do PGN, de
  propósito: o livro chama a continuação de `A)`, então a primeira variante é
  `B)`. Sem o deslocamento, todo rótulo sairia um fora da página impressa.
- **Sufixo de livro precisa de espiada-atrás.** `12.Nf4N` custava um alerta e
  perdia o `$146`; pôr `N` no padrão de NAG resolveria isso e criaria um defeito
  muito pior, com todo `Nc6` do documento virando novidade teórica. A regra é a
  posição: sufixo só casa colado no fim de um lance.

### Árvore dentro do editor

Ver a estrutura no painel ao lado resolve *localizar*; não resolve *editar*. A
edição acontece no texto bruto, e ali uma partida de livro continuava sendo uma
parede de parênteses. Duas adições:

- ✅ **`layout.py` + `Ctrl+T` / `Ctrl+Shift+T`** (SPEC §3.7): uma variante por
  linha, recuada pela profundidade, com o parêntese de fechar alinhado com o de
  abrir. Gesto explícito e num único desfazer — o texto bruto não se reformata
  sozinho (§0, princípio 1).
- ✅ **Cor por profundidade** (SPEC §4.3): o span de realce passou a carregar o
  **número** do nível em vez de um "é variante ou não".

Notas de implementação:

- A transformação mexe **só nos intervalos entre os átomos**. A invariante
  testada é que tirar todo o espaço em branco do antes e do depois dá
  exatamente a mesma coisa — é o que permite oferecer isto como um clique num
  documento de 300 KB. É também idempotente e reversível.
- **Com delimitador aberto o gesto se recusa a rodar.** Um `{` sem fechamento
  vale até o fim da linha; juntar linhas em volta dele muda o que o comentário
  engole, e não existe leitura certa a adivinhar. Recusar e apontar onde está é
  a única saída que não destrói texto.
- As cores são **matizes diferentes**, não o mesmo azul clareando: com quatro
  níveis, tons do mesmo matiz deixam de se distinguir por volta do terceiro — e
  é exatamente ali que a pergunta "de que variante é este lance?" passa a valer.
- Corrigido, duvidoso e não reconhecido **mantêm a sua cor**. "O programa mexeu
  aqui" vale mais que "isto é uma sub-variante", e o recuo já conta a
  profundidade.

### Árvore do PGN dentro do editor

O recuo no texto (`Ctrl+T`) dá a estrutura, mas continua sendo texto: para
saber *em que variante estou* é preciso contar parênteses para trás. A árvore
de linhas (§4.7) responde outra pergunta e mora do outro lado da janela.

- ✅ **`pgn_tree.py` + painel sob o editor** (SPEC §4.8), `F5`. Um item por
  lance, variantes recolhíveis penduradas no lance de que são alternativa,
  cor igual à do realce do texto e sincronia nos dois sentidos com o cursor.

Duas notas:

- **A AST guarda a variante num lance e o leitor a procura noutro.**
  `7.Qd2 ( 7.Be2 … )` fica guardada em `6...e6` — a posição de onde a variante
  parte — mas quem abre a árvore atrás da alternativa a `7.Qd2` olha debaixo de
  `7.Qd2`. `variation_anchor_after` é o que distingue os dois casos, e ignorá-lo
  põe metade das variantes um lance acima do lugar onde se procura por elas.
- **Número de lance em todos os itens.** Na prévia de uma variante recolhida
  vale a regra oposta — é uma corrida horizontal e o número atrapalha —, mas
  numa lista vertical `Nc6` sozinho não localiza nada. Foi um teste que
  mostrou isso, ao falhar por um motivo melhor do que o que eu tinha escrito.

### Saneamento da suíte, feito junto

Três falhas e um travamento que já existiam antes desta fase, e que só
apareciam nesta máquina — o CI, num runner limpo, seguia verde:

- **A suíte lia o perfil real do usuário** (D22a). Um `locale: en` gravado num
  dia derrubava três testes no outro, e os testes que gravam preferência
  mexiam no `app.db` de verdade. `PGN_LIVE_EDITOR_HOME` desvia o diretório de
  dados; o `conftest.py` aponta para uma pasta temporária.
- **O processo morria com corrupção de heap** ao rodar tudo num processo só
  (D22b), sem exceção e sem traceback, num teste que não tinha relação com a
  causa. Eram janelas mortas-vivas: `deleteLater` só destrói quando o laço de
  eventos gira, e uma dúzia delas morria de uma vez dentro de um `qWait`
  alheio. Duas correções de produto (timers com dono, trabalhador destruído na
  thread certa) mais a purga da fila a cada teste.

Resultado: `pytest -q` roda a suíte inteira num processo, **397 testes, zero
falhas** — o que antes só era possível arquivo por arquivo.

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
| ↳ *situação* | ✅ | `SUSPECT_BLUNDER`, dicionário em três camadas, PDF, dupla visão | |
| 6 · Polimento | contínuo | corpus, empacotamento, i18n | D13 restante |
| ↳ *situação* | ✅ | corpus, tabuleiro, preferências, i18n, `.exe`, CI completo | D13, D15–D19 |
| 7 · Livro de repertório | 1–2 | conversor de estrutura, tabela de NAG, árvore, rodapé | D20–D22 |
| ↳ *situação* | ✅ | página crua do PDF = PGN da editora, nó a nó | |

**Total histórico até o produto v1: cerca de 12 a 16 semanas.** O trabalho
seguinte está estimado separadamente nas fases 8–12.

---

## Se houver tempo para apenas uma coisa

Faça as fases **1 e 2**. Salvar o trabalho e parar de perder lances em silêncio valem mais do que qualquer funcionalidade nova — porque um PGN em que você não confia inteiramente precisa ser conferido lance a lance, e aí o programa não economizou tempo nenhum.

---

## Roadmap v2 — próximos incrementos de produtividade

As fases anteriores entregaram a fundação, o conversor de páginas e as três
vistas principais. O risco agora não é mais “o programa sabe ler PGN?”, mas
“o operador consegue corrigir 300 páginas sem perder contexto?”. A prioridade
é reduzir decisões repetitivas e aumentar a evidência de cada decisão.

### Fase 8 — Contrato de confiança e revisão · 1–2 semanas

**Objetivo:** tornar impossível confundir “entendido”, “corrigido”, “inferido”
e “não lido”.

- Criar um `RecognitionRecord` por token: texto bruto, span, texto normalizado,
  interpretação final, candidatos alternativos, regra aplicada, confiança,
  estado do usuário (`pending`, `accepted`, `rejected`, `commented`, `ignored`)
  e versão da análise.
- Exibir no painel uma fila de revisão com filtros por partida, página, nível,
  tipo de erro, confiança e “alterou texto / apenas interpretou”.
- Ações em lote seguras: aceitar somente correções idênticas; rejeitar todas as
  propostas de uma regra; transformar em comentário preservando o original.
- Adicionar “voltar à fonte” para abrir o PDF na página e posição registradas.
- Persistir decisões no `.plez`; uma nova reanálise não pode ressuscitar um
  alerta ignorado nem apagar uma rejeição.

**Entregue.** A fila de reconhecimento agora fica disponível em `F10`, começa
filtrada por pendências e permite filtrar por partida, página, confiança, tipo,
alteração e estado. A identidade dos tokens não depende mais dos vizinhos, as
decisões antigas sobrevivem a edições no texto ao redor e a origem volta ao
trecho do editor e à página do PDF. Ações em lote são transacionais no núcleo:
aceitação exige propostas idênticas, rejeição é limitada à regra escolhida e
comentários preservam o texto original. O estado continua dentro de
`recognition_records` no `.plez`.

**Aceite:** toda alteração automática tem origem e justificativa visíveis; um
usuário consegue revisar apenas os itens pendentes e terminar a sessão sem
precisar reler os lances verdes.

### Fase 9 — Pipeline de importação de PDF profissional · 2–3 semanas

**Objetivo:** separar extração, limpeza, estrutura e reconhecimento.

- Criar uma sessão de importação com etapas e prévias: extração → ordem de
  leitura → limpeza → conversão de livro → partidas → PGN.
- Guardar `source_id`, arquivo, hash, página, bloco/coluna e intervalo do texto
  para cada trecho importado. O `.plez` deve poder reconstruir a origem mesmo
  após novas colagens.
- Detecção de colunas e cabeçalhos/rodapés repetidos configurável por página;
  permitir selecionar uma região e escolher manualmente a ordem das colunas.
- Detectar PDF escaneado sem camada de texto e orientar OCR externo/importação
  de imagens, sem fingir que a página foi extraída corretamente.
- Permitir importar várias páginas/faixas de uma vez, com fila, cancelamento e
  relatório por página.
- Preservar símbolos tipográficos e Unicode antes de qualquer normalização;
  adicionar perfis de fonte/livro para regras recorrentes.

#### Integração obrigatória do OCR de diagramas

O OCR de diagramas será reaproveitado do projeto local
`C:\Python-Chess2\ChessVisionOFF_Puro`. A estratégia é trazer o núcleo de
reconhecimento para este projeto e adaptá-lo ao fluxo PGN, sem duplicar uma
segunda implementação de visão computacional.

- Incorporar, após inventário, os módulos de detecção, recorte/perspectiva,
  pré-processamento, orientação, inferência de peças, conversão FEN, serviço,
  proveniência e fila de revisão de `src/chess_diagram_ocr/`.
- Reaproveitar os testes e fixtures relevantes de `ChessVisionOFF_Puro`,
  incluindo `board_detection`, `inference`, `fen_utils`, `orientation`,
  `provenance`, `review_queue`, `pdf_io` e os testes de detecção/OCR.
- Reaproveitar os modelos, metadados de classes e configurações somente com
  verificação de compatibilidade de versão; pesos e arquivos grandes devem
  permanecer opcionais e não ser copiados silenciosamente para o repositório.
- Criar uma fachada local, por exemplo `pgn_live_editor.services.diagram_ocr`,
  para que a UI não dependa diretamente dos módulos importados. A fachada deve
  devolver `DiagramRecognition` com imagem recortada, FEN, lado a jogar,
  confiança por casa, confiança global, orientação, página e bounding box.
- Converter o resultado em um fragmento editável: `[FEN "..."]`, `[SetUp
  "1"]`, lado a jogar e metadados de origem; nunca inserir uma posição de baixa
  confiança como se fosse fato confirmado.
- Integrar a posição reconhecida ao tabuleiro e ao parser de movetext: o
  diagrama pode iniciar uma nova partida, definir o FEN da partida sob o
  cursor ou ser anexado como posição de referência ao comentário.
- Encaminhar baixa confiança, FEN ilegal, ausência de rei, orientação ambígua
  e lado a jogar desconhecido para a fila de revisão, com opção de corrigir
  casa a casa no tabuleiro e registrar a decisão no `.plez`.
- Preservar a proveniência do projeto de origem: PDF, página, diagrama,
  bounding box, orientação, modelo e versão do OCR devem chegar ao
  `RecognitionRecord` e ao `.json` estrutural.
- Isolar dependências pesadas de OCR/ML como extra opcional; o editor textual
  deve continuar funcionando quando o modelo não estiver instalado.
- Executar a inferência em subprocesso nativo isolado do Qt/PyQt6. O worker da
  UI permanece assíncrono, troca apenas registros JSON e converte falhas de
  DLL do Torch em diagnóstico acionável, sem travar o editor.
- Validar a integração contra os diagramas de `ChessVisionOFF_Puro` e contra
  páginas dos livros usados neste projeto, comparando FEN, orientação, lado a
  jogar e origem — não apenas se a janela abriu.

**Aceite:** uma página pode ser reprocessada com outra configuração sem perder
as decisões feitas nas demais; cada alerta aponta para a página que o gerou.

**Aceite específico do OCR:** ao selecionar um diagrama no PDF, o programa
detecta/recorta o tabuleiro, gera FEN e o mostra no tabuleiro; posições
ambíguas ficam pendentes de revisão, e uma correção manual casa a casa pode ser
salva, reaberta e exportada como PGN sem perder a origem.

### Fase 10 — Edição estrutural acelerada · 2–3 semanas

**Objetivo:** fazer a maior parte do trabalho com teclado e seleção, sem
reformatar texto manualmente.

- Modo “inserir lance” no ponto do cursor: mostrar lances legais, escolher SAN,
  promoção e lado; inserir numa única operação de undo.
- Comandos explícitos para “linha principal”, “variante alternativa”, “comentário
  antes/depois”, “duplicar ramo”, “promover/reordenar/reancorar” e “extrair como
  nova partida”.
- Seleção sincronizada entre editor, árvore PGN, árvore de variantes, tabuleiro
  e fonte PDF; mostrar sempre o contexto do lance pai.
- Editor de comentários com texto multilinha, notas de fonte, NAG, [%cal]/[%csl]
  e prévia sem abrir diálogos repetidamente.
- Macro/repetição de operações e histórico de ações legível (“aceitou 12
  correções da regra X”), com desfazer transacional.

**Aceite:** transcrever e revisar uma partida comentada de 80 lances usando o
teclado, com no máximo uma ação manual por ambiguidade real.

### Fase 11 — Qualidade chess-first e exportação · 1–2 semanas

**Objetivo:** garantir interoperabilidade com os programas que o enxadrista
realmente usa.

- Validar cabeçalhos obrigatórios e coerência de `Result`, `SetUp`, `FEN`, lado
  a jogar, números de lance e terminador; oferecer correção pré-exportação.
- Testar importação/exportação contra python-chess, Scid, ChessX e ao menos um
  fluxo do ChessBase, usando fixtures reais e comparação da árvore, comentários,
  NAGs e FEN — não apenas da linha principal.
- Exportar com política escolhida: preservar texto original em comentários,
  exportar somente itens aceitos, ou bloquear itens pendentes.
- Relatório de exportação com partidas, nós, comentários, NAGs, alertas
  pendentes e divergências; nunca sobrescrever o arquivo anterior sem backup.
- Suportar arquivos muito grandes por streaming onde possível e limitar o custo
  do HTML/preview a uma janela ou ramo visível.

**Aceite:** 100% dos exports aprovados passam round-trip; um export com alerta
pendente exige decisão explícita e deixa registro da política escolhida.

### Fase 12 — Escala, observabilidade e distribuição · contínua

**Objetivo:** operar livros extensos com diagnóstico objetivo.

- Instrumentar tempo de extração, normalização, parse, renderização, export e
  uso de memória por página/partida, sem coletar texto do usuário.
- Testar documentos de 1 MB, 10 mil nós, 1.000 páginas e variantes profundas;
  cancelar tarefas antigas e descartar resultados obsoletos por `request_id`.
- Validação de schema e migrações do `.plez`; backups rotativos e recuperação
  após interrupção durante escrita.
- Empacotamento reproduzível para Windows, diagnóstico “copiar relatório” e
  verificação de recursos Qt/PyInstaller em máquina limpa.
- Internacionalização separada: idioma da interface, idioma da notação e
  idioma do dicionário não devem compartilhar uma única preferência.

**Entregue.** O pipeline registra duração e contadores de normalização, parse,
renderização, exportação e escrita sem armazenar texto do usuário. O diagnóstico
agrega as métricas localmente. Projetos `.plez` agora têm schema 3, validação
explícita, migração tolerante e backups rotativos atômicos; preferências antigas
de idioma são migradas para interface, notação e dicionário independentes. Os
workers já descartam resultados obsoletos por `request_id`, e a suíte cobre
validação, observabilidade, persistência e cargas de livro existentes.

**Aceite:** abrir, revisar e exportar um livro grande sem congelamento visível,
com métricas reproduzíveis e recuperação de projeto após atualização de versão.

### Fase 13 — Operação e suporte pós-produção

- Diagnóstico copiável e salvável com ambiente, arquitetura, projeto/PDF por
  nome e caminho de métricas, sem incluir o texto do usuário.
- Métricas locais de duração e contadores com retenção limitada.
- Backups rotativos de projetos e relatórios de exportação recuperáveis.

## Ordem recomendada de execução

1. Fase 8, porque sem estados persistentes de revisão qualquer automação nova
   aumenta o risco de erro silencioso.
2. Fase 9, porque a origem PDF é a principal evidência para decidir casos OCR.
3. Fase 10, porque reduz o custo de cada correção manual.
4. Fase 11, porque formaliza o “pronto para exportar”.
5. Fase 12 em paralelo, começando por métricas e testes de carga.

## Definição de pronto para uma versão produtiva

- Nenhum token com aparência de lance desaparece sem alerta.
- Toda correção automática é reversível e tem justificativa e origem.
- Toda partida pode ser isolada, revisada, salva e exportada sem contaminar outra.
- O usuário consegue distinguir texto-fonte, interpretação atual e decisão humana.
- O export é bloqueado ou explicitamente confirmado quando ainda existem
  ambiguidades relevantes.
- Corpus, propriedades, round-trip, UI, performance e empacotamento passam em
  ambiente limpo; não basta o teste funcionar apenas no perfil da máquina do
  desenvolvedor.
