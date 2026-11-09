"""Dispor o movetext em arvore indentada -- e desfazer.

O painel de arvore (SPEC 4.7) mostra a estrutura **ao lado** do texto. Este
modulo poe a estrutura **no proprio texto**, que e onde a edicao acontece:
uma variante por linha, recuada pela profundidade.

    1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6 6.Bg5 e6 7.Qd2
        ( 7.Be2 7...Be7 8.0-0 Qc7 )
    7...Be7
        ( 7...h6 8.Be3
            ( 8.Bh4? 8...Nxe4!∓ )
            ( 8.Bxf6 Qxf6 9.0-0-0 Nc6 )
        8...Ng4 9.0-0-0 )
    8.0-0-0

Duas garantias, e as duas sao testadas:

**Nada muda a nao ser espaco em branco.** A transformacao mexe apenas nos
intervalos **entre** os atomos do texto -- comentario, lance, parentese. Tirar
todo o espaco do antes e do depois tem de dar exatamente a mesma coisa. E o
que permite oferecer isto como gesto de um clique num documento de 300 KB.

**Comentario e intocavel.** O que esta dentro de `{}` passa inteiro, com os
espacos que tiver. Um `{` sem fechamento muda de sentido quando as linhas se
juntam, entao o gesto **se recusa** a rodar com delimitador aberto e diz onde
ele esta -- adivinhar ali destruiria o comentario e os lances seguintes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .game_splitter import GameSegment, split_games
from .normalizer import comment_span_end
from .text_ops import OperationResult, TextEdit, check_balance

INDENT = "    "

_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")

ATOM_COMMENT = "comment"
ATOM_OPEN = "open"
ATOM_CLOSE = "close"
ATOM_WORD = "word"


@dataclass(frozen=True)
class _Atom:
    kind: str
    start: int
    end: int


def _scan(text: str, start: int, end: int) -> list[_Atom]:
    """Os pedacos indivisiveis do movetext, na ordem em que aparecem."""
    atoms: list[_Atom] = []
    index = start

    while index < end:
        char = text[index]

        if char.isspace():
            index += 1
            continue

        if char == "{":
            stop = min(comment_span_end(text, index), end)
            atoms.append(_Atom(ATOM_COMMENT, index, stop))
            index = stop
            continue

        if char == "(":
            atoms.append(_Atom(ATOM_OPEN, index, index + 1))
            index += 1
            continue

        if char == ")":
            atoms.append(_Atom(ATOM_CLOSE, index, index + 1))
            index += 1
            continue

        stop = index
        while stop < end and not text[stop].isspace() and text[stop] not in "(){":
            stop += 1
        atoms.append(_Atom(ATOM_WORD, index, stop))
        index = stop

    return atoms


def _separator_before(
    atom: _Atom,
    previous: _Atom,
    gap: str,
    depth: int,
    indent: str,
    tree: bool,
) -> str:
    """O espaco que deve separar `previous` de `atom`."""
    if not tree:
        # Texto corrido: so a quebra de parágrafo sobrevive, e so fora de
        # variante -- dentro dela ela nunca teve sentido nenhum.
        return "\n\n" if depth == 0 and _BLANK_LINE_RE.search(gap) else " "

    if atom.kind == ATOM_OPEN:
        # A variante abre uma linha nova, recuada um nivel a mais do que a
        # linha de onde ela sai.
        return "\n" + indent * (depth + 1)

    if previous.kind == ATOM_CLOSE:
        # Depois de fechar, volta-se ao recuo do nivel em que se esta agora --
        # que e o mesmo do parentese que abriu, e e o que deixa o par alinhado.
        return "\n" + indent * depth

    if depth == 0 and _BLANK_LINE_RE.search(gap):
        return "\n\n"

    return " "


def _reflow(text: str, start: int, end: int, indent: str, tree: bool) -> list[TextEdit]:
    atoms = _scan(text, start, end)
    if len(atoms) < 2:
        return []

    edits: list[TextEdit] = []
    depth = 0

    for position, atom in enumerate(atoms):
        if position:
            previous = atoms[position - 1]
            gap = text[previous.end : atom.start]
            separator = _separator_before(atom, previous, gap, depth, indent, tree)
            if separator != gap:
                edits.append(TextEdit(previous.end, atom.start, separator))

        if atom.kind == ATOM_OPEN:
            depth += 1
        elif atom.kind == ATOM_CLOSE:
            depth = max(0, depth - 1)

    return edits


def _movetext_regions(text: str, segments: list[GameSegment] | None) -> list[tuple[int, int]]:
    """So o corpo de cada partida. Bloco de cabecalho e separador nao se tocam."""
    segments = segments if segments is not None else split_games(text)
    if not segments:
        return [(0, len(text))]
    return [(segment.raw_offset, segment.body_end_offset) for segment in segments]


def _apply(
    text: str,
    segments: list[GameSegment] | None,
    indent: str,
    tree: bool,
    done_message: str,
    idle_message: str,
) -> OperationResult:
    problems = check_balance(text)
    if problems:
        first = problems[0]
        return OperationResult(
            [],
            f"Ha delimitador sem fechamento (posição {first.position}). "
            "Conserte com Ctrl+Shift+B antes de redispor o texto.",
        )

    edits: list[TextEdit] = []
    for start, end in _movetext_regions(text, segments):
        edits.extend(_reflow(text, start, end, indent, tree))

    if not edits:
        return OperationResult([], idle_message)
    return OperationResult(edits, done_message.format(n=len(edits)))


def indent_variations(
    text: str,
    segments: list[GameSegment] | None = None,
    indent: str = INDENT,
) -> OperationResult:
    """Uma variante por linha, recuada pela profundidade."""
    return _apply(
        text,
        segments,
        indent,
        tree=True,
        done_message="Texto disposto em árvore ({n} quebra(s)).",
        idle_message="O texto já esta disposto em árvore.",
    )


def flatten_variations(text: str, segments: list[GameSegment] | None = None) -> OperationResult:
    """O inverso: movetext corrido, uma partida por parágrafo."""
    return _apply(
        text,
        segments,
        INDENT,
        tree=False,
        done_message="Texto voltou a ser corrido ({n} junção(ões)).",
        idle_message="O texto já esta corrido.",
    )


def content_of(text: str) -> str:
    """O texto sem espaco nenhum.

    E a invariante do modulo, e existe para ser comparada no teste: dispor em
    arvore e voltar a corrido nao podem mudar **nada** alem de espaco.
    """
    return re.sub(r"\s+", "", text)
