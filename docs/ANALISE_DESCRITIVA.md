# Análise: notação descritiva (inglês e espanhol) e conferência com Stockfish

Data: 2026-09-30. Material de teste: `PDF/Descritiva/` (4 livros em inglês, 4 em espanhol)
e o Stockfish 19 em `pgn_live_editor/engine/stockfish/`.

## 1. Material de teste

| Livro | Idioma | Camada de texto | Uso no teste |
|---|---|---|---|
| Pachman, *Complete Chess Strategy* 1, 2, 3 | inglês | sim (OCR antigo, muito ruidoso) | 168 partidas brutas + Brühl–Philidor transcrita |
| Pachman, *Modern Chess Strategy* | inglês | **não** (imagem) | — |
| Panov, *ABC de las aperturas* | espanhol | **não** (imagem) | Siciliana da p. 38 transcrita da imagem |
| Pachman, *Táctica moderna en ajedrez* I e II | espanhol | **não** (imagem) | diagrama 95 (FEN + linha + variantes) transcrito |
| Averbach, *Finales de alfil y de caballo* | espanhol | **não** (imagem) | — |

## 2. O que estava quebrado

| # | Defeito | Gravidade | Exemplo |
|---|---|---|---|
| 1 | Descritiva **espanhola** não existia (`A` de alfil, colunas `AD`/`AR`) | crítico | página do Panov: 40 de 40 lances inválidos |
| 2 | Descritiva **inglesa** falhava nas formas comuns: coluna sem ala (`N-B3`, `B-N5`, `R-B1`), `x` minúsculo (`PxP`), qualificadores (`QN-Q2`, `KR-B1`, `NPxB`, `RxNP`) | crítico | Brühl–Philidor limpo: 69 de 90 lances inválidos |
| 3 | `QB`/`QN` trocados no inglês: `P-QB3` saía **g3**, `P-QN3` saía **f3** | crítico, silencioso | lance errado aplicado com 82% |
| 4 | Coluna desconhecida virava "qualquer coluna": `P4AD` (espanhol lido como português) saía **h5** | crítico, silencioso | lance errado aplicado com 82% |
| 5 | Número de lance sem ponto (`1 e4 e5 2 Nf3`, padrão inglês) virava comentário `{1} {2}` | alto | também em algébrica pura |
| 6 | Lance vizinho de parêntese era engolido como prosa, **sem alerta** | alto, silencioso | `2 N-KB3 (2 P-KB4` → `{2 N-KB3}` |
| 7 | `2. ..., Nf6` (vírgula depois das reticências, estilo espanhol) lia o lance das pretas como das brancas e o "corrigia" | alto, silencioso | `Nf6` virou `Nf3` |
| 8 | `,` e `;` entre lances iam para o PGN como `{,}` `{;}` | médio | todo livro espanhol |
| 9 | `C(4D)5C`, `T(1A)1D`, `N(Q2)-B3`: o `(` abria uma variante no meio do lance | alto | — |
| 10 | `P x P` com espaços (como o OCR inglês entrega) virava três tokens | alto | — |
| 11 | `+` do livro descartado: `A×P+` com dois peões capturáveis ficava ambíguo | médio | — |
| 12 | Nenhum aviso quando o número impresso não bate com a vez de jogar — o sinal de um lance perdido pelo OCR | alto | `1 P-K4 P-K' 2 B-B4`: `P-K'` some e as brancas passam a jogar os lances das pretas |
| 13 | O motor em `engine/` **não era encontrado** (só PATH / variável de ambiente) | alto | — |
| 14 | A conferência do motor só olhava a **linha principal** | alto | variantes nunca conferidas |
| 15 | Para lance lido em descritiva, o motor sugeria conserto por distância no **SAN** (`C1A` → sugere `Kf8`) | médio | sugestão sem sentido para o livro |

## 3. O que foi feito

