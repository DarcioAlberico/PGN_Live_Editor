from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

import chess

from .parser import GameAST, MoveNode
from .pgn_headers import STANDARD_PGN_HEADERS, normalize_pgn_date, normalize_pgn_result

CANONICAL_NAG_MAP: dict[str, str] = {
    "!": "$1",
    "?": "$2",
    "!!": "$3",
    "??": "$4",
    "!?": "$5",
    "?!": "$6",
    "\u25a1": "$7",
    "=": "$10",
    "\u221e": "$13",
    "\u2a72": "$14",
    "\u2a71": "$15",
    "\u00b1": "$16",
    "\u2213": "$17",
}


@dataclass
class MoveEntry:
    node: MoveNode
    path: tuple[MoveNode, ...]
    depth: int
    node_id: str
    selection_key: str
    start_index: int
    end_index: int


def _selection_key_for(path: Sequence[MoveNode]) -> str:
    parts = []
    for ply_index, move in enumerate(path):
        parts.append(f"{ply_index}:{move.number or '_'}:{move.display_san}")
    return "|".join(parts)


def build_move_entries(ast: GameAST) -> list[MoveEntry]:
    entries: list[MoveEntry] = []

    def walk(
        moves: list[MoveNode],
        base_path: tuple[MoveNode, ...],
        depth: int,
        structural_path: tuple[int, ...]
    ):
        running_path = list(base_path)
        for move_index, move in enumerate(moves):
            running_path.append(move)
            path = tuple(running_path)
            node_path = structural_path + (move_index,)
            node_id = ".".join(str(index) for index in node_path)

            entries.append(MoveEntry(
                node=move,
                path=path,
                depth=depth,
                node_id=node_id,
                selection_key=_selection_key_for(path),
                start_index=move.start_index,
                end_index=move.end_index
            ))

            for variation_index, variation in enumerate(move.variations):
                anchor_after = variation_index < len(move.variation_anchor_after) and move.variation_anchor_after[variation_index]
                variation_base = path if anchor_after else path[:-1]
                walk(variation, variation_base, depth + 1, node_path + (variation_index,))

    walk(ast.moves, tuple(), 0, tuple())
    return entries


def build_entry_maps(entries: Sequence[MoveEntry]) -> tuple[dict[str, MoveEntry], dict[int, MoveEntry]]:
    by_node_id = {entry.node_id: entry for entry in entries}
    by_move_identity = {id(entry.node): entry for entry in entries}
    return by_node_id, by_move_identity


def resolve_selection_key(entries: Sequence[MoveEntry], selection_key: str | None) -> MoveEntry | None:
    if not selection_key:
        return None

    exact_match = next((entry for entry in entries if entry.selection_key == selection_key), None)
    if exact_match:
        return exact_match

    requested_parts = selection_key.split("|")
    best_match: MoveEntry | None = None
    best_score = -1
    for entry in entries:
        current_parts = entry.selection_key.split("|")
        score = 0
        for requested, current in zip(requested_parts, current_parts, strict=False):
            if requested != current:
                break
            score += 1

        if score > best_score:
            best_score = score
            best_match = entry

    return best_match


def find_entry_for_position(entries: Sequence[MoveEntry], position: int) -> MoveEntry | None:
    containing_entries = [
        entry for entry in entries
        if entry.start_index <= position <= entry.end_index
    ]
    if not containing_entries:
        return None

    return min(containing_entries, key=lambda entry: (entry.end_index - entry.start_index, entry.depth))


def find_entry_for_insertion_position(entries: Sequence[MoveEntry], position: int) -> MoveEntry | None:
    containing_entries = [
        entry for entry in entries
        if entry.start_index < position <= entry.end_index
    ]
    if containing_entries:
        return min(containing_entries, key=lambda entry: (entry.end_index - entry.start_index, entry.depth))

    preceding_entries = [
        entry for entry in entries
        if entry.end_index < position
    ]
    if not preceding_entries:
        return None

    return max(preceding_entries, key=lambda entry: (entry.end_index, entry.depth, len(entry.path)))


