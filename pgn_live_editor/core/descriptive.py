"""Notacao descritiva resolvida **pela posicao**, em portugues, espanhol e ingles.

A notacao descritiva nao nomeia casas absolutas: `P4R` e "peao para a quarta
casa do rei" do ponto de vista de quem joga, `C3BR` e "cavalo para a terceira do
bispo do rei", e `PxP` nem diz qual peao nem onde. Por isso nada aqui tenta
traduzir o token para SAN. O token vira um conjunto de **restricoes** (qual
peca, de que coluna, para que fileira, capturando o que) e os lances legais da
posicao sao filtrados por elas. Sobrou um: e o lance. Sobraram varios: a
ambiguidade e real e vai para o painel, nunca e resolvida as cegas.

As tres familias de livro que o programa encontra:

| Idioma | Exemplo | Casa | Colunas |
|---|---|---|---|
| portugues | `P4R C3BR T1D PBDxP` | fileira e depois coluna | TD CD BD D R BR CR TR |
| espanhol | `P4R C3AR A5C PADxP` | fileira e depois coluna | TD CD AD D R AR CR TR |
| ingles | `P-K4 N-KB3 B-N5 QPxB` | coluna e depois fileira | QR QN QB Q K KB KN KR |

Uma coluna sem ala (`B`, `C`, `T` / `B`, `N`, `R`) vale pelas duas colunas
daquela peca; a posicao desempata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

import chess

from .issues import Candidate

KING_SIDE = frozenset(range(4, 8))
QUEEN_SIDE = frozenset(range(0, 4))


def _files(*names: str) -> frozenset[int]:
    return frozenset(chess.FILE_NAMES.index(name) for name in names)


@dataclass(frozen=True)
class DescriptiveLanguage:
    """Vocabulario de uma familia de livros."""

    code: str
    label: str
    pieces: dict[str, int]
    files: dict[str, frozenset[int]]
    #: `4R` (portugues, espanhol) ou `K4` (ingles).
    rank_first: bool
    #: Letra que qualifica a peca pela ala: `CR`/`TD` ou `KN`/`QR`.
    wings: dict[str, frozenset[int]]

    @property
    def piece_pattern(self) -> str:
        return _alternation(self.pieces)

    @property
    def file_pattern(self) -> str:
        return _alternation(self.files)


def _alternation(names: Iterable[str]) -> str:
    # Do nome mais longo para o mais curto: `TD` tem que casar antes de `T`.
    return "|".join(sorted((re.escape(name) for name in names), key=len, reverse=True))


PORTUGUESE = DescriptiveLanguage(
    code="pt",
    label="portuguesa",
    pieces={"P": chess.PAWN, "C": chess.KNIGHT, "B": chess.BISHOP, "T": chess.ROOK, "D": chess.QUEEN, "R": chess.KING},
    files={
        "TD": _files("a"),
        "CD": _files("b"),
        "BD": _files("c"),
        "D": _files("d"),
        "R": _files("e"),
        "BR": _files("f"),
        "CR": _files("g"),
        "TR": _files("h"),
        "T": _files("a", "h"),
        "C": _files("b", "g"),
        "B": _files("c", "f"),
    },
    rank_first=True,
    wings={"R": KING_SIDE, "D": QUEEN_SIDE},
)

SPANISH = DescriptiveLanguage(
    code="es",
    label="espanhola",
    pieces={"P": chess.PAWN, "C": chess.KNIGHT, "A": chess.BISHOP, "T": chess.ROOK, "D": chess.QUEEN, "R": chess.KING},
    files={
        "TD": _files("a"),
        "CD": _files("b"),
        "AD": _files("c"),
        "D": _files("d"),
        "R": _files("e"),
        "AR": _files("f"),
        "CR": _files("g"),
        "TR": _files("h"),
        "T": _files("a", "h"),
        "C": _files("b", "g"),
        "A": _files("c", "f"),
    },
    rank_first=True,
    wings={"R": KING_SIDE, "D": QUEEN_SIDE},
)

ENGLISH = DescriptiveLanguage(
    code="en",
    label="inglesa",
    # `Kt` e o cavalo dos livros ingleses anteriores a 1950.
    pieces={
        "P": chess.PAWN,
        "N": chess.KNIGHT,
        "KT": chess.KNIGHT,
        "B": chess.BISHOP,
        "R": chess.ROOK,
        "Q": chess.QUEEN,
        "K": chess.KING,
    },
    files={
        "QR": _files("a"),
        "QN": _files("b"),
        "QKT": _files("b"),
        "QB": _files("c"),
        "Q": _files("d"),
        "K": _files("e"),
        "KB": _files("f"),
        "KN": _files("g"),
        "KKT": _files("g"),
        "KR": _files("h"),
        "R": _files("a", "h"),
        "N": _files("b", "g"),
        "KT": _files("b", "g"),
        "B": _files("c", "f"),
    },
    rank_first=False,
    wings={"K": KING_SIDE, "Q": QUEEN_SIDE},
)

LANGUAGES: dict[str, DescriptiveLanguage] = {lang.code: lang for lang in (PORTUGUESE, SPANISH, ENGLISH)}

#: Ordem de tentativa quando o token nao casa no idioma configurado. Um livro
#: espanhol aberto com o programa em portugues ainda tem que funcionar.
FALLBACK_ORDER = ("pt", "es", "en")

#: Casa inicial de cada peca qualificada pela ala (`CR` = cavalo do rei = g1).
HOME_FILE = {
    (chess.KNIGHT, True): 6,
    (chess.KNIGHT, False): 1,
    (chess.BISHOP, True): 5,
    (chess.BISHOP, False): 2,
    (chess.ROOK, True): 7,
    (chess.ROOK, False): 0,
}

# Sufixos que nao mudam o lance: xeque, mate, en passant, avaliacao.
_CHECK_SUFFIX_RE = re.compile(r"(?:\+{1,2}|#|CH|DIS\.?CH|DBL\.?CH|MATE|MAT|JAQUE|XEQUE)$")
_EN_PASSANT_RE = re.compile(r"(?:E\.?P\.?|A\.?P\.?)$")
_ANNOTATION_RE = re.compile(r"(?:[!?]{1,2}|[∞±∓⩱⩲□])+$")

# Confusoes de OCR que so fazem sentido na posicao de fileira (`P-KS` = `P-K5`,
# `K-Kl` = `K-K1`). Cada troca gera uma hipotese; a posicao decide.
_RANK_OCR = {"S": "53", "L": "1", "I": "1", "|": "1", "Z": "2", "G": "6"}


@dataclass(frozen=True)
class DescriptiveQuery:
    """Restricoes extraidas de um token, antes de olhar o tabuleiro."""

    language: DescriptiveLanguage
    piece: int
    #: Pecas: ala da identidade (`CR`); peoes: coluna atual (`PBD`).
    mover_files: frozenset[int] | None = None
    mover_is_wing: bool = False
    origin_rank: int | None = None  # relativa a quem joga, 1..8
    origin_files: frozenset[int] | None = None
    dest_rank: int | None = None
    dest_files: frozenset[int] | None = None
    capture: bool = False
    victim: int | None = None
    victim_files: frozenset[int] | None = None
    victim_is_wing: bool = False
    victim_rank: int | None = None
    victim_square_files: frozenset[int] | None = None
    promotion: int | None = None

    @property
    def fully_located(self) -> bool:
        """A casa de destino foi dada inteira (fileira **e** coluna unica)?"""
        return self.dest_rank is not None and self.dest_files is not None and len(self.dest_files) == 1


@cache
def _grammar(code: str) -> re.Pattern[str]:
    lang = LANGUAGES[code]
    piece = lang.piece_pattern
    file_ = lang.file_pattern
    if lang.rank_first:
        # P4R, C3BR, TD1B, PBDxP, T1R-1CD, C/4D-5C, T3BxT, DxC3C, P8D=D
        pattern = (
            rf"(?P<piece>{piece})(?P<mq>{file_})?"
            rf"(?:/?(?P<orank>[1-8])(?P<ofile>{file_})?(?:-|(?=X)))?"
            rf"(?:-?(?P<drank>[1-8])(?P<dfile>{file_})?)?"
            rf"(?:X(?P<victim>{piece})(?P<vq>{file_})?(?:/?(?P<vrank>[1-8])(?P<vfile>{file_})?)?)?"
            rf"(?:[=/(]?(?P<promo>{piece})\)?)?"
        )
    else:
        # P-K4, N-KB3, QN-Q2, KBP-B4, N/Q2-B3, QPxB, RxNP, BxN/B6, P-K8=Q
        qualifier = r"(?:[KQ](?:KT|[RNB])?|KT|[RNB])"
        pattern = (
            rf"(?P<mq>{qualifier}(?=KT|[PNBRQK]))?(?P<piece>{piece})"
            rf"(?:/(?P<ofile>{file_})?(?P<orank>[1-8])?)?"
            rf"(?:-?(?P<dfile>{file_})(?P<drank>[1-8]))?"
            rf"(?:X(?P<vq>{qualifier}(?=KT|[PNBRQK]))?(?P<victim>{piece})(?:/(?P<vfile>{file_})?(?P<vrank>[1-8]))?)?"
            rf"(?:[=/(]?(?P<promo>{piece})\)?)?"
        )
    return re.compile(pattern)


def prepare_token(token: str) -> str:
    """Tira do token tudo o que nao escolhe o lance: avaliacao, xeque, pontuacao."""
    text = _ANNOTATION_RE.sub("", token.strip())
    text = text.upper().replace("×", "X").replace(":", "X")
    # `Q.-R7` e `P-Q.B3`: ponto de OCR ou de abreviatura no meio do token.
    text = re.sub(r"(?<=[A-Z])\.(?=[A-Z0-9\-])", "", text)
    text = text.rstrip(".")
    # `P--Q4`: o traco dobrado e do OCR, nao da notacao.
    text = re.sub(r"-{2,}", "-", text)
    text = _EN_PASSANT_RE.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _CHECK_SUFFIX_RE.sub("", text)
        text = _ANNOTATION_RE.sub("", text)
    # `C(4D)5C` pode chegar sem o normalizador ter passado (colagem direta).
    text = re.sub(r"\(([^()]*)\)-?(?=[A-Z0-9])", r"/\1-", text)
    return text


def _relative_rank(rank: int, color: chess.Color) -> int:
    """Fileira descritiva (1..8, de quem joga) -> indice absoluto (0..7)."""
    return rank - 1 if color == chess.WHITE else 8 - rank


def parse_descriptive(token: str, language: DescriptiveLanguage) -> DescriptiveQuery | None:
    text = prepare_token(token)
    if not text or not text[0].isalpha():
        return None
    match = _grammar(language.code).fullmatch(text)
    if not match or not (match.group("drank") or match.group("dfile") or match.group("victim")):
        # `P`, `N`, `R` soltos nao sao lance.
        return None
    groups = match.groupdict()

    def files(name: str | None) -> frozenset[int] | None:
        return language.files.get(name) if name else None

    piece = language.pieces[groups["piece"]]
    mover_files: frozenset[int] | None = None
    mover_is_wing = False
    if groups["mq"]:
        qualifier = groups["mq"]
        if piece == chess.PAWN:
            mover_files = files(qualifier)
        elif qualifier in language.wings:
            mover_files = language.wings[qualifier]
            mover_is_wing = True
        else:
            # `TR` como qualificador de torre nao existe; `KB` em ingles e o
            # bispo do rei, que e a mesma ala que `K`.
            wing = qualifier[:1]
            if wing not in language.wings:
                return None
            mover_files = language.wings[wing]
            mover_is_wing = True

    victim = language.pieces.get(groups["victim"]) if groups["victim"] else None
    victim_files: frozenset[int] | None = None
    victim_is_wing = False
    if groups["vq"]:
        if victim == chess.PAWN:
            victim_files = files(groups["vq"])
        elif groups["vq"][:1] in language.wings:
            victim_files = language.wings[groups["vq"][:1]]
            victim_is_wing = True

    promotion = language.pieces.get(groups["promo"]) if groups["promo"] else None
    if promotion in (chess.PAWN, chess.KING):
        return None

    return DescriptiveQuery(
        language=language,
        piece=piece,
        mover_files=mover_files,
        mover_is_wing=mover_is_wing,
        origin_rank=int(groups["orank"]) if groups["orank"] else None,
        origin_files=files(groups["ofile"]),
        dest_rank=int(groups["drank"]) if groups["drank"] else None,
        dest_files=files(groups["dfile"]),
        capture=bool(groups["victim"]),
        victim=victim,
        victim_files=victim_files,
        victim_is_wing=victim_is_wing,
        victim_rank=int(groups["vrank"]) if groups["vrank"] else None,
        victim_square_files=files(groups["vfile"]),
        promotion=promotion,
    )


def origin_file_by_square(board: chess.Board) -> dict[int, int]:
    """Coluna de origem de cada peca, seguida ao longo da partida.

    `CR` e o cavalo que **nasceu** em g1, ainda que hoje esteja em d4. So da
    para saber isso com a historia da partida desde a posicao inicial.
    """
    root = board.root()
    origins = {square: chess.square_file(square) for square in root.piece_map()}
    replay = root.copy(stack=False)
    for move in board.move_stack:
        castling_rook: tuple[int, int, int] | None = None
        if replay.is_castling(move):
            rank = chess.square_rank(move.from_square)
            kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
            rook_from = chess.square(7 if kingside else 0, rank)
            rook_to = chess.square(5 if kingside else 3, rank)
            rook_origin = origins.pop(rook_from, chess.square_file(rook_from))
            origins.pop(rook_to, None)
            castling_rook = (rook_from, rook_to, rook_origin)
        origin_file = origins.pop(move.from_square, chess.square_file(move.from_square))
        origins.pop(move.to_square, None)
        if replay.is_en_passant(move):
            captured_square = move.to_square - 8 if replay.turn == chess.WHITE else move.to_square + 8
            origins.pop(captured_square, None)
        origins[move.to_square] = origin_file
        if castling_rook is not None:
            _rook_from, rook_to, rook_origin = castling_rook
            origins[rook_to] = rook_origin
        replay.push(move)
    return origins


def _history_proves_identity(board: chess.Board) -> bool:
    """A partida comecou da posicao inicial? So entao a identidade e conhecida."""
    root = board.root()
    return bool(board.move_stack) and root.board_fen() == chess.STARTING_BOARD_FEN


def _victim_square(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return move.to_square + (-8 if board.turn == chess.WHITE else 8)
    return move.to_square


def _matches(query: DescriptiveQuery, board: chess.Board, move: chess.Move, origins: dict[int, int] | None) -> bool:
    color = board.turn
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type != query.piece:
        return False

    from_file = chess.square_file(move.from_square)
    if query.mover_files is not None:
        if query.mover_is_wing and origins is not None:
            home = HOME_FILE.get((query.piece, query.mover_files == KING_SIDE))
            if home is not None and origins.get(move.from_square) != home:
                return False
        elif from_file not in query.mover_files:
            return False

    if query.origin_rank is not None and chess.square_rank(move.from_square) != _relative_rank(
        query.origin_rank, color
    ):
        return False
    if query.origin_files is not None and from_file not in query.origin_files:
        return False

    if query.dest_rank is not None and chess.square_rank(move.to_square) != _relative_rank(query.dest_rank, color):
        return False
    if query.dest_files is not None and chess.square_file(move.to_square) not in query.dest_files:
        return False

    if query.promotion is not None and move.promotion != query.promotion:
        return False

    if board.is_capture(move) != query.capture:
        return False
    if query.capture:
        victim_square = _victim_square(board, move)
        victim = board.piece_at(victim_square)
        if query.victim is not None and (victim is None or victim.piece_type != query.victim):
            return False
        victim_file = chess.square_file(victim_square)
        if query.victim_files is not None:
            if query.victim_is_wing and origins is not None and query.victim is not None:
                home = HOME_FILE.get((query.victim, query.victim_files == KING_SIDE))
                if home is not None and origins.get(victim_square) != home:
                    return False
            elif victim_file not in query.victim_files:
                return False
        if query.victim_rank is not None:
            # O livro nomeia a casa da vitima ora do lado de quem captura, ora
            # do lado da vitima (`DxC3C` para o cavalo branco em b3). Vale qualquer um.
            rank = chess.square_rank(victim_square)
            if rank not in {_relative_rank(query.victim_rank, color), _relative_rank(query.victim_rank, not color)}:
                return False
        if query.victim_square_files is not None and victim_file not in query.victim_square_files:
            return False
    return True


def _rank_ocr_variants(token: str) -> list[str]:
    """`P-KS` -> `P-K5`, `P-K3`; `K-Kl` -> `K-K1`. So troca onde cabe uma fileira."""
    text = token.upper()
    variants: list[str] = []
    for index, char in enumerate(text):
        options = _RANK_OCR.get(char)
        if char == "8" and index > 0 and text[index - 1].isdigit():
            # `C1-38`: o `B` de coluna lido como `8`, logo depois da fileira.
            variants.append(text[:index] + "B" + text[index + 1 :])
            continue
        if char == "8" and index > 0 and text[index - 1] in "KQ" and text[index + 1 : index + 2].isdigit():
            # `P-Q84`, `N-K83`: o mesmo `B` no ingles, entre a ala e a fileira.
            variants.append(text[:index] + "B" + text[index + 1 :])
            continue
        if not options or index == 0:
            continue
        before = text[index - 1]
        after = text[index + 1 : index + 2]
        # Uma fileira fica depois de letra de coluna (`KS`, `BS`) no ingles, ou
        # antes dela (`SR`) no portugues/espanhol. Nunca e a primeira letra.
        if not (before.isalpha() or before == "-" or (after and after.isalpha())):
            continue
        for option in options:
            variants.append(text[:index] + option + text[index + 1 :])
    return variants


def _origin_mask(query: DescriptiveQuery, board: chess.Board) -> chess.Bitboard:
    """So as casas das pecas do tipo certo: `N-B3` nao precisa olhar lance de peao."""
    return board.pieces_mask(query.piece, board.turn)


def _destination_mask(query: DescriptiveQuery, board: chess.Board) -> chess.Bitboard:
    """Casas de destino possiveis, quando o token as diz (`4R`, `KB3`).

    Captura fica de fora: a casa dada pode ser a da vitima vista de qualquer
    lado, e o en passant cai noutra casa. Ali `_matches` decide.
    """
    if query.capture or query.dest_rank is None:
        return chess.BB_ALL
    rank = _relative_rank(query.dest_rank, board.turn)
    files = query.dest_files if query.dest_files is not None else range(8)
    mask = 0
    for file in files:
        mask |= chess.BB_SQUARES[chess.square(file, rank)]
    return mask


def _resolve_query(query: DescriptiveQuery, board: chess.Board, token: str) -> list[tuple[chess.Move, str]]:
    uses_identity = query.mover_is_wing or query.victim_is_wing
    origins = origin_file_by_square(board) if uses_identity and _history_proves_identity(board) else None
    notes: list[tuple[chess.Move, str]] = []
    for move in board.generate_legal_moves(_origin_mask(query, board), _destination_mask(query, board)):
        if _matches(query, board, move, origins):
            notes.append((move, f"notação descritiva {query.language.label} {token} convertida pela posição legal"))
    if query.promotion is None and len(notes) > 1:
        # `P8D` sem dizer a peca: o livro quis a dama.
        queens = [(move, note) for move, note in notes if move.promotion in (None, chess.QUEEN)]
        if queens and len(queens) < len(notes):
            notes = queens
    if query.mover_is_wing and origins is None and len(notes) > 1:
        # Sem historia (partida a partir de diagrama) a identidade nao e
        # provavel: fica a peca mais perto da casa de origem da ala (`TD1B`
        # com torres em b1 e d1 e a de b1).
        home = HOME_FILE.get((query.piece, query.mover_files == KING_SIDE))
        if home is not None:
            best = min(abs(chess.square_file(move.from_square) - home) for move, _note in notes)
            notes = [
                (move, note + "; preferência pela peça mais próxima da ala indicada")
                for move, note in notes
                if abs(chess.square_file(move.from_square) - home) == best
            ]
    return notes


def descriptive_candidates(token: str, board: chess.Board, locale: str) -> list[Candidate]:
    """Lances legais que o token descritivo pode significar, do mais provavel ao menos.

    Tenta primeiro o idioma configurado e so depois os outros: um livro em
    espanhol aberto com o programa em portugues continua legivel, e o painel diz
    em que notacao o lance foi lido.
    """
    stripped = _ANNOTATION_RE.sub("", token.strip())
    # Livros descritivos escrevem a peca em maiuscula. Manter essa exigencia
    # impede que `d4` (SAN) vire `D4` (dama em portugues). Minusculas toleradas:
    # o `x` de captura (`PxP`) e o `l`/`i` que o OCR poe no lugar do `1` (`K-Kl`).
    if not stripped[:1].isupper() or re.search(r"[a-z]", re.sub(r"[xli]", "", stripped[1:])):
        return []
    if re.search(r"[li]", stripped[1:]) and not re.search(r"[A-Z]", stripped[1:]):
        # `Ali`, `Bil`: palavra, nao lance.
        return []

    order = [locale] if locale in LANGUAGES else []
    order += [code for code in FALLBACK_ORDER if code not in order]

    for attempt, code in enumerate(order):
        language = LANGUAGES[code]
        penalty = 0.0 if attempt == 0 else 0.02
        found = _candidates_for(token, board, language, penalty)
        if found:
            return found

    # Ultima tentativa: fileira mal lida pelo OCR (`P-KS`, `K-Kl`), no idioma
    # configurado apenas. Vale menos e sempre passa pela revisao.
    primary = LANGUAGES.get(locale)
    if primary is None:
        return []
    moves: dict[str, Candidate] = {}
    for variant in _rank_ocr_variants(stripped):
        for candidate in _candidates_for(variant, board, primary, 0.0):
            if candidate.san not in moves:
                moves[candidate.san] = Candidate(
                    candidate.san,
                    0.0,
                    "descritiva+ocr",
                    f"leitura do OCR corrigida: {token} -> {variant}",
                )
    confidence = 0.85 if len(moves) == 1 else 0.60
    return [Candidate(c.san, confidence, c.source, c.rationale) for c in moves.values()]


_ANNOUNCES_MATE_RE = re.compile(r"(?:#|\+\+|MATE|MAT)$")
_ANNOUNCES_CHECK_RE = re.compile(r"(?:\+|CH|JAQUE|XEQUE)$")


def _prefer_announced_check(
    found: list[tuple[chess.Move, str]], board: chess.Board, token: str
) -> list[tuple[chess.Move, str]]:
    """`AxP+` com dois peoes capturaveis: o livro disse qual deles da xeque.

    So filtra quando o sinal existe **e** separa as leituras. Sinal de xeque
    esquecido pelo livro (ou pelo OCR) nao elimina nada.
    """
    text = _ANNOTATION_RE.sub("", token.strip().upper())
    if _ANNOUNCES_MATE_RE.search(text):
        wanted = board.is_checkmate
    elif _ANNOUNCES_CHECK_RE.search(text):
        wanted = board.is_check
    else:
        return found

    kept: list[tuple[chess.Move, str]] = []
    for move, note in found:
        board.push(move)
        try:
            if wanted():
                kept.append((move, note + "; único que dá o xeque anunciado"))
        finally:
            board.pop()
    return kept or found


def _candidates_for(token: str, board: chess.Board, language: DescriptiveLanguage, penalty: float) -> list[Candidate]:
    query = parse_descriptive(token, language)
    if query is None:
        return []
    found = _resolve_query(query, board, token)
    if not found:
        return []
    if len(found) > 1:
        found = _prefer_announced_check(found, board, token)

    candidates = [Candidate(board.san(move), 0.97 - penalty, "descritiva", note) for move, note in found]
    if len(candidates) == 1:
        return candidates

    if query.capture and board.move_stack and query.mover_files is None and query.victim_files is None:
        # `PxP` logo depois de `P4R`: capturar a peca que acabou de se mover e
        # a leitura que o livro quis. As outras continuam visiveis quando nao ha
        # captura assim.
        last_square = board.move_stack[-1].to_square
        recaptures = [c for c, (move, _note) in zip(candidates, found, strict=True) if move.to_square == last_square]
        if recaptures:
            return (
                [
                    Candidate(c.san, 0.94 - penalty, c.source, c.rationale + "; captura da peça recém-movida")
                    for c in recaptures
                ]
                if len(recaptures) == 1
                else [
                    Candidate(c.san, 0.82 - penalty, c.source, c.rationale + "; captura da peça recém-movida")
                    for c in recaptures
                ]
            )

    if query.mover_is_wing and all("preferência" in note for _move, note in found):
        return [Candidate(c.san, 0.96 - penalty, c.source, c.rationale) for c in candidates]

    confidence = (0.82 if query.fully_located else 0.72) - penalty
    return [Candidate(c.san, confidence, c.source, c.rationale) for c in candidates]