| Arquivo | Mudança |
|---|---|
| `core/descriptive.py` (novo) | Gramática por idioma (português, espanhol, inglês): o token vira restrições (peça, qualificador, origem, destino, vítima, promoção) e os lances legais são filtrados. Idioma configurado primeiro, depois os outros. Identidade de peça pela história (`CR`, `QN`), vítima com casa (`D×C3C`), xeque anunciado como desempate, reparo de OCR (`P-KS`, `K-Kl`, `P-Q84`, `P--Q4`, `C1-38`) com confiança menor que 0.90 (sempre fica para revisão). Corrige os defeitos 1–4 e 11 |
| `core/candidates.py` | Usa o módulo novo; `looks_like_move` aceita `AxC`, `NPxB`, `N/Q2-B3` e formas do OCR como `B-IH` (alerta em vez de sumiço); `resolve(fuzzy=False)` pula o Levenshtein onde ele nunca seria usado |
| `core/normalizer.py` | Número sem ponto → `N.` (só antes de algo com cara de lance, e não depois de "diagrama", "page" etc.); `P x P` → `PxP`; `C(4D)5C` → `C/4D-5C`; `×` → `x`; vírgula depois de `N. ...`. Defeitos 5, 7, 9, 10 |
| `core/tokenizer.py` | Parênteses de variante contam como contexto de notação (defeito 6) |
| `core/parser.py` | `,`/`;` colados a lance não viram comentário (8); **desempate pela continuação** — entre leituras igualmente prováveis, fica a única que mantém legais os 2 lances seguintes; aviso `MOVE_NUMBER_MISMATCH` (12), relativo ao primeiro lance numerado para não disparar com FEN de diagrama |
| `services/engine_service.py` | Procura `engine/**/stockfish*.exe` (no projeto e ao lado do executável empacotado) depois do caminho configurado e da variável de ambiente (13); `scan_game` confere linha principal **e variantes** em qualquer profundidade, com `include_variations` nas configurações (14); sem sugestão por distância de SAN para lance descritivo (15) |
| `ui/main_window.py` | Mensagem de status diz se as variantes estão sendo conferidas |

Testes novos: `tests/test_descriptive_languages.py` (27), 8 em `tests/test_engine.py`,
`tests/test_stockfish_integration.py` (Stockfish real, pula sem motor), e três páginas no
corpus (`pachman_philidor_1783_en`, `panov_siciliana_es`, `pachman_tactica_diag95_es`).
O gabarito do Philidor é a partida histórica (94 lances), não a saída do programa.

## 4. Resultados

| Teste | Antes | Depois |
|---|---|---|
| Panov, Siciliana (espanhol, 4 variantes) | 0 / 40 lances | **55 / 55**, 0 erro, 0 suspeita do Stockfish |
| Pachman, diagrama 95 (espanhol, FEN, 4 variantes) | 0 lances | **43 / 43**, 0 erro, 0 aviso |
| Brühl–Philidor limpo (inglês) | 21 / 90 válidos | **94 / 94**, idêntica à partida histórica |
| 168 partidas **brutas** do OCR dos 3 livros de Pachman | 24% dos lances válidos; mediana de 1 lance certo antes do 1º erro | **60%**; mediana de 7; 24 partidas com 20+ lances limpos |

Suíte: 555 testes + 68 subtestes passando (eram 518), ruff e mypy limpos, cobertura de
`core/` em 93%. Custo por análise de uma página: 15–57 ms (a análise roda fora da
thread da interface).

Conferência com o Stockfish 19 (200 ms por posição): Panov 55 lances (42 em variantes)
em 11 s, nenhuma suspeita. No diagrama 95 ele marca três lances que são **erros reais do
Kotov na partida** (6...D×C3C?? leva mate), não erros de leitura — ver 5.3.

## 5. Segunda rodada: o que faltava, feito

As oito pendências da primeira versão deste relatório foram implementadas.