def render_game_movetext(
    ast: GameAST,
    result_override: str | None = None,
    canonical_nags: bool = False
) -> str:
    def render_comments(comments: Sequence[str]) -> list[str]:
        return [f"{{{comment}}}" for comment in comments if comment]

    def render_nags(move: MoveNode, nags: Sequence[str]) -> list[str]:
        rendered_nags: list[str] = []
        for nag in nags:
            if canonical_nags:
                if nag in {"\u2a00", "\u2299"} and move.parent_fen and move.is_valid:
                    board = chess.Board(move.parent_fen)
                    try:
                        board.push_san(move.display_san)
                    except ValueError:
                        rendered_nags.append(nag)
                        continue
                    rendered_nags.append("$22" if board.turn == chess.WHITE else "$23")
                    continue

                rendered_nags.append(CANONICAL_NAG_MAP.get(nag, nag))
            else:
                rendered_nags.append(nag)
        return rendered_nags

    def _variations_for_anchor(move: MoveNode, anchor_after: bool) -> list[list[MoveNode]]:
        variations: list[list[MoveNode]] = []
        for variation_index, variation in enumerate(move.variations):
            current_anchor_after = True
            if variation_index < len(move.variation_anchor_after):
                current_anchor_after = move.variation_anchor_after[variation_index]
            if current_anchor_after == anchor_after:
                variations.append(variation)
        return variations

    def render_variation_lines(variations: Sequence[Sequence[MoveNode]]) -> list[str]:
        parts: list[str] = []
        for variation in variations:
            variation_text = render_line(variation)
            if variation_text:
                parts.append(f"( {variation_text} )")
        return parts

    def render_move(move: MoveNode, force_number: bool = False) -> str:
        parts: list[str] = []
        parts.extend(render_comments(move.comments_before))

        number = move.number
        if not number and (force_number or move.comments_before):
            number = _synthetic_move_number(move)

        move_label = " ".join(part for part in [number, move.display_san] if part)
        if move_label:
            parts.append(move_label)

        if move.nags:
            parts.extend(render_nags(move, move.nags))

        parts.extend(render_comments(move.comments_after))
        return " ".join(parts)

    def render_line(moves: Sequence[MoveNode]) -> str:
        rendered_parts: list[str] = []
        pending_after_variations: list[list[MoveNode]] = []
        # Depois de fechar uma variante, o proximo lance da linha principal
        # precisa reafirmar o numero (`) 12... Be6`), senao leitores como o
        # ChessBase podem se perder.
        force_number = False

        for move in moves:
            if not move:
                continue

            rendered_parts.append(render_move(move, force_number=force_number))

            before_variations = render_variation_lines(_variations_for_anchor(move, anchor_after=False))
            pending_rendered = render_variation_lines(pending_after_variations)
            rendered_parts.extend(before_variations)
            rendered_parts.extend(pending_rendered)
            force_number = bool(before_variations or pending_rendered)

            pending_after_variations = _variations_for_anchor(move, anchor_after=True)

        rendered_parts.extend(render_variation_lines(pending_after_variations))
        return " ".join(rendered_parts)

    movetext = render_line(ast.moves).strip()
    result = result_override or ast.result or "*"
    if movetext:
        return f"{movetext} {result}".strip()
    return result


def _synthetic_move_number(move: MoveNode) -> str | None:
    """Numero de lance deduzido da posicao, para quando o texto nao trouxe um."""
    if not move.parent_fen:
        return None

    board = chess.Board(move.parent_fen)
    if board.turn == chess.WHITE:
        return f"{board.fullmove_number}."
    return f"{board.fullmove_number}..."


def render_game_pgn(ast: GameAST) -> str:
    def escape_tag_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    header_values = dict(ast.headers)
    result = normalize_pgn_result(ast.result or header_values.get("Result") or "*")

    merged_headers = {tag: value for tag, value in STANDARD_PGN_HEADERS}
    extra_headers: list[tuple[str, str]] = []

    for tag, value in ast.headers:
        if tag in merged_headers:
            merged_headers[tag] = value
        elif tag not in {"Result", "SetUp"}:
            extra_headers.append((tag, value))

    merged_headers["Date"] = normalize_pgn_date(merged_headers["Date"])

    # `SetUp` e derivado, nunca copiado: sem isso o PGN sai incoerente com o FEN.
    fen = header_values.get("FEN", "").strip()
    if fen:
        extra_headers.append(("SetUp", "1"))

    header_lines = [f'[{tag} "{escape_tag_value(merged_headers[tag])}"]' for tag, _default in STANDARD_PGN_HEADERS]
    header_lines.append(f'[Result "{escape_tag_value(result)}"]')
    header_lines.extend(f'[{tag} "{escape_tag_value(value)}"]' for tag, value in extra_headers)

    movetext = render_game_movetext(ast, result_override=result, canonical_nags=True)
    return "\n".join(header_lines + ["", movetext])


