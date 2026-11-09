# Análise Técnica — PGN Live Editor

> Estado histórico: o código atual está em `pgn_live_editor/`, com corpus e
> testes em `tests/`. As referências a `app/` e aos 29 testes abaixo pertencem
> ao diagnóstico original; use os caminhos atuais ao implementar correções.
> Todos os defeitos abaixo foram **reproduzidos empiricamente** contra o pipeline real (venv do projeto),
> não são suposições de leitura de código.

> **Status: todas as fases implementadas.** Corrigidos e travados por teste: **D1 a D19**, sem
> exceção. `tests/test_regressions.py` tem um caso por defeito, e `tests/corpus/` tem páginas de
> livro inteiras. Os 9 pontos órfãos `10... .` dos arquivos em `Trabalhos Salvos/` desaparecem ao
> reabrir e reexportar.
>
> O que fechou por último, na fase 6: **diálogo de promoção** (o programa perguntava nada e promovia
> a dama), **número do lance preto após variante**, **coordenadas e inversão do tabuleiro**,
> **atalhos**, **`load_custom_dictionary`** — que era código morto prometendo um dicionário de
> usuário e virou `dictionary_service.py` com três camadas —, **`find_entry_for_position`**, e a
> **barra de status** com a conta do que o parser entendeu. De **D14** sobrava o empacotamento, que
> agora é `packaging/pgn-live-editor.spec`, e o CI, que passou a rodar lint, formatação, tipos e
> cobertura.

---

## 1. O que já existe e é bom

O núcleo do programa está mais maduro do que o tamanho sugere (~2.500 linhas). Três decisões de arquitetura são acertadas e devem ser preservadas:

