"""Gestos de edicao estrutural sobre o **texto bruto**.

Tudo aqui e funcao pura: recebe o texto e a AST, devolve uma lista de `TextEdit`.
Quem aplica e a interface, dentro de um unico bloco de desfazer -- assim o
Ctrl+Z volta o gesto inteiro de uma vez.

Os `TextEdit` sao devolvidos ja ordenados de tras para frente, para que aplicar
um nao invalide as posicoes do seguinte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from .annotations import VisualAnnotations, apply_annotations, format_annotations
from .normalizer import comment_span_end
from .parser import GameAST, MoveNode, iter_moves
from .tokenizer import PGNTokenizer

RESULT_TAIL_RE = re.compile(r"\s*(?:1-0|0-1|1/2-1/2|\*)\s*$")

#: O que, depois do SAN, ainda **pertence** ao lance: os simbolos de avaliacao.
#: Vem da mesma tabela que o tokenizador usa, e por isso `+-`, `⩲` e o `N` de
#: novidade contam como parte do lance tanto aqui quanto la.
#:
#: `[ \t]*` e nao `\s*`: um `!` na linha seguinte e do proximo lance.
_TRAILING_NAG_RE = re.compile(rf"[ \t]*(?:{PGNTokenizer.NAG_PATTERN})")


@dataclass
class TextEdit:
    """Uma substituicao no texto bruto."""

    start: int
    end: int
    replacement: str

    def apply_to(self, text: str) -> str:
        return text[: self.start] + self.replacement + text[self.end :]


@dataclass
class OperationResult:
    edits: list[TextEdit]
    message: str = ""
    # Onde deixar a selecao depois de aplicar (posicoes no texto ja editado).
    select_start: int | None = None
    select_end: int | None = None

    @property
    def ok(self) -> bool:
        return bool(self.edits)


def apply_edits(text: str, edits: list[TextEdit]) -> str:
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        text = edit.apply_to(text)
    return text


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------


def _trim_selection(text: str, start: int, end: int) -> tuple[int, int]:
    start = max(0, min(start, len(text)))
    end = max(0, min(end, len(text)))
    if start > end:
        start, end = end, start
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end)


def _sanitize_comment_body(body: str) -> str:
    """Limpa prosa para caber em uma única linha de comentário PGN.

    PDF/HTML costuma trazer quebras físicas e entidades de espaço no meio da
    frase. Dentro de ``{...}`` elas não carregam informação estrutural, então
    viram espaços normais; chaves internas continuam sendo convertidas para
    evitar comentários aninhados inválidos.
    """
    body = body.replace("&#x20;", " ").replace("&#X20;", " ").replace("&#160;", " ").replace("&nbsp;", " ")
    body = re.sub(r"\s+", " ", body)
    return body.replace("{", "(").replace("}", ")").strip()


def _prose_as_comment_body(body: str) -> str:
    """Prosa vinda de fora pronta para morar entre chaves.

    Duas coisas acontecem aqui. A primeira: um comentario nao pode atravessar
    linha em branco -- passando dela, deixa de ser comentario para o
    tokenizador --, e uma pagina de PDF na area de transferencia vem cheia
    delas; o texto vira uma linha so. A segunda: quem copiou `{...}` de outro
    ponto do documento quer o **conteudo**, nao um comentario dentro de outro.
    """
    body = body.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    return _sanitize_comment_body(re.sub(r"\s+", " ", body))


def _skip_trailing_nags(text: str, position: int) -> int:
    """Fim do lance, simbolos de avaliacao inclusive.

    E onde o comentario do lance comeca: `12.Be3!` recebe `12.Be3! {…}`, e nao
    `12.Be3 {…}!` -- o `!` e do lance, nao do que vem depois dele.
    """
    while True:
        match = _TRAILING_NAG_RE.match(text, position)
        if match is None or match.end() == position:
            return position
        position = match.end()


def _comment_starting_at(text: str, position: int) -> tuple[int, int] | None:
    """Span do comentario que comeca em `position`, saltando espaco horizontal."""
    cursor = position
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    if cursor < len(text) and text[cursor] == "{":
        return (cursor, comment_span_end(text, cursor))
    return None


def move_number_for(move: MoveNode, *, force: bool = True) -> str | None:
    """Numero de lance deduzido da posicao anterior ao lance."""
    if not move.parent_fen:
        return None

    board = chess.Board(move.parent_fen)
    if board.turn == chess.WHITE:
        return f"{board.fullmove_number}."
    return f"{board.fullmove_number}..." if force else None


def moves_in_span(ast: GameAST, start: int, end: int) -> list[MoveNode]:
    return [move for move in iter_moves(ast.moves) if start <= move.san_start_index and move.san_end_index <= end]


def first_move_after(ast: GameAST, position: int) -> MoveNode | None:
    candidates = [move for move in iter_moves(ast.moves) if move.san_start_index >= position]
    return min(candidates, key=lambda move: move.san_start_index) if candidates else None


def find_enclosing_delimiter(text: str, position: int) -> tuple[int, int, str] | None:
    """Menor par `{}` ou `()` que contem `position`. Devolve (abre, fecha, tipo)."""
    best: tuple[int, int, str] | None = None
    stack: list[tuple[int, str]] = []
    inside_comment = False

    for index, char in enumerate(text):
        if inside_comment:
            if char == "}":
                open_index, _kind = stack.pop()
                inside_comment = False
                if open_index <= position <= index:
                    if best is None or (index - open_index) < (best[1] - best[0]):
                        best = (open_index, index, "{}")
            continue

        if char == "{":
            stack.append((index, "{}"))
            inside_comment = True
        elif char == "(":
            stack.append((index, "()"))
        elif char == ")":
            while stack and stack[-1][1] != "()":
                stack.pop()
            if stack:
                open_index, _kind = stack.pop()
                if open_index <= position <= index:
                    if best is None or (index - open_index) < (best[1] - best[0]):
                        best = (open_index, index, "()")

    return best


def scope_end(text: str, position: int) -> int:
    """Fim do escopo atual: o `)` que fecha a variante em volta, ou o fim do texto.

    O resultado final (`1-0`, `*`) fica de fora -- ele pertence a partida, nao
    a linha que esta sendo movida.
    """
    depth = 0
    index = position
    while index < len(text):
        char = text[index]
        if char == "{":
            close = text.find("}", index)
            line_end = text.find("\n", index)
            if close == -1 or (line_end != -1 and line_end < close):
                index = line_end if line_end != -1 else len(text)
            else:
                index = close + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return index
            depth -= 1
        index += 1

    tail = RESULT_TAIL_RE.search(text, position)
    return tail.start() if tail else len(text)


# ---------------------------------------------------------------------------
# Gestos
# ---------------------------------------------------------------------------


def wrap_as_comment(text: str, start: int, end: int) -> OperationResult:
    """Ctrl+K -- envolve a selecao em chaves."""
    start, end = _trim_selection(text, start, end)
    if start >= end:
        return OperationResult([], "Selecione o texto que deve virar comentário.")

    body = text[start:end]
    if body.startswith("{") and body.endswith("}"):
        return OperationResult([], "Este trecho já é um comentário.")

    replacement = "{" + _sanitize_comment_body(body) + "}"
    return OperationResult(
        [TextEdit(start, end, replacement)],
        "Trecho transformado em comentário.",
        select_start=start,
        select_end=start + len(replacement),
    )


def wrap_as_variation(text: str, start: int, end: int, ast: GameAST) -> OperationResult:
    """Ctrl+9 -- envolve a selecao em parenteses **com a numeracao certa**.

    E o gesto que o fluxo de livro mais usa: seleciona-se `Nf6 5.Be3` no meio do
    texto e sai `(4... Nf6 5. Be3)`, com o lance seguinte da linha principal
    recuperando o proprio numero.
    """
    start, end = _trim_selection(text, start, end)
    if start >= end:
        return OperationResult([], "Selecione os lances que devem virar variante.")

    body = text[start:end].strip()
    if body.startswith("(") and body.endswith(")"):
        return OperationResult([], "Este trecho já é uma variante.")

    inner_moves = moves_in_span(ast, start, end)
    edits: list[TextEdit] = []

    # 1. O primeiro lance da variante precisa se identificar.
    prefix = ""
    if inner_moves:
        first = inner_moves[0]
        if not first.number and first.san_start_index == start:
            number = move_number_for(first)
            if number:
                prefix = f"{number} "

    replacement = f"({prefix}{body})"
    edits.append(TextEdit(start, end, replacement))

    # 2. O lance seguinte da linha principal volta a ter numero, senao o leitor
    #    tem de contar lances para saber de quem e a vez depois do parentese.
    following = first_move_after(ast, end)
    if following is not None and not following.number and following not in inner_moves:
        number = move_number_for(following)
        if number:
            edits.append(TextEdit(following.san_start_index, following.san_start_index, f"{number} "))

    return OperationResult(
        edits,
        "Trecho transformado em variante.",
        select_start=start,
        select_end=start + len(replacement),
    )


def unwrap_at(text: str, position: int) -> OperationResult:
    """Ctrl+Shift+K -- tira os delimitadores em volta do cursor."""
    found = find_enclosing_delimiter(text, position)
    if not found:
        return OperationResult([], "O cursor não esta dentro dé um comentário nem dé uma variante.")

    open_index, close_index, kind = found
    body = text[open_index + 1 : close_index].strip()
    label = "Comentário" if kind == "{}" else "Variante"
    return OperationResult(
        [TextEdit(open_index, close_index + 1, body)],
        f"{label} desenvolvido.",
        select_start=open_index,
        select_end=open_index + len(body),
    )


def paragraph_to_comment(text: str, position: int, ast: GameAST) -> OperationResult:
    """Ctrl+Shift+C -- pega a prosa solta em volta do cursor e vira comentario.

    "Prosa solta" e o trecho continuo, entre os lances vizinhos, que ainda nao
    esta entre chaves. E o caso classico do parágrafo que veio do PDF entre dois
    lances.
    """
    if not 0 <= position <= len(text):
        return OperationResult([], "")

    moves = sorted(iter_moves(ast.moves), key=lambda move: move.san_start_index)
    lower = 0
    upper = len(text)
    for move in moves:
        if move.san_end_index <= position:
            lower = max(lower, move.san_end_index)
        if move.san_start_index >= position:
            upper = min(upper, move.start_index)

    # Nao atravessar delimitadores ja existentes.
    for char in "{}()\n":
        left = text.rfind(char, lower, position)
        if left != -1:
            lower = max(lower, left + 1)
        right = text.find(char, position, upper)
        if right != -1:
            upper = min(upper, right)

    start, end = _trim_selection(text, lower, upper)
    if start >= end:
        return OperationResult([], "Não ha prosa solta sob o cursor.")

    return wrap_as_comment(text, start, end)


def move_label(move: MoveNode) -> str:
    """`12. Be3` -- como se fala do lance numa mensagem para o usuario."""
    return " ".join(part for part in (move.number or move_number_for(move), move.display_san) if part)


def insert_comment(text: str, move: MoveNode, body: str, *, before: bool) -> OperationResult:
    """Cola prosa como comentario **do lance escolhido**, antes ou depois dele.

    Os dois lados nao sao o mesmo gesto visto de dois angulos. Em
    `7.Qd2 ( 7.Be2 … ) 7...Be7` ha mais de um ponto de insercao entre `Qd2` e
    `Be7`: "depois de 7.Qd2" cai antes de a variante abrir e "antes de 7...Be7"
    cai depois de ela fechar. E a diferenca que faz o comentario introdutorio de
    um lance nao ir para dentro da variante que o precede.

    Colar **antes** e antes do numero (`{c} 12.Be3`), e nao entre o numero e o
    lance. Vale dizer o que o PGN faz com isso: um comentario no meio da linha
    pertence, na leitura, ao lance **anterior** -- e a mesma posicao no fluxo do
    movetext, escolhida pelo lance de que a prosa fala.

    Um caso vale ser sabido de antemao, porque nao e escolha deste modulo:
    quando o lance e seguido de uma variante que o **substitui**, a prosa entre
    os dois e lida como introducao da variante -- e o que o PGN da editora faz
    (`7. Qd2 ( {Placing the queen on d2 …} 7. Be2 … )`, no gabarito do Najdorf),
    e por isso o parser faz igual. Para que ela fique com o lance, a variante
    precisa ter a introducao dela; ai as duas convivem.
    """
    body = _prose_as_comment_body(body)
    if not body:
        return OperationResult([], "Não ha texto para colar como comentário.")

    position = move.start_index if before else _skip_trailing_nags(text, move.san_end_index)
    if not 0 <= position <= len(text):
        return OperationResult([], "Lance fora do texto.")

    label = move_label(move)

    # Juntar ao comentario vizinho so vale **depois** do lance: ali o vizinho e
    # o comentario do proprio lance, e o livro quer um comentario por lance, nao
    # dois colados. Antes do lance, o vizinho a esquerda e do lance anterior --
    # reescreve-lo seria mexer no que não foi escolhido.
    existing = None if before else _comment_starting_at(text, position)
    if existing is not None:
        open_index, close_index = existing
        # Sem `}` no fim quando o comentario esta aberto -- e ele continua
        # aberto depois de juntar, que e o estado que o alerta ja aponta.
        existing_body = text[open_index + 1 : close_index].removesuffix("}")
        replacement = "{" + f"{existing_body.strip()} {body}".strip() + "}"
        return OperationResult(
            [TextEdit(open_index, close_index, replacement)],
            f"Comentário juntado ao que {label} já tinha.",
            select_start=open_index,
            select_end=open_index + len(replacement),
        )

    prefix = "" if position == 0 or text[position - 1].isspace() else " "
    suffix = "" if position >= len(text) or text[position].isspace() else " "
    replacement = f"{prefix}{{{body}}}{suffix}"
    return OperationResult(
        [TextEdit(position, position, replacement)],
        f"Comentário colado {'antes' if before else 'depois'} de {label}.",
        select_start=position + len(prefix),
        select_end=position + len(prefix) + len(body) + 2,
    )


def renumber_document(text: str, ast: GameAST) -> OperationResult:
    """Ctrl+R -- reescreve todos os numeros de lance a partir do tabuleiro.

    Conserta de uma vez os `10... .` herdados do OCR, os numeros que faltam
    depois dé uma variante e a numeracao que o livro trouxe errada.
    """
    edits: list[TextEdit] = []

    def walk(moves: list[MoveNode]):
        # Depois de um lance nao reconhecido o tabuleiro esta dessincronizado:
        # renumerar dali em diante inventaria numeros a partir de uma posicao
        # que nao existe -- inclusive trocando um lance das pretas por um das
        # brancas. Melhor deixar como esta e o alerta pedir a correcao.
        trustworthy = True

        for position, move in enumerate(moves):
            if not move.is_valid:
                trustworthy = False

            if trustworthy and move.parent_fen:
                board = chess.Board(move.parent_fen)
                is_white = board.turn == chess.WHITE

                # Um lance das pretas so precisa de numero quando algo foi
                # impresso entre ele e o lance anterior. Testar isso no texto
                # -- e nao na arvore -- cobre comentario e variante de uma vez,
                # sem depender de onde a heuristica ancorou a variante.
                gap = text[moves[position - 1].san_end_index : move.start_index] if position else ""
                interrupted = ")" in gap or "}" in gap

                needs_number = (
                    is_white or position == 0 or bool(move.number) or bool(move.comments_before) or interrupted
                )
                desired = f"{move_number_for(move)} " if needs_number else ""

                current = text[move.start_index : move.san_start_index]
                if current != desired:
                    edits.append(TextEdit(move.start_index, move.san_start_index, desired))

            for variation in move.variations:
                walk(variation)

    walk(ast.moves)

    if not edits:
        return OperationResult([], "A numeracao já esta correta.")
    return OperationResult(edits, f"{len(edits)} número(s) de lance corrigido(s).")


def promote_variation(text: str, ast: GameAST, owner: MoveNode, variation_index: int) -> OperationResult:
    """Troca uma variante com a linha principal a partir da mesma posicao.

    `A ( V ) M` vira `V ( A M )` quando a variante e alternativa a `A`, e
    `( V ) M` vira `V ( M )` quando ela parte da posicao depois de `A`.
    """
    if variation_index >= len(owner.variation_spans):
        return OperationResult([], "Variante sem posição registrada no texto.")

    open_index, close_index = owner.variation_spans[variation_index]
    if close_index <= open_index:
        return OperationResult([], "Variante sem parentese de fechamento.")

    anchor_after = (
        owner.variation_anchor_after[variation_index] if variation_index < len(owner.variation_anchor_after) else True
    )

    variation_body = text[open_index + 1 : close_index - 1].strip()
    if not variation_body:
        return OperationResult([], "Variante vazia.")

    # O parser ancora a variante numa **posicao**, nao num lance. Com
    # `anchor_after`, ela parte de depois do lance ancora -- entao o lance da
    # linha principal que vem logo a seguir tambem faz parte do que e rebaixado.
    region_start = owner.san_end_index if anchor_after else owner.start_index
    demoted_head = text[region_start:open_index].strip()

    region_end = scope_end(text, close_index)
    mainline_tail = text[close_index:region_end].strip()

    demoted = " ".join(part for part in (demoted_head, mainline_tail) if part)
    if not demoted:
        return OperationResult([], "Não ha linha principal para trocar com a variante.")

    # A regiao comeca colada ao lance ancora; sem isto sairia `e52.Bc4`.
    separator = "" if region_start == 0 or text[region_start - 1].isspace() else " "
    replacement = f"{separator}{variation_body} ({demoted})"

    return OperationResult(
        [TextEdit(region_start, region_end, replacement)],
        "Variante promovida a linha principal.",
        select_start=region_start + len(separator),
        select_end=region_start + len(separator) + len(variation_body),
    )


def move_variation(text: str, owner: MoveNode, variation_index: int, delta: int) -> OperationResult:
    """Sobe ou descé uma variante entre as irmas do mesmo lance.

    A ordem das variantes nao e decorativa: em livro, a primeira e a principal
    alternativa e as seguintes sao secundarias. Trocar duas de lugar e so trocar
    dois trechos de texto -- e como não há aninhamento entre irmas, uma troca
    nunca invalida a posicao da outra.
    """
    spans = owner.variation_spans
    target_index = variation_index + delta

    if not 0 <= variation_index < len(spans):
        return OperationResult([], "Variante sem posição registrada no texto.")
    if not 0 <= target_index < len(spans):
        return OperationResult([], "A variante já esta no fim da lista.")

    first, second = sorted((spans[variation_index], spans[target_index]))
    if first[1] > second[0]:
        return OperationResult([], "As variantes se sobrepoem no texto.")

    return OperationResult(
        [
            TextEdit(second[0], second[1], text[first[0] : first[1]]),
            TextEdit(first[0], first[1], text[second[0] : second[1]]),
        ],
        "Variante movida." if delta < 0 else "Variante rebaixada entre as irmas.",
    )


def reanchor_variation(
    text: str,
    owner: MoveNode,
    variation_index: int,
    new_owner: MoveNode,
    anchor_after: bool = True,
) -> OperationResult:
    """Pendura a variante noutro lance, quando a heuristica de ancora errar.

    A ancoragem por legalidade acerta quase sempre, mas quando erra não hávia
    saida nenhuma a nao ser recortar e colar a mao. Aqui a variante e movida
    inteira -- parenteses incluidos -- para logo depois (ou logo antes) do lance
    escolhido, e o primeiro lance dela ganha o numero certo da posicao nova.
    """
    if not 0 <= variation_index < len(owner.variation_spans):
        return OperationResult([], "Variante sem posição registrada no texto.")

    open_index, close_index = owner.variation_spans[variation_index]
    if close_index <= open_index:
        return OperationResult([], "Variante sem parentese de fechamento.")

    if new_owner is owner:
        return OperationResult([], "A variante já esta neste lance.")

    destination = new_owner.san_end_index if anchor_after else new_owner.start_index
    if open_index <= destination <= close_index:
        return OperationResult([], "Não da para pendurar a variante nela mesma.")

    body = text[open_index + 1 : close_index - 1].strip()
    if not body:
        return OperationResult([], "Variante vazia.")

    # O numero de lance de dentro da variante fala da posicao **antiga**: sem
    # trocar, `(12... Be6)` continuaria dizendo 12 depois de mudar de lugar.
    number = move_number_for(new_owner) if anchor_after else move_number_for(new_owner, force=False)
    body = re.sub(r"^\d+\s*\.+\s*", "", body)
    if number:
        # Depois do lance ancora, quem joga e o outro lado.
        board = chess.Board(new_owner.parent_fen) if new_owner.parent_fen else None
        if board is not None and anchor_after:
            board.push_san(new_owner.display_san)
            number = f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}..."
        body = f"{number} {body}"

    separator = "" if destination == 0 or text[destination - 1].isspace() else " "

    # Tirar a variante deixaria dois espacos onde ela estava.
    removal_start = open_index
    if removal_start > 0 and text[removal_start - 1] == " ":
        removal_start -= 1

    # Ambas as posicoes sao do texto **original**: `apply_edits` aplica de tras
    # para frente justamente para que uma edicao nao desloque a outra.
    return OperationResult(
        [
            TextEdit(removal_start, close_index, ""),
            TextEdit(destination, destination, f"{separator}({body})"),
        ],
        "Variante reancorada.",
    )


def renumber_movetext(movetext: str, board: chess.Board) -> str:
    """Renumera um trecho de movetext a partir de uma posicao.

    E o que falta para colar uma linha copiada de outro lugar do livro: os
    numeros vem da posicao de origem, e no destino eles estao errados. Aqui os
    lances sao empurrados num tabuleiro de verdade, entao a renumeracao tambem
    diz **se a linha e legal** naquela posicao -- e quando nao e, o texto volta
    intocado em vez de sair numerado errado.
    """
    tokens = movetext.split()
    if not tokens:
        return movetext

    working = board.copy(stack=False)
    parts: list[str] = []

    for token in tokens:
        # O numero antigo sai fora -- solto (`12...`) ou colado ao lance
        # (`13.Bb5`), que e como o livro escreve. O novo vem da posicao.
        san = re.sub(r"^\d+\s*\.*\s*", "", token)
        if not san:
            continue

        try:
            move = working.parse_san(san)
        except (ValueError, AssertionError):
            return movetext

        if working.turn == chess.WHITE:
            parts.append(f"{working.fullmove_number}.")
        elif not parts:
            parts.append(f"{working.fullmove_number}...")

        parts.append(working.san(move))
        working.push(move)

    return " ".join(parts) if parts else movetext


# ---------------------------------------------------------------------------
# Verificacao de balanceamento
# ---------------------------------------------------------------------------


def set_move_annotations(text: str, move: MoveNode, annotations: VisualAnnotations) -> OperationResult:
    """Grava as setas e casas coloridas no comentario do lance.

    O PGN guarda anotacao visual dentro do comentario, entao e isso que se
    edita -- não há estrutura paralela nenhuma, e o texto bruto continua sendo a
    fonte da verdade. Se o lance ja tem comentario, a prosa dele e preservada e
    so os comandos `[%cal]`/`[%csl]` sao trocados; se nao tem, um comentario
    novo nasce logo depois do lance.
    """
    insert_at = move.san_end_index
    if not 0 <= insert_at <= len(text):
        return OperationResult([], "Lance fora do texto.")

    cursor = insert_at
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1

    if cursor < len(text) and text[cursor] == "{":
        end = comment_span_end(text, cursor)
        body = text[cursor:end].strip("{}")
        updated = apply_annotations(body, annotations)
        if not updated:
            # Sem prosa e sem anotacao, o comentario deixa de ter razao de ser.
            return OperationResult(
                [TextEdit(insert_at, end, "")],
                "Anotações removidas.",
            )
        return OperationResult(
            [TextEdit(cursor, end, "{" + updated + "}")],
            "Anotações gravadas no comentário.",
        )

    commands = format_annotations(annotations)
    if not commands:
        return OperationResult([], "Não ha anotação para gravar.")

    return OperationResult(
        [TextEdit(insert_at, insert_at, " {" + commands + "}")],
        "Anotações gravadas.",
    )


@dataclass
class BalanceProblem:
    position: int
    message: str


def check_balance(text: str) -> list[BalanceProblem]:
    """Delimitadores abertos e nao fechados, ou fechados sem abrir."""
    problems: list[BalanceProblem] = []
    open_parens: list[int] = []
    index = 0

    while index < len(text):
        char = text[index]

        if char == "{":
            close = text.find("}", index)
            line_end = text.find("\n", index)
            if close == -1 or (line_end != -1 and line_end < close):
                problems.append(BalanceProblem(index, "Comentário aberto com '{' e nunca fechado."))
                index = line_end if line_end != -1 else len(text)
            else:
                index = close + 1
            continue

        if char == "}":
            problems.append(BalanceProblem(index, "'}' sem '{' correspondente."))
        elif char == "(":
            open_parens.append(index)
        elif char == ")":
            if open_parens:
                open_parens.pop()
            else:
                problems.append(BalanceProblem(index, "')' sem '(' correspondente."))

        index += 1

    problems.extend(BalanceProblem(position, "'(' sem ')' correspondente.") for position in open_parens)
    return sorted(problems, key=lambda problem: problem.position)
