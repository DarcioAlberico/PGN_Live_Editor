"""Um documento e um livro, nao uma partida (SPEC 2.5 -- corrige D6).

O editor mantem **um** texto bruto e **uma lista** de segmentos. O tabuleiro, o
preview e o painel de alertas seguem o segmento que contem o cursor; a
exportacao em lote percorre todos.

Regra de corte: o inicio do documento, toda linha `[Tag "..."]` precedida de
linha em branco -- desde que a partida corrente ja tenha corpo -- e uma linha
separadora configuravel. O separador existe porque, em texto recem-colado de
PDF, os cabecalhos ainda nao foram escritos: `=== Partida 2 ===` e a forma mais
curta de dizer onde uma partida acaba e a outra comeca.

Os segmentos **ladrilham** o documento: todo caractere pertence a exatamente um
segmento. E isso que permite descobrir a partida sob o cursor com uma unica
comparacao de posicao.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pgn_headers import HEADER_LINE_RE, extract_pgn_headers

# `=== Partida 2 ===`, `----- 2 -----` nao; so a forma com `=`, que nao aparece
# em notacao (um `=` solto e simbolo de igualdade, nunca dois seguidos).
DEFAULT_SEPARATOR_PATTERN = r"^\s*={2,}[^=\n]*={2,}\s*$"

_YEAR_RE = re.compile(r"^(\d{4})")


@dataclass
class GameSegment:
    """Uma partida dentro do texto bruto do livro."""

    index: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    # Onde `body` comeca no texto bruto completo -- e o `base_offset` que o
    # normalizador precisa para que todo span da AST seja absoluto.
    raw_offset: int = 0
    # Limites do segmento inteiro, cabecalho e separador incluidos.
    start_offset: int = 0
    end_offset: int = 0
    # Onde comeca o texto da partida propriamente dita: depois da linha
    # separadora, antes do bloco de cabecalhos. E aqui que um cabecalho novo
    # entra -- inseri-lo em `start_offset` o poria **antes** do separador.
    content_offset: int = 0
    title: str = ""

    @property
    def body_end_offset(self) -> int:
        return self.raw_offset + len(self.body)

    @property
    def has_content(self) -> bool:
        return bool(self.body.strip() or self.headers)

    def contains(self, position: int) -> bool:
        return self.start_offset <= position < self.end_offset


def _iter_lines(text: str):
    offset = 0
    for line in text.splitlines(keepends=True):
        yield offset, line
        offset += len(line)


def _boundaries(raw_text: str, separator_pattern: str | None) -> list[tuple[int, int]]:
    """Pares (inicio do segmento, inicio do conteudo).

    Os dois so diferem quando o segmento comeca por uma linha separadora: ela
    delimita, mas nao faz parte do texto da partida -- deixa-la no corpo faria
    o tokenizador ler `===` como tres simbolos de avaliacao.
    """
    separator_re = re.compile(separator_pattern) if separator_pattern else None

    boundaries: list[tuple[int, int]] = [(0, 0)]
    saw_body = False

    for offset, line in _iter_lines(raw_text):
        stripped = line.strip()

        if separator_re is not None and stripped and separator_re.match(stripped):
            end_of_line = offset + len(line)
            if offset == boundaries[-1][0]:
                boundaries[-1] = (offset, end_of_line)
            else:
                boundaries.append((offset, end_of_line))
            saw_body = False
            continue

        if not stripped:
            continue

        is_header = HEADER_LINE_RE.match(stripped) is not None

        # O corte e "cabecalho depois de movetext", e nao "cabecalho depois de
        # linha em branco". O padrao PGN exige a linha em branco entre partidas,
        # mas arquivo concatenado a mao e texto de OCR nem sempre a trazem -- e
        # ali, fundir as duas partidas produz lance ilegal, que e pior do que
        # qualquer corte errado. `saw_body` tambem garante o inverso: uma linha
        # em branco no meio do bloco de tags nao parte a partida ao meio.
        if is_header and saw_body and offset > boundaries[-1][0]:
            boundaries.append((offset, offset))
            saw_body = False
        elif not is_header:
            saw_body = True

    return boundaries


def split_games(
    raw_text: str,
    separator_pattern: str | None = DEFAULT_SEPARATOR_PATTERN,
) -> list[GameSegment]:
    """Divide o texto bruto em segmentos. Sempre devolve pelo menos um."""
    boundaries = _boundaries(raw_text, separator_pattern)

    segments: list[GameSegment] = []
    for index, (start_offset, content_start) in enumerate(boundaries):
        end_offset = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(raw_text)
        chunk = raw_text[content_start:end_offset]
        headers, body, consumed = extract_pgn_headers(chunk)

        segments.append(
            GameSegment(
                index=index,
                headers=headers,
                body=body,
                raw_offset=content_start + consumed,
                start_offset=start_offset,
                end_offset=end_offset,
                content_offset=content_start,
                title=build_segment_title(index, headers, body),
            )
        )

    return segments


def find_segment_at(segments: list[GameSegment], position: int) -> GameSegment | None:
    """O segmento que contem a posicao. Fora dos limites, o mais proximo."""
    if not segments:
        return None
    if position < segments[0].start_offset:
        return segments[0]
    for segment in segments:
        if segment.contains(position):
            return segment
    return segments[-1]


def index_at(segments: list[GameSegment], position: int) -> int:
    segment = find_segment_at(segments, position)
    return segment.index if segment else 0


# ---------------------------------------------------------------------------
# Titulo automatico
# ---------------------------------------------------------------------------


def _year_of(date_value: str | None) -> str:
    match = _YEAR_RE.match((date_value or "").strip())
    return match.group(1) if match else ""


def _clean(value: str | None) -> str:
    cleaned = (value or "").strip()
    return "" if cleaned in {"", "?", "??"} else cleaned


def _body_snippet(body: str, limit: int = 46) -> str:
    """Primeira linha util do corpo -- serve de titulo enquanto não há cabecalho."""
    for line in body.splitlines():
        text = line.strip().strip("{}").strip()
        if text:
            return text[:limit] + ("..." if len(text) > limit else "")
    return ""


def build_segment_title(index: int, headers: dict[str, str], body: str) -> str:
    """`13. Smyslov - Rudakovsky, Moscou 1945`, com o que houver disponivel."""
    number = index + 1

    white = _clean(headers.get("White"))
    black = _clean(headers.get("Black"))
    if white or black:
        title = f"{number}. {white or '?'} - {black or '?'}"
        tail = " ".join(part for part in (_clean(headers.get("Site")), _year_of(headers.get("Date"))) if part)
        return f"{title}, {tail}" if tail else title

    event = _clean(headers.get("Event"))
    if event:
        return f"{number}. {event}"

    snippet = _body_snippet(body)
    return f"{number}. {snippet}" if snippet else f"Partida {number}"
