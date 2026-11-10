"""Texto de livro impresso -> movetext que o editor entende.

O `paste_cleanup` resolve o **layout** da pagina: junta linha quebrada,
remonta palavra hifenizada, tira numero de pagina. Este modulo resolve a
**estrutura**, que e o que sobra e o que da trabalho de verdade: descobrir
onde comeca e termina cada variante.

Um livro de repertorio moderno nao escreve parenteses. Ele escreve rotulos:

    7...h6
    A) 8.Be3 Ng4 [8...b5 9.f3 transposes ...] 9.0-0-0 Nxe3 10.Qxe3 Nc6
    B) 8.Bh4? falls into an elementary trap: 8...Nxe4!µ
    C) 8.Bxf6 Qxf6 9.0-0-0 Nc6
    C1) 10.Nb3?! This has been the most popular move ... 10...b5 11.f4 Qd8
    C2) 10.Nxc6 bxc6 11.f4 Qd8 12.Bc4 Be7=

E o mesmo trecho em PGN e:

    7...h6 8.Be3 ( 8.Bh4 $2 8...Nxe4 $1 $17 )
    ( 8.Bxf6 Qxf6 9.0-0-0 Nc6 10.Nb3 $6 ( 10.Nxc6 bxc6 11.f4 Qd8 12.Bc4 Be7 $10 )
      10...b5 11.f4 Qd8 ) 8...Ng4 ( 8...b5 9.f3 ) 9.0-0-0 Nxe3 10.Qxe3 Nc6

Trocar um pelo outro a mao e o gargalo do fluxo. Tres regras fazem quase
tudo:

1. **O primeiro irmao continua a linha; os demais viram parenteses** -- e o
   que faz `A)` sumir e `B)`/`C)` aparecerem entre `(` e `)`. E os
   parenteses entram logo depois do **primeiro lance** de `A)`, porque em
   PGN uma variante e alternativa ao lance imediatamente anterior.
2. **`[...]` e uma variante embutida**, e o `;` dentro dela separa irmas.
3. **Prosa e comentario.** Todo trecho que nao e notacao entra em `{}` --
   caso contrario ele desaparece do PGN exportado, que e a metade do livro
   que interessa guardar.

O que **nao** da para decidir sem tabuleiro esta em `notes`, nunca em
silencio: a SPEC (secao 0, principio 3) manda duvidar em voz alta.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .delimiters import comment_spans, in_comment
from .fen_tools import DIAGRAM_MARK
from .nag_table import SEARCHABLE_GLYPHS, map_book_symbols
from .normalizer import comment_span_end

# ----------------------------------------------------------------------
# Reconhecimento de notacao
# ----------------------------------------------------------------------

_GLYPH_ALT = "|".join(re.escape(glyph) for glyph in SEARCHABLE_GLYPHS)
_NAG_ALT = rf"(?:\$\d+|!!|\?\?|!\?|\?!|[!?]|{_GLYPH_ALT})"

_SAN_ALT = (
    r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[KQRBN])?[+#]?"
    r"|[O0]-[O0]-[O0][+#]?"
    r"|[O0]-[O0][+#]?"
)

# Um "atomo" e um lance com o numero opcional grudado e as anotacoes que o
# seguem: `10.Nb3?!`, `8...Nxe4!∓`, `O-O`. As bordas `(?<![\w-])`/`(?![\w-])`
# sao o que impede `d4-square` e `Nothing` de virarem lance.
_ATOM = rf"(?<![\w-])(?:(?P<num>\d{{1,3}}\s*\.{{1,3}})\s*)?(?P<san>{_SAN_ALT})(?P<nags>(?:{_NAG_ALT})*)(?![\w-])"

_TOKEN_RE = re.compile(
    r"(?P<brace>\{[^}]*\})"
    r"|(?P<paren>[()])"
    rf"|(?P<atom>{_ATOM})"
    r"|(?P<number>(?<![\w.])\d{1,3}\s*\.{1,3}(?![\w.]))"
    rf"|(?P<nag>{_NAG_ALT})"
    r"|(?P<space>\s+)"
    r"|(?P<other>[^\s(){}]+)"
)

#: Rotulo de variante da Thinkers/Quality/Everyman: `A)`, `C2)`, `B1.2)`.
LABEL_RE = re.compile(r"^([A-Z]\d*(?:\.\d+)*)\)\s+")

#: `□ 1.?` / `■ 1...?` -- a pergunta de um exercicio, com o lado que joga.
EXERCISE_RE = re.compile(r"^([□■])\s*1\s*\.{1,3}\s*\?$")

_CHAPTER_HEADER_RE = re.compile(r"^Chapter\s+\d+$", re.IGNORECASE)
_PART_HEADER_RE = re.compile(r"^Part\s+[IVXLC]+$", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_POSITION_AFTER_RE = re.compile(r"^(?:Position after|Posi[cç][aã]o ap[oó]s)\s*:", re.IGNORECASE)

_FURNITURE_LINES = frozenset(
    {
        "show/hide solution",
        "show in text mode",
        "chapter guide",
        "table of contents",
        "key to symbols",
    }
)

#: Restos de hifenizacao que o extrator de PDF nao soube decodificar. O
#: `double￾edged` do Najdorf era `double-edged` na pagina impressa.
_BROKEN_HYPHEN_RE = re.compile(r"(?<=\w)[￾�­](?=\w)")

#: `[Event "..."]`, `[%cal ...]` e o marcador de diagrama `{[#]}` nao sao
#: variantes embutidas. Sem esta excecao, converter um documento que ja tem
#: cabecalhos os destruiria.
_PROTECTED_BRACKET_RE = re.compile(r"^\[\s*(?:%|#\]|[A-Z][A-Za-z]*\s+\")")


DIAGRAM_REMOVE = "remove"
DIAGRAM_COMMENT = "comment"


@dataclass
class BookImportOptions:
    map_symbols: bool = True
    repair_broken_hyphens: bool = True
    strip_furniture: bool = True
    #: O que fazer com `Position after: 15...a5`: sumir, ou virar `{[#]}`.
    diagram_marks: str = DIAGRAM_REMOVE
    labels_to_variations: bool = True
    brackets_to_variations: bool = True
    prose_to_comments: bool = True
    exercises_to_games: bool = True
    #: `8.0-0-0` seguido de `8.f4!?` e uma alternativa, nao uma continuacao.
    #: E heuristica -- por isso cada uso vira uma nota no relatorio.
    regressions_to_variations: bool = True


@dataclass
class BookImportReport:
    text: str
    stats: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        pieces = [
            ("symbols", "{n} símbolo(s) de livro convertido(s)"),
            ("labels", "{n} rótulo(s) de variante"),
            ("brackets", "{n} variante(s) entre colchetes"),
            ("comments", "{n} trecho(s) de prosa viraram comentário"),
            ("citations", "{n} lance(s) citado(s) na frase devolvido(s) à prosa"),
            ("furniture", "{n} linha(s) de página descartada(s)"),
            ("diagrams", "{n} marcador(es) de diagrama"),
            ("exercises", "{n} exercício(s) viraram partidas"),
            ("regressions", "{n} variante(s) deduzida(s) pela numeração"),
        ]
        parts = [template.format(n=self.stats[key]) for key, template in pieces if self.stats[key]]
        return " | ".join(parts) if parts else "Nada a converter."


# ----------------------------------------------------------------------
# Etapa 1 -- linhas: mobiliario de pagina, diagramas, exercicios
# ----------------------------------------------------------------------


@dataclass
class _Chunk:
    """Um paragrafo logico: ou a linha principal, ou um rotulo do livro."""

    label: str
    text: str
    #: Separador de partida a emitir **antes** deste trecho (exercicios).
    separator: str = ""


def _strip_lines(text: str, options: BookImportOptions, report: BookImportReport) -> list[str]:
    """Tira o que e da pagina impressa e marca onde comeca cada exercicio."""
    kept: list[str] = []
    pending_number = ""
    pending_chapter = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            kept.append("")
            continue

        exercise = EXERCISE_RE.match(line)
        if exercise and options.exercises_to_games:
            side = "brancas jogam" if exercise.group(1) == "□" else "pretas jogam"
            title = " — ".join(
                part for part in (f"Exercício {pending_number}" if pending_number else "", pending_chapter) if part
            )
            kept.append("")
            kept.append(f"=== {title or 'Exercício'} ===")
            kept.append(f"{{{title or 'Exercício'} — {side}}}")
            report.stats["exercises"] += 1
            pending_number = pending_chapter = ""
            continue

        if not options.strip_furniture:
            kept.append(line)
            continue

        if line.lower() in _FURNITURE_LINES:
            report.stats["furniture"] += 1
            continue

        if _POSITION_AFTER_RE.match(line):
            report.stats["diagrams"] += 1
            if options.diagram_marks == DIAGRAM_COMMENT:
                kept.append(DIAGRAM_MARK)
            continue

        if _CHAPTER_HEADER_RE.match(line):
            # Cabecalho corrente da pagina -- mas nos exercicios ele diz de
            # que capitulo veio a posicao, e isso vale guardar.
            pending_chapter = line
            report.stats["furniture"] += 1
            continue

        if _PART_HEADER_RE.match(line):
            report.stats["furniture"] += 1
            continue

        if _BARE_NUMBER_RE.match(line):
            # Numero de pagina, ou o numero do exercicio. A ordem impressa
            # distingue: o do exercicio vem **antes** do "Chapter N" de onde
            # a posicao saiu, o da pagina vem depois. Nas duas leituras o
            # numero sai do movetext.
            if not pending_chapter:
                pending_number = line
            report.stats["furniture"] += 1
            continue

        kept.append(line)

    return kept


#: Uma coluna de livro tem largura fixa. Abaixo disto o texto nao veio de
#: uma pagina diagramada e nao ha margem direita para consultar.
_MIN_COLUMN_WIDTH = 60
#: Folga para a palavra que nao coube na linha.
_MARGIN_SLACK = 10
#: Folga maior, valida so quando a linha nem sequer termina a frase --
#: paragrafo nenhum acaba no meio de uma oracao.
_OPEN_SENTENCE_SLACK = 25

_SENTENCE_END_RE = re.compile(r"[.!?:;\"'\]\)=]$")


def _reaches_margin(line: str, width: int) -> bool:
    """A linha bateu na margem direita -- ou seja, ela continua na proxima.

    E o unico sinal confiavel que a extracao de PDF preserva. `7...Be7`
    sozinho numa linha e um lance da linha principal; `...to occupy the
    d4-square.] 16...Qb6 17.f5 Nxd4 18.Nxd4 e5 19.Nb5 Rd8`, com quase a
    mesma quantidade de notacao, e o meio de um paragrafo. O que separa os
    dois nao e o conteudo, e o comprimento.
    """
    length = len(line)
    if length >= width - _MARGIN_SLACK:
        return True
    return length >= width - _OPEN_SENTENCE_SLACK and not _SENTENCE_END_RE.search(line)


def _is_double_spaced(lines: list[str]) -> bool:
    """Quase todo par de linhas com texto e separado por linha em branco?"""
    gaps = blank_gaps = 0
    seen_text = pending_blank = False
    for line in lines:
        if not line.strip():
            pending_blank = seen_text
            continue
        if seen_text:
            gaps += 1
            blank_gaps += int(pending_blank)
        seen_text, pending_blank = True, False
    return gaps >= 3 and blank_gaps >= 0.8 * gaps


def join_wrapped_lines(lines: list[str]) -> list[str]:
    """Remonta os paragrafos quebrados pela largura da coluna impressa."""
    meaningful = [line for line in lines if line.strip()]
    width = max((len(line) for line in meaningful), default=0)
    if width < _MIN_COLUMN_WIDTH:
        # Trecho pequeno, ou texto digitado a mao: sem margem para consultar,
        # cada linha vale por um paragrafo. Juntar seria adivinhar.
        return meaningful

    joined: list[str] = []
    # A pergunta e sempre sobre a **ultima linha fisica**, nunca sobre o
    # paragrafo ja remontado: juntar duas linhas cheias da uma cadeia com o
    # dobro da largura, e a partir dai tudo "bate na margem" e o documento
    # inteiro vira um paragrafo so.
    last_physical = ""
    # Linha em branco e fim de paragrafo: antes ela era jogada fora e so a
    # margem decidia -- depois da linha mais larga do trecho, o paragrafo
    # seguinte (uma regressao `8.f4 ...`) era colado nela (L-03). A excecao e
    # o texto em espaco duplo (PDF que poe linha em branco entre **todas** as
    # linhas, ate no meio da frase): ali uma linha em branco sozinha nao diz
    # nada, e so duas ou mais seguidas separam paragrafos.
    breaking_run = 2 if _is_double_spaced(lines) else 1
    after_blank = False
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            after_blank = after_blank or blank_run >= breaking_run
            continue
        blank_run = 0
        # Um rotulo ou um separador **sempre** abre paragrafo, por mais que a
        # linha anterior tenha batido na margem. Sem esta guarda, o `B)` que
        # vem depois de uma linha cheia era engolido por ela e o grupo inteiro
        # perdia um nivel.
        starts_block = bool(LABEL_RE.match(line)) or (line.startswith("===") and line.endswith("==="))
        # Colchete aberto nao atravessa paragrafo. `15.Bxf6 [15.Bb7 Rb8=` cai
        # exatamente na folga da margem e termina num `=`, que sao os dois
        # sinais de fim de paragrafo -- mas a variante embutida continua na
        # linha de baixo, e o `]` dela ficaria orfao.
        open_bracket = joined and joined[-1].count("[") > joined[-1].count("]")
        if joined and not after_blank and not starts_block and (open_bracket or _reaches_margin(last_physical, width)):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
        last_physical = line
        after_blank = False
    return joined


def _to_chunks(lines: list[str]) -> list[_Chunk]:
    """Junta as linhas em paragrafos logicos, cortando em cada rotulo."""
    chunks: list[_Chunk] = []
    pending_separator = ""

    for line in join_wrapped_lines(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("===") and stripped.endswith("==="):
            pending_separator = stripped
            continue

        match = LABEL_RE.match(stripped)
        label, body = (match.group(1), stripped[match.end() :]) if match else ("", stripped)
        if body.strip():
            chunks.append(_Chunk(label=label, text=body, separator=pending_separator))
            pending_separator = ""

    return chunks


# ----------------------------------------------------------------------
# Etapa 2 -- dentro do paragrafo: colchetes e prosa
# ----------------------------------------------------------------------


def _brackets_to_parens(text: str, report: BookImportReport) -> str:
    """`[A; B]` -> `( A ) ( B )`, sem tocar em `[Event "..."]`, `[%cal]` nem em comentario.

    O colchete protegido e o comentario sao copiados **inteiros**: antes o `[`
    de um `[%cal Ge4]` era poupado mas o `]` dele virava `)` e fechava a
    variante no meio, e colchetes dentro de `{...}` viravam variante (L-16).
    """
    out: list[str] = []
    depth = 0
    index = 0

    while index < len(text):
        char = text[index]

        if char == "{":
            end = comment_span_end(text, index)
            out.append(text[index:end])
            index = end
            continue

        if char == "[" and _PROTECTED_BRACKET_RE.match(text[index:]):
            close = text.find("]", index)
            end = close + 1 if close != -1 else len(text)
            out.append(text[index:end])
            index = end
            continue

        if char == "[":
            depth += 1
            report.stats["brackets"] += 1
            out.append("(")
        elif char == "]" and depth > 0:
            depth -= 1
            out.append(")")
        elif char == ";" and depth > 0:
            # Dentro de colchete, `;` separa linhas irmas -- fecha uma e
            # abre a proxima, no mesmo ponto de ancoragem.
            out.append(") (")
            report.stats["brackets"] += 1
        else:
            out.append(char)
        index += 1

    if depth:
        report.notes.append(f"{depth} colchete(s) sem fechamento; foram fechados no fim do parágrafo.")
        out.append(")" * depth)

    return "".join(out)


@dataclass
class _Tok:
    kind: str  # 'notation' | 'san' | 'nag' | 'prose'
    text: str


#: A ordem importa: e a mesma da alternancia de `_TOKEN_RE`. `lastgroup` nao
#: serve aqui porque `atom` tem grupos nomeados dentro dele e o `re` devolve
#: o mais interno que casou, nao o da alternativa.
_TOKEN_GROUPS = ("brace", "paren", "atom", "number", "nag", "space", "other")


def _matched_group(match: re.Match) -> str:
    for name in _TOKEN_GROUPS:
        if match.group(name) is not None:
            return name
    return "other"


def _classify(text: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    for match in _TOKEN_RE.finditer(text):
        kind = _matched_group(match)
        value = match.group(0)

        if kind == "space":
            continue
        if kind in {"brace", "paren", "number"}:
            tokens.append(_Tok("notation", value))
        elif kind == "atom":
            # Numero de lance grudado nao deixa duvida; lance solto ainda
            # pode ser uma casa citada na prosa ("thanks to the e5 weakness").
            tokens.append(_Tok("notation" if match.group("num") else "san", value))
        elif kind == "nag":
            tokens.append(_Tok("nag", value))
        else:
            tokens.append(_Tok("prose", value))
    return tokens


def _promote_adjacent_moves(tokens: list[_Tok]) -> None:
    """Um lance solto e notacao se **encostar** em notacao.

    E o que separa `Nfd7` de `9...dxe5 10.fxe5 Nfd7` -- que e lance -- de
    `Nxe6` em "White threatens both Nxe6 and Nd5" -- que e prosa. Sozinha,
    nenhuma regra local acerta os dois; a vizinhanca acerta.
    """
    changed = True
    while changed:
        changed = False
        for index, token in enumerate(tokens):
            if token.kind != "san":
                continue

            previous = next((tokens[i] for i in range(index - 1, -1, -1) if tokens[i].kind != "nag"), None)
            following = next((tokens[i] for i in range(index + 1, len(tokens)) if tokens[i].kind != "nag"), None)
            if (previous and previous.kind == "notation") or (following and following.kind == "notation"):
                token.kind = "notation"
                changed = True

    for token in tokens:
        if token.kind == "san":
            token.kind = "prose"

    # Um `!` ou um `=` herda o que vier antes dele: depois de lance e
    # avaliacao, depois de prosa e pontuacao.
    for index, token in enumerate(tokens):
        if token.kind != "nag":
            continue
        previous = tokens[index - 1] if index else None
        token.kind = previous.kind if previous and previous.kind in {"notation", "prose"} else "prose"


#: Ate quantos lances seguidos, cercados de prosa dos dois lados, ainda sao
#: uma **citacao** e nao uma linha. "transposes to the 6.Be3 e6 variation"
#: tem dois; uma linha de verdade tem cinco, dez, vinte.
_CITATION_RUN_LIMIT = 2

#: Ate quantas palavras antes de um lance ainda sao um conector de variante
#: ("After", "or", "e.g.", "with the idea of") e nao uma frase.
_CONNECTOR_WORD_LIMIT = 3


def _demote_citations(tokens: list[_Tok], report: BookImportReport) -> None:
    """Lance citado no meio de uma frase volta a ser prosa.

    O livro escreve "transposes to the 6.Be3 e6 variation" e "White
    threatens both Nxe6 and Nd5": ali `6.Be3` e `Nxe6` nao sao lances a
    jogar, sao o assunto da frase. Deixa-los como lance quebra a linha e
    enche o painel de alertas; devolve-los a prosa preserva o texto inteiro
    dentro do comentario, que e o modo de errar que nao perde nada.

    Duas condicoes contem a regra, e as duas vieram de contra-exemplos do
    proprio livro:

    - a linha ja tem de ter **comecado** naquele nivel. `[After 16.Bg2?!
      e5!µ White doesn't...]` tem prosa dos dois lados e mesmo assim e uma
      variante de verdade -- porque `16.Bg2` e o primeiro lance dali.
    - a borda conta como notacao: `B) 8.Bh4? falls into a trap: 8...Nxe4!µ`
      termina em lance, e lance no fim do paragrafo nao e citacao.
    """
    started: list[bool] = [False]
    index = 0

    while index < len(tokens):
        token = tokens[index]

        if token.kind == "notation" and token.text == "(":
            started.append(False)
            index += 1
            continue
        if token.kind == "notation" and token.text == ")":
            if len(started) > 1:
                started.pop()
            index += 1
            continue
        if token.kind != "notation":
            index += 1
            continue

        end = index
        while end < len(tokens) and tokens[end].kind == "notation" and tokens[end].text not in "()":
            end += 1

        run = tokens[index:end]
        surrounded = (
            index > 0 and end < len(tokens) and tokens[index - 1].kind == "prose" and tokens[end].kind == "prose"
        )
        # Frase inteira antes do lance quer dizer que ele e assunto, nao
        # continuacao: "Placing the queen on d2 in the 6.Bg5 Najdorf..." e
        # prosa da primeira palavra a ultima. Ate tres palavras ainda e
        # conector de variante -- "After", "or", "e.g.", "with the idea".
        leading_words = 0
        cursor = index - 1
        while cursor >= 0 and tokens[cursor].kind == "prose":
            leading_words += 1
            cursor -= 1

        opened_by_prose = leading_words > _CONNECTOR_WORD_LIMIT
        if surrounded and (started[-1] or opened_by_prose) and len(run) <= _CITATION_RUN_LIMIT:
            for item in run:
                item.kind = "prose"
            report.stats["citations"] += 1
        elif run:
            started[-1] = True

        index = max(end, index + 1)


_HAS_LETTER_RE = re.compile(r"[^\W\d_]")


def _tidy_prose(body: str) -> str:
    """Junta as palavras sem deixar espaco antes de pontuacao.

    A pontuacao do fim da frase anterior costuma sobrar na frente do
    comentario (`Ne5=. The position is...` deixa um `.` orfao); ela sai.
    """
    # O `(?!\.)` preserva a reticencia de lance das pretas dentro da prosa:
    # "the 'free' move ...h6" nao pode virar "move...h6".
    body = re.sub(r"\s+([,;:!?%])", r"\1", body)
    body = re.sub(r"\s+\.(?!\.)", ".", body)
    body = re.sub(r"^[\s.,;:]+", "", body)
    return re.sub(r"\s{2,}", " ", body).strip()


def _wrap_prose(text: str, report: BookImportReport) -> str:
    """Envolve em `{}` todo trecho que nao e notacao."""
    tokens = _classify(text)
    _promote_adjacent_moves(tokens)
    _demote_citations(tokens, report)

    parts: list[str] = []
    prose_run: list[str] = []

    def flush_prose():
        if not prose_run:
            return
        body = _tidy_prose(" ".join(prose_run))
        prose_run.clear()
        if not _HAS_LETTER_RE.search(body):
            # Pontuacao solta nao merece um comentario so para ela.
            if body:
                parts.append(body)
            return
        # `{` e `}` dentro do comentario fechariam-no no meio: viram
        # parenteses, que e o que o PGN deixa passar.
        parts.append("{" + body.replace("{", "(").replace("}", ")") + "}")
        report.stats["comments"] += 1

    for token in tokens:
        if token.kind == "prose":
            prose_run.append(token.text)
        else:
            flush_prose()
            parts.append(token.text)

    flush_prose()
    return " ".join(parts)


# ----------------------------------------------------------------------
# Etapa 3 -- a arvore de rotulos
# ----------------------------------------------------------------------


def label_lineage(label: str) -> list[str]:
    """`"B1.2"` -> `["B", "B1", "B1.2"]` -- a cadeia de rotulos ate a raiz."""
    head, rest = label[0], label[1:]
    lineage = [head]
    if not rest:
        return lineage

    accumulated = head
    for index, piece in enumerate(rest.split(".")):
        accumulated = f"{accumulated}{piece}" if index == 0 else f"{accumulated}.{piece}"
        lineage.append(accumulated)
    return lineage


@dataclass
class _Node:
    label: str
    text: str = ""
    children: list[_Node] = field(default_factory=list)


def _first_move_split(text: str) -> tuple[str, str]:
    """Separa o primeiro lance do resto.

    Em PGN a variante e alternativa ao lance **anterior** a ela, entao as
    irmas de um grupo tem de entrar logo depois do primeiro lance da linha
    que continua -- e nao no fim dela.
    """
    for match in _TOKEN_RE.finditer(text):
        if _matched_group(match) == "atom":
            return text[: match.end()].strip(), text[match.end() :].strip()
    return text.strip(), ""


def _render_group(children: list[_Node]) -> str:
    if not children:
        return ""

    first, *siblings = children
    head, tail = _first_move_split(first.text)

    parts = [head]
    parts.extend(f"( {_render_node(sibling)} )" for sibling in siblings)
    parts.append(tail)
    parts.append(_render_group(first.children))
    return " ".join(part for part in parts if part)


def _render_node(node: _Node) -> str:
    return " ".join(part for part in (node.text, _render_group(node.children)) if part)


# ----------------------------------------------------------------------
# Etapa 4 -- a espinha: onde cada paragrafo sem rotulo se encaixa
# ----------------------------------------------------------------------

_LEADING_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3})\s*(\.{1,3})")
_BRACE_SPAN_RE = re.compile(r"\{[^}]*\}")


def _outside_comments(text: str) -> str:
    """O trecho sem os comentarios -- `{... 6.Bg5 ...}` nao e lance nenhum."""
    return _BRACE_SPAN_RE.sub(" ", text)


def _first_move_number(text: str) -> tuple[int, bool] | None:
    """`(numero, e_das_brancas)` do primeiro lance numerado do trecho."""
    match = _LEADING_NUMBER_RE.search(_outside_comments(text))
    if not match:
        return None
    return int(match.group(1)), len(match.group(2)) == 1


def _last_move_number(text: str) -> tuple[int, bool] | None:
    matches = list(_LEADING_NUMBER_RE.finditer(_outside_comments(text)))
    if not matches:
        return None
    last = matches[-1]
    return int(last.group(1)), len(last.group(2)) == 1


def _continues(previous: tuple[int, bool] | None, candidate: tuple[int, bool] | None) -> bool:
    """O trecho continua a linha, ou repete um lance que ela ja teve?

    Repetir e o sinal de alternativa: um livro escreve `8.0-0-0` e, logo
    abaixo, `8.f4!?` -- que nao e o nono lance, e outro oitavo.
    """
    if previous is None or candidate is None:
        return True

    number, is_white = candidate
    last_number, last_is_white = previous
    if number > last_number:
        return True
    if number < last_number:
        return False
    # Mesmo numero: so as pretas continuam depois das brancas.
    return last_is_white and not is_white


# ----------------------------------------------------------------------
# Montagem
# ----------------------------------------------------------------------


def convert_book_text(text: str, options: BookImportOptions | None = None) -> BookImportReport:
    """Converte o texto de um livro no movetext que o editor analisa."""
    options = options or BookImportOptions()
    report = BookImportReport(text=text)

    if options.map_symbols:
        text, converted = map_book_symbols(text)
        report.stats["symbols"] = converted

    if options.repair_broken_hyphens:
        text, repaired = _BROKEN_HYPHEN_RE.subn("-", text)
        if repaired:
            report.notes.append(f"{repaired} hífen(s) quebrado(s) pelo extrator de PDF remontado(s).")

    lines = _strip_lines(text, options, report)
    chunks = _to_chunks(lines)

    if not chunks:
        report.text = ""
        return report

    for chunk in chunks:
        body = chunk.text
        if options.brackets_to_variations:
            body = _brackets_to_parens(body, report)
        if options.prose_to_comments:
            body = _wrap_prose(body, report)
        chunk.text = re.sub(r"\s{2,}", " ", body).strip()
        if chunk.label:
            report.stats["labels"] += 1

    if not options.labels_to_variations:
        report.text = "\n\n".join(" ".join(part for part in (chunk.separator, chunk.text) if part) for chunk in chunks)
        return report

    report.text = _assemble(chunks, options, report)
    return report


_NUMBERED_TOKEN_RE = re.compile(r"^(\d+)\.(\.\.)?(.*)$")
_NOT_A_MOVE_TOKEN_RE = re.compile(r"^(?:\$\d+|[!?]{1,2}|1-0|0-1|1/2-1/2|\*|[^\w].*)$")


def _split_after_ply(text: str, number: int, is_black: bool) -> tuple[str, str] | None:
    """`text` partido logo depois do lance `number` (das brancas ou pretas), ou None.

    So olha a linha do proprio paragrafo: o que esta em comentario ou dentro de
    parenteses nao conta. NAG colado ou logo depois do lance fica com ele.
    """
    target = 2 * (number - 1) + int(is_black)
    spans = comment_spans(text)
    next_ply: int | None = None
    depth = 0
    tokens = list(re.finditer(r"\S+", text))
    for position, match in enumerate(tokens):
        token = match.group()
        if in_comment(spans, match.start()):
            continue
        opened, closed = token.count("("), token.count(")")
        if depth or opened:
            depth = max(0, depth + opened - closed)
            continue
        numbered = _NUMBERED_TOKEN_RE.match(token)
        if numbered:
            next_ply = 2 * (int(numbered.group(1)) - 1) + int(bool(numbered.group(2)))
            token = numbered.group(3)
            if not token:
                continue
        if next_ply is None or _NOT_A_MOVE_TOKEN_RE.match(token):
            continue
        if next_ply == target:
            end = match.end()
            for following in tokens[position + 1 :]:
                if not re.fullmatch(r"\$\d+|[!?]{1,2}", following.group()):
                    break
                end = following.end()
            return text[:end], text[end:]
        next_ply += 1
    return None


def _assemble(chunks: list[_Chunk], options: BookImportOptions, report: BookImportReport) -> str:
    """Monta o documento: espinha sem rotulo + arvore de rotulos em cada no."""
    documents: list[str] = []
    pieces: list[str] = []
    # Cada nivel aberto guarda o ultimo numero de lance que viu. E o que
    # permite saber, quando um paragrafo sem rotulo reaparece, a qual linha
    # ele esta voltando -- `8...b5` depois de uma variante que chegou ao
    # lance 19 nao continua a variante, volta a linha principal.
    open_levels: list[tuple[int, bool] | None] = [None]
    # O resto do paragrafo que a regressao interrompeu (`d6 3.d4 cxd4`), um por
    # nivel aberto: ele volta logo depois do `)` (R-211).
    tails: list[str] = []

    def close_level() -> None:
        open_levels.pop()
        pieces.append(")")
        tail = tails.pop().strip()
        if tail:
            pieces.append(tail)

    index = 0
    while index < len(chunks):
        chunk = chunks[index]

        if chunk.separator:
            while len(open_levels) > 1:
                close_level()
            if pieces:
                documents.append(" ".join(pieces))
                pieces = []
            open_levels = [None]
            documents.append(chunk.separator)

        if chunk.label:
            # Rotulo sem paragrafo dono antes: a pagina colada comecou no
            # meio de um grupo. Vale como linha solta, no nivel corrente.
            index, rendered, tail = _consume_label_group(chunks, index, report)
            if rendered:
                pieces.append(rendered)
                open_levels[-1] = tail or open_levels[-1]
            continue

        candidate = _first_move_number(chunk.text)
        if options.regressions_to_variations and candidate is not None:
            while len(open_levels) > 1 and not _continues(open_levels[-1], candidate):
                close_level()

            if not _continues(open_levels[-1], candidate):
                # A variante vem logo depois do lance que ela repete -- o lugar
                # padrao do PGN. Antes o `(` ia para o fim do paragrafo
                # anterior (`3.d4 cxd4 ( 2.Nc3 ... )`), e so o parser tolerante
                # entendia (L-18). Sem achar o lance ali, fica como era.
                number, is_white = candidate
                split = _split_after_ply(pieces[-1], number, is_black=not is_white) if pieces else None
                tail = ""
                if split is not None:
                    pieces[-1], tail = split
                pieces.append("(")
                open_levels.append(None)
                tails.append(tail)
                report.stats["regressions"] += 1
                excerpt = chunk.text[:38].strip()
                report.notes.append(
                    f"'{excerpt}…' repete o lance {candidate[0]}: lido como alternativa, não como continuação."
                )

        pieces.append(chunk.text)
        open_levels[-1] = _last_move_number(chunk.text) or open_levels[-1]

        index, rendered, tail = _consume_label_group(chunks, index + 1, report)
        if rendered:
            pieces.append(rendered)
            open_levels[-1] = tail or open_levels[-1]

    while len(open_levels) > 1:
        close_level()
    if pieces:
        documents.append(" ".join(pieces))

    body = "\n\n".join(part for part in documents if part.strip())
    return re.sub(r"[ \t]{2,}", " ", body).strip()


def _group_tail_number(children: list[_Node]) -> tuple[int, bool] | None:
    """Ultimo numero da linha que o grupo **continua** (a cadeia de primeiros).

    Os irmaos viram parenteses e nao contam: quem segue depois do grupo e a
    linha principal dele.
    """
    node: _Node | None = children[0] if children else None
    last: tuple[int, bool] | None = None
    while node is not None:
        last = _last_move_number(node.text) or last
        node = node.children[0] if node.children else None
    return last


def _consume_label_group(
    chunks: list[_Chunk], start: int, report: BookImportReport
) -> tuple[int, str, tuple[int, bool] | None]:
    """Le a sequencia de rotulos que segue um paragrafo e a rende de uma vez."""
    index = start
    root = _Node(label="")
    by_label: dict[str, _Node] = {}

    last_node: _Node | None = None

    while index < len(chunks) and not chunks[index].separator:
        chunk = chunks[index]

        if not chunk.label:
            # Paragrafo sem rotulo depois de um rotulo: ou ele **continua**
            # aquela variante -- o livro quebra a linha na virada da pagina o
            # tempo todo -- ou e a volta a linha principal. A numeracao
            # decide: `15.g4 a5` seguido de `16.Nbd4` continua; seguido de
            # `8.0-0-0` nao continua coisa nenhuma, esta voltando.
            if last_node is None:
                break
            if not _continues(_last_move_number(last_node.text), _first_move_number(chunk.text)):
                break
            last_node.text = f"{last_node.text} {chunk.text}".strip()
            index += 1
            continue

        lineage = label_lineage(chunk.label)
        parent = by_label.get(lineage[-2]) if len(lineage) > 1 else root

        if parent is None:
            # `B1.2)` sem `B1)` antes: o livro pulou um nivel, ou a pagina
            # colada comecou no meio. Pendura no ancestral mais proximo que
            # exista, e diz que fez isso.
            parent = next((by_label[name] for name in reversed(lineage[:-1]) if name in by_label), root)
            owner = parent.label or "linha principal"
            report.notes.append(f"Rótulo {chunk.label}) apareceu sem o nível acima; foi ligado a {owner}.")

        node = _Node(label=chunk.label, text=chunk.text)
        parent.children.append(node)
        by_label[chunk.label] = node
        last_node = node
        index += 1

    if not root.children:
        return index, "", None

    return index, _render_group(root.children), _group_tail_number(root.children)
