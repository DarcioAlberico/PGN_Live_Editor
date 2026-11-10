"""Tabela unica de NAG: codigo, glifo, nome e os apelidos que o PDF produz.

Tres partes do programa precisavam saber a mesma coisa e sabiam pedacos
diferentes dela: o tokenizador (quais glifos sao NAG), o renderizador
(glifo -> `$n` na exportacao) e a paleta da interface (o que oferecer ao
usuario). Cada uma tinha a sua lista, e as tres estavam incompletas -- o
`+-` do livro virava `$16` ("clara vantagem") em vez de `$18` ("ganho"), e
`Rxc6©` nem chegava a ser lance. Aqui elas passam a ler da mesma tabela.

Duas coisas nao obvias moram neste modulo:

**Os simbolos do livro nao sobrevivem ao NFKC.** A fonte de simbolos da
Thinkers Publishing (e da Quality Chess, e da Everyman) reaproveita
codepoints latinos: `²` e "brancas um pouco melhor", `³` e o mesmo para as
pretas, `µ` e "pretas claramente melhor", `¹` e "melhor e". O normalizador
faz NFKC, e o NFKC transforma `²` em `2`, `³` em `3`, `¹` em `1` e `…` em
`...`. Ou seja: colar a pagina crua **destroi** a avaliacao antes de
qualquer analise -- `Rad8³` chega ao tokenizador como `Rad83`. Por isso
`BOOK_SYMBOL_ALIASES` tem de ser aplicado no texto bruto, antes de tudo.

**Nem todo NAG cabe num glifo.** Novidade teorica (`$146`) e a letra `N`, e
comentario de editor (`$145`) e `RR`. Colocar `N` no padrao de NAG do
tokenizador faria toda casa de cavalo virar avaliacao. Esses entram na
tabela com `safe_glyph=False`: a interface os insere na forma numerica e o
tokenizador nao os procura.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WHITE_SIDE = "white"
BLACK_SIDE = "black"

GROUP_MOVE = "Lance"
GROUP_EVALUATION = "Avaliação"
GROUP_DYNAMICS = "Dinâmica"
GROUP_INTENT = "Intenção"

GROUP_ORDER = (GROUP_MOVE, GROUP_EVALUATION, GROUP_DYNAMICS, GROUP_INTENT)


@dataclass(frozen=True)
class Nag:
    """Um simbolo de anotacao e tudo que o programa precisa saber dele.

    `code` e a forma canonica gravada no PGN. `black_code` so existe nos
    simbolos que mudam de numero conforme o lado -- zugzwang das brancas e
    `$22`, o das pretas e `$23`, e o glifo e o mesmo nos dois casos. Quem
    resolve o lado e quem tem o tabuleiro na mao.
    """

    code: str
    glyph: str
    name: str
    group: str
    black_code: str | None = None
    #: De quem se fala. `True` (o normal) e o lado que **jogou** o lance --
    #: quem sacrificou e tem compensacao, quem atacou, quem tem a iniciativa.
    #: `False` e o lado que esta **na vez**: zugzwang e o unico caso, e e o
    #: caso em que o simbolo descreve quem vai ter de estragar a propria
    #: posicao no lance seguinte.
    side_is_mover: bool = True
    #: `False` quando o glifo e ambiguo demais para a interface o oferecer
    #: solto (`N` de novidade colide com o cavalo). A paleta insere `code`.
    safe_glyph: bool = True
    #: `True` quando o tokenizador so o reconhece **colado no fim de um
    #: lance**. E o que permite achar o `N` de `12.Nf4N` sem transformar em
    #: avaliacao todo cavalo do documento.
    suffix_only: bool = False
    #: Formas alternativas encontradas em PDF: fonte de simbolos do editor,
    #: ASCII do Informator, restos de OCR.
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_side_dependent(self) -> bool:
        return self.black_code is not None

    def code_for(self, white_to_move: bool) -> str:
        """O codigo do lado que **fez** o lance anotado."""
        if self.black_code is None:
            return self.code
        return self.code if white_to_move else self.black_code

    @property
    def insert_text(self) -> str:
        """O que a paleta escreve no editor: o glifo, ou o `$n` se for arriscado."""
        return self.glyph if self.safe_glyph else self.code


#: A tabela. Ordem = ordem de exibicao na paleta e no rodape.
#:
#: Os apelidos de fonte vem da chave de simbolos impressa no proprio livro
#: (Thinkers Publishing 2021, "Key to Symbols"), conferida contra o PGN que
#: o editor distribui junto: `Ne5=` la e `Ne5 $10` no PGN, `Nxe4!µ` e
#: `Nxe4 $1 $17`, `22.a3+–` e `22.a3 $18`.
NAG_TABLE: tuple[Nag, ...] = (
    # -- Lance ---------------------------------------------------------
    Nag("$1", "!", "Bom lance", GROUP_MOVE),
    Nag("$2", "?", "Lance fraco", GROUP_MOVE),
    Nag("$3", "!!", "Lance excelente", GROUP_MOVE),
    Nag("$4", "??", "Erro grave", GROUP_MOVE),
    Nag("$5", "!?", "Lance interessante", GROUP_MOVE),
    Nag("$6", "?!", "Lance duvidoso", GROUP_MOVE),
    Nag("$7", "□", "Lance único (forçado)", GROUP_MOVE, aliases=("™",)),
    Nag("$146", "N", "Novidade teórica", GROUP_MOVE, safe_glyph=False, suffix_only=True),
    # -- Avaliacao -----------------------------------------------------
    Nag("$10", "=", "Posição equilibrada", GROUP_EVALUATION),
    Nag("$13", "∞", "Posição pouco clara", GROUP_EVALUATION),
    Nag("$14", "⩲", "Brancas um pouco melhor", GROUP_EVALUATION, aliases=("²", "+/=", "+=")),
    Nag("$15", "⩱", "Pretas um pouco melhor", GROUP_EVALUATION, aliases=("³", "=/+", "=+")),
    Nag("$16", "±", "Brancas claramente melhor", GROUP_EVALUATION, aliases=("+/-",)),
    Nag(
        "$17",
        "∓",
        "Pretas claramente melhor",
        GROUP_EVALUATION,
        # `µ` U+00B5 e o micro-sinal; o NFKC o converte no mu grego U+03BC.
        # Os dois entram porque o texto pode chegar ja normalizado.
        aliases=("µ", "μ", "-/+"),
    ),
    Nag("$18", "+-", "Brancas ganham", GROUP_EVALUATION, aliases=("+–", "+—", "+−")),
    Nag("$19", "-+", "Pretas ganham", GROUP_EVALUATION, aliases=("–+", "—+", "−+")),
    Nag("$44", "©", "Compensação pelo material", GROUP_EVALUATION, black_code="$45", aliases=("=/∞",)),
    # -- Dinamica ------------------------------------------------------
    Nag("$22", "⨀", "Zugzwang", GROUP_DYNAMICS, black_code="$23", side_is_mover=False, aliases=("⊙", "ʘ")),
    Nag("$32", "⟳", "Vantagem de desenvolvimento", GROUP_DYNAMICS, black_code="$33", aliases=("‰",)),
    Nag("$36", "↑", "Com iniciativa", GROUP_DYNAMICS, black_code="$37", aliases=("ƒ",)),
    Nag("$40", "→", "Com ataque", GROUP_DYNAMICS, black_code="$41", aliases=("‚",)),
    Nag("$132", "⇆", "Com contrajogo", GROUP_DYNAMICS, black_code="$133", aliases=("„",)),
    Nag("$138", "⨁", "Em apuro de tempo", GROUP_DYNAMICS, black_code="$139"),
    # -- Intencao ------------------------------------------------------
    # `…` **nao** entra como apelido de `∆`, embora a chave de simbolos da
    # Thinkers diga que sim: reticencia tipografica e o que quase todo PDF de
    # xadrez usa para marcar lance das pretas (`7…h6`). Trocar isso por `∆`
    # destruiria a numeracao. O NFKC ja a converte em `...`, que e o certo.
    Nag("$140", "∆", "Com a ideia de", GROUP_INTENT, aliases=("Δ",)),
    Nag("$141", "∇", "Contra a ideia de", GROUP_INTENT),
    Nag("$142", "⌓", "Melhor é", GROUP_INTENT, aliases=("¹",)),
    Nag("$143", "≤", "Pior é", GROUP_INTENT),
    Nag("$145", "RR", "Comentário do editor", GROUP_INTENT, safe_glyph=False),
)


NAG_BY_GLYPH: dict[str, Nag] = {nag.glyph: nag for nag in NAG_TABLE}
NAG_BY_CODE: dict[str, Nag] = {}
for _nag in NAG_TABLE:
    NAG_BY_CODE[_nag.code] = _nag
    if _nag.black_code:
        NAG_BY_CODE[_nag.black_code] = _nag


def nags_by_group() -> list[tuple[str, list[Nag]]]:
    """A tabela agrupada, na ordem em que a interface deve mostra-la."""
    return [(group, [nag for nag in NAG_TABLE if nag.group == group]) for group in GROUP_ORDER]


# ----------------------------------------------------------------------
# Glifos do livro -> glifo canonico
# ----------------------------------------------------------------------

#: Todo apelido conhecido -> o glifo canonico correspondente.
BOOK_SYMBOL_ALIASES: dict[str, str] = {}
for _nag in NAG_TABLE:
    for _alias in _nag.aliases:
        BOOK_SYMBOL_ALIASES[_alias] = _nag.glyph

#: Apelidos que so valem **antes** de um lance.
#:
#: `¹` e a unica colisao real da tabela: na chave da Thinkers ele e "melhor e"
#: e vem antes do lance (`¹18.Rd1`); em todo o resto da literatura ele e marca
#: de nota de rodape e vem colada depois (`Nf3¹`). A posicao resolve, e por
#: isso vale a pena separar os dois casos em vez de escolher um.
_PREFIX_ONLY_ALIASES = frozenset({"¹"})


def _alternation(aliases: list[str]) -> str:
    # Do mais longo para o mais curto: a alternancia do `re` para no primeiro
    # que casar, e sem isso `+` casaria antes de `+-`.
    return "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))


#: O que pode terminar um lance em SAN. E o que a espiada-atras do sufixo
#: exige: `12.Nf4N` tem `4` antes do `N`, e `2.Nf3 Nc6` tem um espaco.
_MOVE_END_CLASS = "[a-h1-8QRBNO#+=]"

#: Apelidos que tambem sao tipografia comum: aspa baixa alema/polonesa (`„`),
#: aspa simples baixa (`‚`), florim (`ƒ`) e por mil (`‰`). Soltos na prosa eles
#: sao o que parecem; so colados ao fim de um lance (`Nf3‚`) valem como simbolo
#: de livro. Antes `Nf3 „gute Idee“` virava `Nf3 $132 {gute Idee"}` (N-10).
_GLUED_ONLY_ALIASES = frozenset({"„", "‚", "ƒ", "‰"})

_FREE_ALIASES = [
    alias for alias in BOOK_SYMBOL_ALIASES if alias not in _PREFIX_ONLY_ALIASES and alias not in _GLUED_ONLY_ALIASES
]
_PREFIX_ALIASES = [alias for alias in BOOK_SYMBOL_ALIASES if alias in _PREFIX_ONLY_ALIASES]
_GLUED_ALIASES = [alias for alias in BOOK_SYMBOL_ALIASES if alias in _GLUED_ONLY_ALIASES]

#: Padrao publico -- o normalizador o aplica no texto bruto, antes do NFKC.
BOOK_SYMBOL_PATTERN = (
    f"(?:{_alternation(_FREE_ALIASES)})"
    + (f"|(?<![A-Za-z0-9])(?:{_alternation(_PREFIX_ALIASES)})" if _PREFIX_ALIASES else "")
    + (f"|(?<={_MOVE_END_CLASS})(?:{_alternation(_GLUED_ALIASES)})" if _GLUED_ALIASES else "")
)

_ALIAS_RE = re.compile(BOOK_SYMBOL_PATTERN)


def map_book_symbols(text: str) -> tuple[str, int]:
    """Troca os simbolos de fonte de livro pelos glifos canonicos.

    Devolve `(texto, quantidade trocada)`. Tem de rodar **antes** de
    qualquer normalizacao Unicode: o NFKC transforma `²` em `2` e `¹` em `1`,
    e depois disso nao ha mais o que recuperar.
    """
    count = 0

    def replace(match: re.Match) -> str:
        nonlocal count
        count += 1
        return BOOK_SYMBOL_ALIASES[match.group(0)]

    return _ALIAS_RE.sub(replace, text), count


# ----------------------------------------------------------------------
# Consumidores
# ----------------------------------------------------------------------

#: Glifos que o tokenizador pode procurar no texto, do mais longo para o
#: mais curto -- a alternancia do `re` e ordenada e `+` casaria antes de `+-`.
SEARCHABLE_GLYPHS: tuple[str, ...] = tuple(
    sorted(
        (nag.glyph for nag in NAG_TABLE if nag.safe_glyph),
        key=len,
        reverse=True,
    )
)

#: Glifos que nao sao `!`/`?` -- o tokenizador ja trata esses a parte.
SYMBOLIC_GLYPHS: tuple[str, ...] = tuple(glyph for glyph in SEARCHABLE_GLYPHS if not set(glyph) <= {"!", "?"})


#: Glifos que so valem colados no fim de um lance.
SUFFIX_GLYPHS: tuple[str, ...] = tuple(nag.glyph for nag in NAG_TABLE if nag.suffix_only)


def symbolic_nag_pattern() -> str:
    """Alternancia de regex com todos os glifos simbolicos conhecidos."""
    return "(?:" + "|".join(re.escape(glyph) for glyph in SYMBOLIC_GLYPHS) + ")"


def suffix_nag_pattern() -> str:
    """Sufixos de livro: so casam grudados no lance anterior (SPEC 2.2).

    A espiada-atras e a regra inteira. Sem ela, `N` no padrao de NAG faria
    todo cavalo do documento virar novidade teorica; com ela, `Nc6` fica
    intacto (vem depois de um espaco) e o `N` de `12.Nf4N` e achado.
    """
    if not SUFFIX_GLYPHS:
        return "(?!)"
    body = "|".join(re.escape(glyph) for glyph in sorted(SUFFIX_GLYPHS, key=len, reverse=True))
    return rf"(?<={_MOVE_END_CLASS})(?:{body})(?![\w])"


def canonical_code(glyph: str, white_to_move: bool = True) -> str | None:
    """`$n` de um glifo, ou `None` se ele nao estiver na tabela.

    `white_to_move` e o lado que **fez** o lance: e o que decide entre
    `$22` e `$23`, `$44` e `$45`, e assim por diante.
    """
    nag = NAG_BY_GLYPH.get(glyph)
    if nag is None:
        return None
    return nag.code_for(white_to_move)


def describe(token: str) -> str:
    """Nome legivel de um glifo ou de um `$n`, para dica de tela e alerta."""
    nag = NAG_BY_GLYPH.get(token) or NAG_BY_CODE.get(token)
    return nag.name if nag else token
