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

RESULT_TAIL_RE = re.compile(r'\s*(?:1-0|0-1|1/2-1/2|\*)\s*$')


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
    """Chaves nao aninham em PGN: viram parenteses para o texto sobreviver."""
    return body.replace("{", "(").replace("}", ")").strip()


def move_number_for(move: MoveNode, *, force: bool = True) -> str | None:
    """Numero de lance deduzido da posicao anterior ao lance."""
    if not move.parent_fen:
        return None

    board = chess.Board(move.parent_fen)
    if board.turn == chess.WHITE:
        return f"{board.fullmove_number}."
    return f"{board.fullmove_number}..." if force else None


def moves_in_span(ast: GameAST, start: int, end: int) -> list[MoveNode]:
    return [
        move
        for move in iter_moves(ast.moves)
        if start <= move.san_start_index and move.san_end_index <= end
    ]


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
        return OperationResult([], "Selecione o texto que deve virar comentario.")

    body = text[start:end]
    if body.startswith("{") and body.endswith("}"):
        return OperationResult([], "Este trecho ja e um comentario.")

    replacement = "{" + _sanitize_comment_body(body) + "}"
    return OperationResult(
        [TextEdit(start, end, replacement)],
        "Trecho transformado em comentario.",
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
        return OperationResult([], "Este trecho ja e uma variante.")

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
        return OperationResult([], "O cursor nao esta dentro de um comentario nem de uma variante.")

    open_index, close_index, kind = found
    body = text[open_index + 1 : close_index].strip()
    label = "Comentario" if kind == "{}" else "Variante"
    return OperationResult(
        [TextEdit(open_index, close_index + 1, body)],
        f"{label} desenvolvido.",
        select_start=open_index,
        select_end=open_index + len(body),
    )


def paragraph_to_comment(text: str, position: int, ast: GameAST) -> OperationResult:
    """Ctrl+Shift+C -- pega a prosa solta em volta do cursor e vira comentario.

    "Prosa solta" e o trecho continuo, entre os lances vizinhos, que ainda nao
    esta entre chaves. E o caso classico do paragrafo que veio do PDF entre dois
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
        return OperationResult([], "Nao ha prosa solta sob o cursor.")

    return wrap_as_comment(text, start, end)


def renumber_document(text: str, ast: GameAST) -> OperationResult:
    """Ctrl+R -- reescreve todos os numeros de lance a partir do tabuleiro.

    Conserta de uma vez os `10... .` herdados do OCR, os numeros que faltam
    depois de uma variante e a numeracao que o livro trouxe errada.
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
                    is_white
                    or position == 0
                    or bool(move.number)
                    or bool(move.comments_before)
                    or interrupted
                )
                desired = f"{move_number_for(move)} " if needs_number else ""

                current = text[move.start_index : move.san_start_index]
                if current != desired:
                    edits.append(TextEdit(move.start_index, move.san_start_index, desired))

            for variation in move.variations:
                walk(variation)

    walk(ast.moves)

    if not edits:
        return OperationResult([], "A numeracao ja esta correta.")
    return OperationResult(edits, f"{len(edits)} numero(s) de lance corrigido(s).")


def promote_variation(text: str, ast: GameAST, owner: MoveNode, variation_index: int) -> OperationResult:
    """Troca uma variante com a linha principal a partir da mesma posicao.

    `A ( V ) M` vira `V ( A M )` quando a variante e alternativa a `A`, e
    `( V ) M` vira `V ( M )` quando ela parte da posicao depois de `A`.
    """
    if variation_index >= len(owner.variation_spans):
        return OperationResult([], "Variante sem posicao registrada no texto.")

    open_index, close_index = owner.variation_spans[variation_index]
    if close_index <= open_index:
        return OperationResult([], "Variante sem parentese de fechamento.")

    anchor_after = (
        owner.variation_anchor_after[variation_index]
        if variation_index < len(owner.variation_anchor_after)
        else True
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
        return OperationResult([], "Nao ha linha principal para trocar com a variante.")

    # A regiao comeca colada ao lance ancora; sem isto sairia `e52.Bc4`.
    separator = "" if region_start == 0 or text[region_start - 1].isspace() else " "
    replacement = f"{separator}{variation_body} ({demoted})"

    return OperationResult(
        [TextEdit(region_start, region_end, replacement)],
        "Variante promovida a linha principal.",
        select_start=region_start + len(separator),
        select_end=region_start + len(separator) + len(variation_body),
    )


# ---------------------------------------------------------------------------
# Verificacao de balanceamento
# ---------------------------------------------------------------------------


def set_move_annotations(text: str, move: MoveNode, annotations: VisualAnnotations) -> OperationResult:
    """Grava as setas e casas coloridas no comentario do lance.

    O PGN guarda anotacao visual dentro do comentario, entao e isso que se
    edita -- nao ha estrutura paralela nenhuma, e o texto bruto continua sendo a
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
                "Anotacoes removidas.",
            )
        return OperationResult(
            [TextEdit(cursor, end, "{" + updated + "}")],
            "Anotacoes gravadas no comentario.",
        )

    commands = format_annotations(annotations)
    if not commands:
        return OperationResult([], "Nao ha anotacao para gravar.")

    return OperationResult(
        [TextEdit(insert_at, insert_at, " {" + commands + "}")],
        "Anotacoes gravadas.",
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
                problems.append(BalanceProblem(index, "Comentario aberto com '{' e nunca fechado."))
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