**1.1 Mapeamento de spans normalizado → bruto.**
`NormalizedText.raw_spans` ([normalizer.py:8-17](../app/core/normalizer.py#L8-L17)) guarda, para cada caractere do texto normalizado, o intervalo correspondente no texto original. É isso que permite marcar o lance no editor bruto mesmo depois de `♘f3` virar `Nf3`. É a peça mais valiosa do projeto e nenhuma refatoração deve quebrá-la.

**1.2 Ancoragem de variantes por legalidade.**
`_choose_variation_anchor` ([parser.py:189-219](../app/core/parser.py#L189-L219)) testa cada ponto de ancoragem possível empurrando os primeiros lances da variante num tabuleiro e pontuando legalidade + coerência do número de lance. É o que faz `12.Be3 (12.fxe5 dxe5 …)` funcionar — em livro, a variante quase nunca sai do lance imediatamente anterior. Heurística correta para o domínio.

**1.3 Referência retroativa vira comentário.**
`_is_backward_reference` ([parser.py:242-274](../app/core/parser.py#L242-L274)) detecta `… 9.exd5 cxd5 {em geral} 8…Re8 seguido por …d6` e entende que `8…Re8` é prosa, não lance. Também é uma heurística específica de livro que outros conversores não têm.

**1.4 Fidelidade verificada.** Reabrindo os dois arquivos reais e re-exportando:

| Arquivo | Lances na linha principal | Nós totais (com variantes) | Idêntico após round-trip |
|---|---|---|---|
| `Partida_13.pgn` | 57 | 82 | ✅ |
| `Partida_14.pgn` | 81 | 97 | ✅ |

Zero erros no `python-chess` nos dois casos. **A base é confiável — o problema não é o núcleo, são as bordas.**

---

## 2. Defeitos confirmados

Severidade: 🔴 corrompe dados em silêncio · 🟠 perde trabalho ou trava fluxo · 🟡 qualidade/polimento

### 🔴 D1 — Prosa em português é corrompida pelo mapeamento de peças

`_apply_piece_translation` ([normalizer.py:148-163](../app/core/normalizer.py#L148-L163)) aplica o mapa de idioma a **qualquer** palavra que case `\b([A-Z])([a-h\-\+x=#1-8]+)\b`. Como o locale está fixo em `'pt'` ([editor_panel.py:92](../app/ui/editor_panel.py#L92)), toda palavra iniciada por C/D/T seguida de letras a–h vira "lance":

```
'Cada lance conta'   -> 'Nada lance conta'
'Dada a posicao'     -> 'Qada a posicao'
'Cabe notar'         -> 'Nabe notar'
'Cede o centro'      -> 'Nede o centro'
'Chega a hora'       -> 'Nhega a hora'
'Tcheca defesa'      -> 'R+eca defesa'
```

Isso só não apareceu nos arquivos salvos porque lá a prosa **já estava** entre `{}` — e comentários são protegidos. Mas o fluxo real é: colar o livro (prosa solta) → só depois colocar as chaves. Durante essa janela inteira o preview e qualquer exportação carregam texto corrompido.

### 🔴 D2 — Substituições literais destroem palavras comuns

`OCR_SUBSTITUTIONS` ([normalizer.py:37-50](../app/core/normalizer.py#L37-L50)) aplica `"ch" → "+"` e `"mate" → "#"` como substring, sem fronteira de palavra:

```
'as chances das brancas'  -> 'as +ances das brancas'
'ameacando mate rapido'   -> 'ameacando # rapido'
'o Cavalo chega a c3'     -> 'o Cavalo +ega a c3'
```

Combinado com D1, praticamente nenhum parágrafo em português sobrevive intacto antes de ser envolvido em chaves.

### 🔴 D3 — Lance ilegível é descartado sem nenhum aviso

```
entrada:     1.e4 e5 2.4Jf3 tDc6
normalizado: 1. e4 e5 2. Nf3 tDc6
resultado:   moves = [e4, e5, Nf3]      issues = 0
```

`4Jf3` foi resgatado corretamente, mas `tDc6` não casou o padrão SAN ([tokenizer.py:38-42](../app/core/tokenizer.py#L38-L42)), caiu no catch-all `TEXT` ([tokenizer.py:49](../app/core/tokenizer.py#L49)) e foi **absorvido como comentário do lance anterior**. A partida perdeu um lance e o contador de problemas continua em zero. Para transcrição de livro este é o defeito mais perigoso de todos: erro silencioso, invisível, e que só aparece meses depois na base.

### 🔴 D4 — Resgate por Levenshtein reescreve lances errados em silêncio

`attempt_levenshtein_rescue` ([move_validator.py:37-62](../app/core/move_validator.py#L37-L62)) usa limiar fixo de 2 e aceita qualquer lance legal mais próximo:

```
'Bh5' com 3.Bb5 esperado    -> corrigido para 'Bb5'   (ok, mas por sorte)
'Nc6' no turno das brancas  -> corrigido para 'Nc3'   (troca o lado do lance!)
'Kd5' a partir do início    -> corrigido para 'd5'    (troca a peça!)
```

Distância 2 num token de 3 caracteres significa que quase qualquer coisa "conserta". Pior: a correção é registrada em `ast.issues` com nível `'info'` — e **`ast.issues` nunca é mostrado**. A UI só exibe a contagem ([chess_panel.py:158-160](../app/ui/chess_panel.py#L158-L160)). O usuário não tem como saber que um lance foi trocado.

### 🔴 D5 — `10. ...` vira `10... .`

O regex de número de lance ([normalizer.py:233](../app/core/normalizer.py#L233)) consome no máximo 3 pontos:

```
'10.... Nxd4'    -> '10... . Nxd4'
'10. ... Nxd4'   -> '10... . Nxd4'      <- estilo clássico de livro impresso
```

O ponto órfão vira parte do token `MOVE_NUMBER` e é impresso literalmente no PGN. **Este defeito já está nos seus arquivos salvos**: `Partida_13.pgn` tem `10... . Nxd4`, `15... . h6`, `18... . Qc8`; `Partida_14.pgn` tem `18... . Bf5`. O `python-chess` tolera, mas ChessBase e SCID não garantem, e o arquivo fica visivelmente errado.

### 🔴 D6 — Segunda partida na mesma colagem é fundida na primeira

```
[Event "Jogo 1"]

1.e4 e5 1-0

[Event "Jogo 2"]

1.d4 d5 0-1
```
→ `headers = {'Event': 'Jogo 1'}`, `moves = [e4, e5, d4, d5]`, `issues = 0`.

`extract_pgn_headers` ([pgn_headers.py:17-53](../app/core/pgn_headers.py#L17-L53)) só lê o bloco de cabeçalho inicial. O segundo `[Event]` vira texto solto, os lances da segunda partida são anexados à primeira (aqui deram legais por acaso) e o resultado `1-0` é sobrescrito por `0-1`. Como o objetivo declarado é **transformar um livro inteiro**, esta é a lacuna funcional mais cara do programa.

### 🟠 D7 — Chave `{` não fechada engole o resto do documento

Enquanto você digita `{`, o normalizador trata tudo até o fim do texto como comentário ([normalizer.py:181-183](../app/core/normalizer.py#L181-L183)) e o parse do restante desmorona:

```
'1.e4 e5 {comentario incompleto 2.Nf3 Nc6 3.Bb5'
-> moves = [e4, e5, Nc3]    # Nc6 virou Nc3 por resgate errado (D4 em cascata)
```

Ou seja: no instante em que você abre uma chave, o tabuleiro e o preview ficam errados até você fechá-la.

### 🟠 D8 — Sufixos de livro quebram o reconhecimento SAN

O lookahead do padrão SAN exige espaço, parêntese ou NAG logo após o lance. Marcadores comuns em livro falham:

```
'2.Nf3N Nc6'  ->  'Nf3N' não é lance; e o 'Nc6' seguinte é resgatado para 'Nc3'
```
Um único `N` de "novidade teórica" derruba o lance **e** contamina o próximo. Mesmo problema com `e.p.`, `‡`, `∆`, `→` colados ao lance.

### 🟠 D9 — Não há como salvar/reabrir o trabalho

O menu tem exatamente uma ação: *Salvar PGN* ([main_window.py:52-88](../app/ui/main_window.py#L52-L88)). Não existe *Novo*, *Abrir*, *Importar PGN*, autosave nem recuperação de sessão. O **texto bruto — que é o trabalho de verdade** — não pode ser salvo nem reaberto. Fechar a janela por engano num capítulo de 40 páginas apaga tudo. A pasta `Trabalhos Salvos/` só contém saídas finais, sem caminho de volta.

### 🟠 D10 — Posição inicial via FEN não é suportada

`TolerantParser.parse` aceita `start_fen` ([parser.py:276](../app/core/parser.py#L276)), mas o editor nunca o passa ([editor_panel.py:243](../app/ui/editor_panel.py#L243)). Com `[FEN "…"] [SetUp "1"]` no cabeçalho:

```
issues: [(5, "O lance 'Kd5' foi corrigido para 'd5'.", 'info')]
```

Todo fragmento de livro que parte de um diagrama (exercícios, finais, posições de estudo) é impossível de transcrever — e ainda por cima gera correções falsas.

### 🟠 D11 — `setPlainText` destrói o histórico de desfazer

*Join Lines* ([editor_panel.py:144](../app/ui/editor_panel.py#L144)) e *Inserir Headers* ([editor_panel.py:176](../app/ui/editor_panel.py#L176)) substituem o documento inteiro. Ctrl+Z não volta, o cursor pula para o início e a rolagem se perde. Num capítulo grande isso é perda de trabalho real.

### 🟡 D12 — Rehighlight quadrático a cada movimento do cursor

Mover o cursor dispara `set_current_move_span` → `_apply_highlights` → `set_ranges` → `rehighlight()` do documento inteiro ([editor_panel.py:215-220](../app/ui/editor_panel.py#L215-L220), [:318](../app/ui/editor_panel.py#L318)). E `highlightBlock` ([editor_panel.py:336-352](../app/ui/editor_panel.py#L336-L352)) varre **todos** os ranges para cada bloco → O(blocos × lances). Em paralelo, `_refresh_preview` regenera o HTML completo da partida a cada movimento de cursor ([chess_panel.py:162-167](../app/ui/chess_panel.py#L162-L167)).

Custo do reparse completo (linear, medido):

| Tamanho | normalize | tokenize | parse | **total** |
|---:|---:|---:|---:|---:|
| 2.7 KB | 3 ms | 1 ms | 28 ms | **32 ms** |
| 10.7 KB | 15 ms | 2 ms | 31 ms | **48 ms** |
| 42.8 KB | 65 ms | 9 ms | 40 ms | **114 ms** |
| 85.6 KB | 142 ms | 18 ms | 50 ms | **210 ms** |

Tudo na thread de UI. Um capítulo de 80 KB congela a interface por ~0,2 s a cada pausa de digitação, mais o rehighlight.

### 🟡 D13 — Demais pontos de qualidade

| Item | Onde | Efeito |
|---|---|---|
| Promoção sempre para dama, sem diálogo | [chess_panel.py:349-363](../app/ui/chess_panel.py#L349-L363), [variant_builder_panel.py:159-173](../app/ui/variant_builder_panel.py#L159-L173) | subpromoção impossível pelo tabuleiro |
| Número do lance preto some após variante | [move_tree.py:201-215](../app/core/move_tree.py#L201-L215) | `) Be6` em vez de `) 12... Be6` |
| `[Date "1945"]` aceito sem validação | [move_tree.py:224-243](../app/core/move_tree.py#L224-L243) | data inválida em PGN (deveria ser `1945.??.??`) |
| `SetUp` não é derivado de `FEN` | idem | PGN incoerente |
| `*` ou `1-0` na prosa viram resultado | [tokenizer.py:47](../app/core/tokenizer.py#L47) | resultado errado |
| Tabuleiro sem coordenadas e sem inversão | [chess_panel.py:118-126](../app/ui/chess_panel.py#L118-L126) | navegação difícil |
| Sem atalhos de teclado (← → para lances) | [main_window.py:52-59](../app/ui/main_window.py#L52-L59) | fluxo lento e repetitivo |
| `load_custom_dictionary` é código morto | [normalizer.py:67-69](../app/core/normalizer.py#L67-L69) | dicionário do usuário prometido e não entregue |
| `find_entry_for_position` não é usado | [move_tree.py:113](../app/core/move_tree.py#L113) | código morto |
| Sem barra de status com feedback contínuo | — | usuário não sabe o que o parser entendeu |

### 🟡 D14 — Infraestrutura

- `.git` inicializado com **zero commits** — nenhuma rede de segurança.
- Pacote chamado `app` — colide com qualquer outro projeto se instalado.
- `sys.path.append` no meio do código de UI ([editor_panel.py:23](../app/ui/editor_panel.py#L23)) e mistura de imports absolutos (`from app.core…`) com relativos (`from ..core…`).
- `requires-python = ">=3.8"` no `pyproject.toml` contra `.python-version` = 3.12 e README pedindo 3.12.
- Sem lint, sem formatação, sem type-check, sem CI, sem teste de UI, sem corpus real de regressão.

---

---

## 2b. Defeitos achados pelo corpus de regressão

Os cinco abaixo **não estavam nesta análise**. Apareceram no dia em que uma página
de livro de verdade — notação portuguesa, quebrada em colunas, com número de página
no meio — entrou no `tests/corpus/`. Nenhum teste sintético os pegaria, porque todos
supunham notação inglesa e texto numa linha só. Vale a pena ler a lista como
argumento a favor do corpus: cinco defeitos, quatro deles capazes de destruir a
partida inteira, num pipeline que já tinha 225 testes verdes.

### 🔴 D15 — Comentário de mais de uma linha era lido como chave aberta

O limite do comentário era o **fim da linha** (a correção de D7). Mas toda página de
livro quebra texto em linhas: praticamente *todo* comentário real caía nessa regra, o
comentário engolia os lances seguintes e o resto da partida desmoronava. Passou
despercebido porque os arquivos de `Trabalhos Salvos/` guardam cada comentário numa
única linha comprida.

**Regra nova:** se existe `}` no mesmo parágrafo, o comentário vai até lá, atravessando
quantas linhas forem; se não existe, para no fim da linha. A linha em branco é o limite
porque nenhum parágrafo tem uma no meio — e a proteção de D7 continua de pé.

### 🔴 D16 — `R` de Rei virava Torre em português

`LOCALE_PIECE_MAPS['pt']` não tinha `R → K`. Pior que faltar: `R` **também** é a Torre
inglesa, e num final de torre as duas leituras costumam ser legais na mesma posição —
`Rc7` é Rei-c7 e Torre-c7 ao mesmo tempo. A leitura exata valia 1.0 incondicionalmente,
então o programa escolhia a torre e destruía a partida do primeiro lance de rei em diante.

**Regra nova:** o documento prova o idioma sozinho. Achando um lance começado por letra
que não existe no SAN inglês (`Cf3`, `Dc7`, `Tf1`), a notação está confirmada e a leitura
inglesa de `R` cai para 0.80, abaixo da leitura de idioma (0.95). Sem essa prova — um PGN
inglês importado — nada muda e `Re1` continua sendo a torre.

### 🟠 D17 — Âncora de variante escolhida às cegas em notação estrangeira

A pontuação de âncora só entendia `SAN_MOVE`. Num livro em português **nenhum** lance é
SAN: são todos candidatos. O tabuleiro não avançava durante a pontuação, e o número de
lance seguinte era comparado com uma posição parada — a variante ia parar no lance errado,
e com ela todos os lances dela.

### 🔴 D18 — Lance entre dois comentários era descartado como prosa

`18.Tf2 {e se} Dc8 {então} 19.Tc1` é escrita corrente de livro. Um candidato só contava
como lance se tivesse notação **ao lado**, e `COMMENT` não estava na lista de vizinhos —
o lance sumia sem alerta nenhum. É D3 outra vez, por outra porta.

### 🟠 D19 — Sinal de avaliação colado ao lance derrubava o lance

`Bg5!` em inglês se salvava (o SAN casa e o `!` vira NAG à parte), mas `Tg5!` em português
morria no filtro de candidato, que não aceitava `!` nem `?` — antes de chegar a quem sabe
tirar o sufixo. Livro imprime a anotação colada o tempo todo.

---

## 2c. Defeitos achados por um livro de repertório inglês

Os três abaixo apareceram quando *The Najdorf Bg5 Revisited* (Lukasz Jarmula,
Thinkers Publishing 2021) entrou no corpus — 746 páginas, 275 partidas, e uma
chave de símbolos impressa na página 5 que serve de gabarito. O padrão é o mesmo
de 2b, com uma diferença que vale registrar: **os três são defeitos silenciosos
de avaliação**, não de lance. Nada quebra, nada some, nada acende no painel de
alertas. O PGN sai plausível e errado, e só a página impressa ao lado revela.

### 🔴 D20 — `+-` era exportado como `$16` (metade da avaliação)

`SYMBOL_PATTERNS` reescrevia `+-` como `±` e `-+` como `∓`. Mas `±` é `$16`,
"claramente melhor", e `+-` é `$18`, **"ganha"** — são linhas diferentes na chave
de símbolos de qualquer livro, e a forma ASCII de `±` é `+/-`, não `+-`.

Só no volume 1 do Najdorf são **191 ocorrências de `+–` e 27 de `–+`**: duzentas
e dezoito posições ganhas que o programa exportava como "um pouco melhor". Nada
falha, nada avisa. É o defeito mais caro desta lista pelo mesmo motivo que o
torna difícil de achar.

### 🔴 D21 — O NFKC destruía a avaliação antes de qualquer análise

A fonte de símbolos da Thinkers (e da Quality Chess, e da Everyman) reaproveita
codepoints latinos: `²` é "brancas um pouco melhor", `³` é o mesmo para as
pretas, `µ` é "pretas claramente melhor". O normalizador faz NFKC no primeiro
passo, e o NFKC converte `²` em `2`, `³` em `3` e `¹` em `1`.

Resultado: colar a página crua transformava `Rad8³` em `Rad83` — que não é lance
nem avaliação, e vira candidato não reconhecido. **212 avaliações** perdidas por
página colada, e junto com elas o lance que as carregava.

### 🟠 D22a — A suíte lia as preferências reais da máquina

`app_data_dir()` apontava sempre para o perfil do usuário, e os testes iam
junto: preferências, dicionário global, caminho do motor, arquivos recentes.
Um `locale` gravado num dia derrubava três testes no outro — e derrubava **só
naquela máquina**, porque o CI roda num runner limpo e continuava verde. Pior:
os testes que gravam preferência estavam mexendo no perfil de verdade.

**Regra nova:** `PGN_LIVE_EDITOR_HOME` desvia o diretório de dados. A suíte
aponta para uma pasta temporária; instalação portátil e diagnóstico com perfil
limpo ganham a mesma porta de graça.

### 🔴 D22b — Janelas mortas-vivas derrubavam o processo

`deleteLater` **agenda** a destruição para a próxima volta do laço de eventos.
Um teste de interface que nunca gira o laço termina deixando a janela viva; a
suíte acumulava uma dúzia delas até algum teste adiante chamar `QTest.qWait` —
e aí todas morriam de uma vez, no meio de outro teste, com os timers e as
threads de todas disparando juntos. O processo morria com corrupção de heap
(`0xc0000374`), **sem exceção e sem traceback**, num teste que não tinha nada a
ver com a causa. Rodar os arquivos separadamente escondia o defeito.

Duas causas de fundo, as duas corrigidas no produto e não só no teste:

- `QTimer.singleShot` estático não tem dono: ele guardava um método ligado a
  uma janela já destruída do lado C++ e disparava assim mesmo. Timers com dono
  (`qt_helpers.single_shot`) morrem com quem os criou.
- O trabalhador do `parse_worker` era destruído **depois** de a thread dele
  parar, ou seja, pela thread errada. Agora ele morre junto com o laço de
  eventos que o hospedava (`thread.finished → runner.deleteLater`), que é o
  idioma que o Qt documenta.

### 🟠 D22c — A novidade teórica custava um alerta e sumia

`12.Nf4N` era lido como um token só, "consertado" para `Nf4` por Levenshtein e
reportado como correção automática: o usuário via um aviso amarelo, e o `N` —
que é `$146`, novidade teórica — desaparecia. Pôr `N` no padrão de NAG resolveria
o sumiço e criaria um defeito muito pior: todo `Nc6` do documento viraria
avaliação.

**Regra nova:** sufixo de livro só casa **colado no fim de um lance**
(espiada-atrás por caractere que termina um SAN). `12.Nf4N` acha a novidade;
`2.Nf3 Nc6` não tem nada colado e fica intacto.

### 🟠 D22 — Três listas de NAG, todas incompletas

O tokenizador procurava nove glifos, o renderizador convertia treze e a paleta
oferecia treze — três listas, três conteúdos. `Rxc6©` não chegava a ser lance
(o tokenizador não conhecia `©`), `Bh6!‚` idem, e os símbolos que mudam de número
conforme o lado só tinham tratamento para zugzwang. A correção não é acrescentar
símbolo em três lugares: é ter **um** lugar (SPEC §2.8).

---

## 3. Diagnóstico em uma frase

O produto já tem um núcleo forte de reconhecimento tolerante e edição assistida;
o próximo gargalo é a confiança operacional: provar de onde veio cada leitura,
manter as decisões humanas após reanálise e revisar apenas o que ainda é
ambíguo.

## 4. Auditoria complementar — 2026-09-10

### Revisão 1: coerência com a implementação

- O pacote real é `pgn_live_editor`, não `app`; links antigos da análise são
  históricos e devem ser atualizados quando uma linha for reutilizada.
- Persistência `.plez`, autosave, múltiplas partidas, dicionário, painel PDF,
  motor, árvores, NAGs e exportações auxiliares já aparecem no código e nos
  testes; não devem voltar ao roadmap como tarefas novas.
- A alegação histórica de suíte verde depende de um ambiente preparado. Nesta
  auditoria, `python -m compileall` passou, mas `pytest -q` não iniciou porque
  `PyQt6` não está instalado no ambiente atual. O procedimento de validação deve
  começar por `uv sync`.

### Revisão 2: riscos de produção que permanecem

- O AST e os spans explicam a leitura atual, mas ainda falta uma entidade
  persistente por token para distinguir confirmação automática, correção,
  rejeição e silêncio deliberado após novas análises.
- O painel PDF usa o texto extraído da página; PDF escaneado, ordem de colunas e
  posição exata da seleção ainda precisam de um pipeline de origem explícito.
- `request_id` já protege parte do trabalho assíncrono, mas a próxima versão
  deve associar também um hash/versão do documento antes de aplicar qualquer
  resultado tardio.
- Exportar a linha principal correta não prova que a árvore completa, os
  comentários e a política de pendências estão corretos; o round-trip precisa
  comparar a estrutura inteira e produzir relatório.
- A produtividade medida deve incluir tempo de revisão, quantidade de alertas
  aceitos/rejeitados, falsos positivos e retorno à fonte, não apenas tempo de
  parse.

Essas duas revisões deram origem às fases 8–12 do [ROADMAP.md](ROADMAP.md) e à
seção 7 da [SPEC.md](SPEC.md). Nenhuma delas é considerada implementada apenas
por estar especificada.

O núcleo (normalizador com spans, parser tolerante, ancoragem de variantes) é sólido e comprovadamente fiel.
O diagnóstico acima é histórico; para o estado atual e para o que falta para
produção, valem a auditoria complementar, a seção 7 da SPEC e as fases 8–12 do
roadmap.

Continua em [SPEC.md](SPEC.md) e [ROADMAP.md](ROADMAP.md).