| # | Pendência | Como ficou |
|---|---|---|
| 1 | OCR de texto para PDF escaneado | `services/text_ocr.py` usa o OCR embutido do Windows 10/11 (extra `text-ocr`, pacotes `winrt-*`). O painel de PDF lê sozinho a página sem camada de texto, numa thread à parte, e o texto entra no lugar do extraído; *Ler texto (OCR)* relê. Peculiaridade medida: o OCR do Windows **descarta** palavras como `PxP`/`AxP` no meio da frase, e o que some muda com a resolução. Solução: depois da leitura da página, cada espaço largo demais entre palavras (e o fim da linha antes da margem da coluna, sem invadir a vizinha) é recortado e relido sozinho em 200/300/250 dpi. Custo: 0,2–0,4 s por página. Ler a página inteira em várias resoluções rendia o mesmo por 4× o tempo e trazia ruído do diagrama |
| 2 | Lance-ameaça na prosa | Lance sem número logo depois de "amenaza", "ameaça", "threatening", "idea", "ideia de"... fica no comentário, com alerta `CITED_MOVE` (info). "con la amenaza T1T" já não vira `Ra8` |
| 3 | Propor o lance que falta | Quando `MOVE_NUMBER_MISMATCH` indica **um** lance faltando, o parser procura o único lance legal que torna legais os 14 lances seguintes do livro. Havendo um só, vem no alerta; *Aceitar* o insere antes do número impresso (`Candidate.replacement`). No Philidor bruto do OCR ele acha o `...e5` que sumiu |
| 4 | Falso positivo do motor | Se a resposta do próprio livro aproveita a queda (a queda dela é menor que metade do limiar), o `SUSPECT_BLUNDER` vira `info`: "erro do jogador mostrado pelo livro". Os três lances do Kotov no diagrama 95 passaram a info |
| 5 | Desempate pelo motor | Lance ambíguo (`needs_review`): se só uma leitura não perde material, alerta `ENGINE_PREFERENCE` com ela como candidato — `warning` quando o parser escolheu outra, `info` quando confirma. Em `N-R4` (Dois Cavalos) aponta `Na5`, a jogada do livro |
| 6 | Interface | Caixa *Conferir também as variantes* nas Preferências; caixa **Idioma do texto** no editor, gravada no projeto; botão *Parece Espanhol — usar* quando o texto colado parece de outro idioma (`core/notation_detect.py`, nunca troca sozinho). Corrigido junto: salvar gravava `pt` no projeto mesmo lendo o texto em outro idioma, e confirmar Preferências trocava o idioma do livro aberto |
| 7 | Desempenho | A resolução descritiva gera só os lances da peça certa e, quando a casa é conhecida, só para ela. Panov: 88 → 34 ms por análise. Um cache por `(token, posição)` foi medido e descartado: a chave custava mais que a economia |
| 8 | Detalhes | `Q-R5 +` → `Q-R5+` (antes virava comentário `{+}`), só depois de algo com cara de lance — prosa fica intacta |

### Terceira rodada

| Item | Como ficou |
|---|---|
| Livro escaneado inteiro | *Ler intervalo (OCR)* no painel de PDF lê as páginas `de`..`até` e manda tudo ao editor com `{ PDF página N }`; página com texto usa o texto, escaneada passa pelo OCR. Não depende do extra de diagramas. Panov p. 40–42: 1,1 s. O *Reconhecer PDF inteiro* (processo com PyMuPDF) também passou a usar o OCR nas páginas sem camada de texto (`text_method: "ocr"`) |
| Linha inteira perdida pelo OCR | Vão entre duas linhas da mesma coluna do tamanho de uma linha (1 a 2,6 alturas) é relido; entra só o que não repete palavra vizinha e tem token de notação. Em 5 páginas reais recuperou exatamente a linha que faltava (`D1A; 8. P×P+, R×P; 9. D6A+, R1C` na *Táctica*, p. 60) e nenhum ruído — diagramas e parágrafos ficam de fora pelo tamanho do vão |
| Stockfish no instalador | Opcional: `PGN_BUNDLE_ENGINE=1` põe no pacote o `stockfish*.exe` e a licença GPL dele (Copying.txt, AUTHORS, README); o código-fonte não vai. Os módulos `winrt` do OCR entram quando instalados. Pacote construído e conferido: 221 MB, o executável acha o motor em `_internal\engine\...` e o OCR, e a janela sobe |
| Diagnóstico | O relatório diz qual motor foi achado e se o OCR está disponível; `--diagnostico ARQUIVO` grava sem abrir a janela (é como se confere um executável sem console) |

### O que continua de fora

- **Idioma do OCR**: a máquina de teste só tem o pacote pt-BR do OCR do Windows. Ele lê bem
  espanhol e inglês (alfabeto latino); instalar o pacote do idioma do livro pode melhorar
  a prosa, não a notação.
- **Estruturar a página lida**: o OCR entrega o texto na ordem de leitura; separar prosa de
  lance e variante continua sendo o *Converter e mandar* e os gestos do editor.

## 6. Como reproduzir

```powershell
cd pgn_live_editor
uv run python -m pytest tests/test_descriptive_languages.py tests/test_engine.py tests/test_stockfish_integration.py tests/test_corpus.py tests/test_text_ocr.py tests/test_diagnostics.py
```

O teste de integração usa o Stockfish de `engine/`, e o de OCR usa o OCR do Windows; sem eles, pulam. Backup do código
anterior às mudanças: `C:\Python-Chess2\PGN_Live_Editor\_backup\pgn_live_editor_src_2026-09-30.tgz`.
