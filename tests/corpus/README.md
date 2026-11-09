# Corpus de regressão

Páginas de livro em texto bruto, com o PGN que o programa **tem** que produzir a
partir delas. É o único teste que roda o programa na direção em que ele é usado
de verdade — texto colado → PGN — e por isso é o que mede a métrica que importa
(SPEC §6.3): **zero lances perdidos em silêncio.**

Cada entrada são dois arquivos, mais um terceiro opcional:

| Arquivo | Papel |
|---|---|
| `nome.txt` | o texto bruto, como sai do PDF: notação no idioma do livro, linhas quebradas por largura de coluna, número de página, cabeçalho corrente |
| `nome.expected.pgn` | o PGN esperado. Vale a **lista de lances e a contagem de nós**, não o texto: comentário e formatação não entram na comparação |
| `nome.json` | opcional. `{"locale": "pt", "cleanup": true, "games": 2}` |

Opções do `.json`:

- `locale` — idioma da notação (padrão `pt`).
- `cleanup` — se `true`, o texto passa por `clean_pdf_text` antes da análise,
  que é o passo 2 do fluxo do README. Use quando a página tem artefato de
  página no meio do texto.
- `book_import` — se `true`, o texto passa por `convert_book_text` (SPEC §3.6):
  rótulos `A)`/`B1.2)` viram variantes, `[ … ]` vira variante, a prosa vira
  comentário. Use para página de livro de repertório. Substitui `cleanup`.
- `games` — quantas partidas o texto contém (padrão: o que houver no
  `.expected.pgn`).
- `allow_errors` — número de alertas de nível `error` tolerados. O padrão é
  **zero**, e é assim que deve continuar: um lance que o programa não entende
  tem que aparecer no painel, mas uma página de corpus que gera erro é uma
  página que o programa ainda não sabe ler.

## Como adicionar uma página real

1. Cole a página no programa e transcreva até o PGN ficar certo.
2. Salve o texto bruto em `nome.txt` e o PGN conferido em `nome.expected.pgn`.
3. Rode `uv run python -m unittest tests.test_corpus`.

Não há passo 4: o runner acha os arquivos sozinho. Uma página que quebra hoje
vale mais que dez que passam — é ela que diz o que falta implementar.

## O que cada entrada de hoje cobre

| Entrada | Por que está aqui |
|---|---|
| `smyslov_rudakovsky_1945` | página inteira em notação portuguesa (`Cf3`, `Dc7`, `Th1`, `Rg1`), variantes aninhadas, marcas de diagrama, três quebras de página com cabeçalho corrente. É a transcrição real de `Trabalhos Salvos/Partida_13.pgn` |
| `final_de_torre` | fragmento que parte de um diagrama: só existe com `[FEN]` + `[SetUp]` |
| `capitulo_duas_partidas` | duas partidas na mesma colagem, a segunda sem cabeçalho, separadas por `=== Partida 2 ===` |
| `prosa_do_livro` | prosa solta em português **fora** de chaves, com as palavras que já corromperam o texto no passado (`chances`, `Cada`, `Chega`, `mate`) |
| `najdorf_bg5_7qd2` | três páginas cruas de *The Najdorf Bg5 Revisited* (Thinkers Publishing 2021): rótulos `A)`/`C1)`/`C2)`, colchetes, símbolos da fonte do livro (`²`, `³`, `µ`, `+–`), legendas de diagrama e números de página. O gabarito é o **PGN que a editora distribui** — 84 nós, árvore idêntica |
| `pachman_philidor_1783_en` | notação descritiva **inglesa** como o OCR entrega: números sem ponto (`1 P-K4 P-K4 2 B-B4`), captura com espaços (`P x P`), colunas sem ala (`N-B3`, `B-N5`), peças qualificadas (`QN-Q2`, `KR-B1`, `NPxB`). O gabarito é a partida histórica Brühl–Philidor, Londres 1783 (94 lances), não a saída do programa |
| `panov_siciliana_es` | notação descritiva **espanhola** (*ABC de las aperturas*, p. 38): `A` de alfil, colunas `AD`/`AR`, `×`, `2. ..., C3AR` com vírgula, `,`/`;` entre lances, origem entre parênteses `C(4D)5C`, prosa espanhola dentro das variantes |
| `pachman_tactica_diag95_es` | fragmento a partir de diagrama (`[FEN]`) de *Táctica moderna en ajedrez*: captura com casa da vítima (`D×C3C`), xeque anunciado que desempata (`A×P+`), variantes aninhadas com prosa |