def render_game_html(ast: GameAST, entries: Sequence[MoveEntry], current_node_id: str | None = None) -> str:
    _by_node_id, by_move_identity = build_entry_maps(entries)

    def render_comments(comments: Sequence[str]) -> str:
        if not comments:
            return ""
        return " ".join(f"<span class='comment'>{{{escape(comment)}}}</span>" for comment in comments)

    def render_move(move: MoveNode) -> str:
        entry = by_move_identity[id(move)]
        move_classes = ["move"]
        if current_node_id == entry.node_id:
            move_classes.append("current")
        if not move.is_valid:
            move_classes.append("invalid")
        elif move.corrected_san:
            move_classes.append("corrected")

        title = ""
        if move.corrected_san:
            title = f"Corrigido: {move.san} -> {move.corrected_san}"
        elif not move.is_valid:
            title = f"Lance inválido: {move.san}"

        label_parts = []
        if move.number:
            label_parts.append(move.number)
        label_parts.append(move.display_san)
        move_label = escape(" ".join(label_parts))

        parts = []
        before_comments = render_comments(move.comments_before)
        if before_comments:
            parts.append(before_comments)

        parts.append(
            f"<a name='{entry.node_id}'></a><a href='move:{entry.node_id}' class='{' '.join(move_classes)}' title='{escape(title)}'>{move_label}</a>"
        )

        if move.nags:
            parts.append(f"<span class='nag'>{escape(''.join(move.nags))}</span>")

        after_comments = render_comments(move.comments_after)
        if after_comments:
            parts.append(after_comments)

        for variation in move.variations:
            variation_html = render_line(variation)
            if variation_html:
                parts.append(f"<span class='variation'>( {variation_html} )</span>")

        return " ".join(part for part in parts if part)

    def render_line(moves: Sequence[MoveNode]) -> str:
        return " ".join(render_move(move) for move in moves if move)

    movetext = render_line(ast.moves)
    if ast.result:
        movetext = f"{movetext} <span class='result'>{escape(ast.result)}</span>".strip()

    if not movetext:
        movetext = "<span class='empty'>Cole o texto do PGN ou do PDF para ver a estrutura aqui.</span>"

    return f"""
    <html>
    <head>
        <style>
            body {{
                margin: 0;
                font-family: 'Consolas', 'Courier New', monospace;
                background: #16181c;
                color: #e9edf3;
            }}
            .wrap {{
                padding: 14px 16px 20px;
                line-height: 1.65;
                font-size: 14px;
                white-space: normal;
            }}
            .move {{
                color: #d6e2ff;
                text-decoration: none;
                border-radius: 6px;
                padding: 1px 4px;
            }}
            .move:hover {{
                background: #273244;
            }}
            .move.current {{
                background: #d8b13b;
                color: #1a1b1e;
                font-weight: 700;
            }}
            .move.invalid {{
                color: #ff8c8c;
                text-decoration: underline wavy #ff6b6b;
            }}
            .move.corrected {{
                color: #9ce1a5;
                text-decoration: underline;
            }}
            .comment {{
                color: #8fa2b9;
                font-style: italic;
            }}
            .variation {{
                color: #c7d0dc;
                display: inline;
                margin-left: 2px;
            }}
            .nag {{
                color: #f0c05a;
                font-weight: 700;
            }}
            .result {{
                color: #8ee0ff;
                font-weight: 700;
            }}
            .empty {{
                color: #758195;
            }}
        </style>
    </head>
    <body>
        <div class='wrap'>{movetext}</div>
    </body>
    </html>
    """
