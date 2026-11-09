"""Normalizacao **so do que e inequivoco**.

O que sai daqui e o que nao depende de contexto: Unicode, glifos de peca,
variantes de traco, roque com zeros, simbolos de avaliacao e espacamento de
numero de lance. Nenhuma dessas transformacoes pode mudar o sentido de uma
palavra de prosa.

Tudo que depende de contexto -- letra de peca por idioma (`C`->`N`), ruido de
OCR (`tD`->`N`), `ch`->`+`, `mate`->`#` -- mudou-se para `candidates.py`, onde
so e tentado sobre um token com cara de lance e so e aceito se der um lance
legal na posicao. Era esse acoplamento que transformava `chances` em `+ances`
e `Cada` em `Nada`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from .nag_table import BOOK_SYMBOL_ALIASES, BOOK_SYMBOL_PATTERN

# Onde um comentario termina -- regra unica, usada aqui e no tokenizador.
#
# Duas exigencias opostas se encontram aqui. Uma pagina de livro quebra o texto
# em linhas, entao um comentario legitimo **precisa** poder ocupar varias linhas.
# Ao mesmo tempo, uma `{` recem-digitada nao pode engolir o resto do capítulo
# (era o D7). A regra que atende as duas: **se existe `}` no mesmo parágrafo, o
# comentario vai ate la, atravessando quantas linhas forem; se nao existe, ele
# para no fim da linha.** A linha em branco e o limite porque nenhum parágrafo
# de livro tem uma no meio -- e uma `}` do outro lado dela pertence a outro
# comentario, nao a este.
COMMENT_PATTERN = (
    r"\{(?:[^}\n]|\n(?![ \t]*(?:\n|$)))*\}"  # fechado: pode ocupar varias linhas
    r"|\{[^}\n]*"  # aberto: vale so ate o fim da linha
)
COMMENT_RE = re.compile(COMMENT_PATTERN, re.MULTILINE)


def comment_span_end(text: str, start: int) -> int:
    """Fim (exclusivo) do comentario aberto em `start`."""
    match = COMMENT_RE.match(text, start)
    return match.end() if match else start + 1


@dataclass
class NormalizedText:
    """Texto normalizado + o intervalo de origem de cada caractere no texto bruto."""

    text: str
    raw_spans: list[tuple[int, int]]

    def raw_span_for(self, start: int, end: int) -> tuple[int, int]:
        if start >= end or start < 0 or end > len(self.raw_spans):
            return (start, end)

        spans = self.raw_spans[start:end]
        return (min(span[0] for span in spans), max(span[1] for span in spans))


class LiveTextNormalizer:
    """Limpa o texto sem nunca alterar o sentido de uma palavra."""

    PIECE_GLYPHS = {
        "♔": "K",
        "♕": "Q",
        "♖": "R",
        "♗": "B",
        "♘": "N",
        "♙": "",
        "♚": "K",
        "♛": "Q",
        "♜": "R",
        "♝": "B",
        "♞": "N",
        "♟": "",
    }

    UNICODE_REPLACEMENTS = {
        "–": "-",
        "—": "-",
        "−": "-",
        "′": "'",
        "`": "'",
        "´": "'",
        "“": '"',
        "”": '"',
        "­": "",  # hífen condicional herdado do PDF
        "​": "",  # espaco de largura zero
        "﻿": "",  # BOM no meio do texto
        # Sinal de captura dos livros espanhois e russos (`C×e5`, `A×C`). Fora
        # da notacao ele so aparece em medidas (`3 × 3`), que nao mudam de sentido.
        "×": "x",
    }

    # Substituicoes seguras: ancoradas em pontuacao e em contexto de notacao,
    # nunca em letras soltas, e por isso incapazes de corromper prosa.
    #
    # `+-` e `-+` **nao** estao aqui de proposito. Eles ja sao os glifos
    # canonicos de `$18` e `$19` ("brancas ganham" / "pretas ganham"), e a
    # versao anterior os reescrevia como `±` e `∓`, que sao `$16` e `$17`
    # ("claramente melhor"). Todo `+–` de um livro -- 191 deles so no Najdorf
    # Bg5 Revisited -- virava meia avaliacao a menos, sem aviso nenhum.
    SYMBOL_PATTERNS: tuple[tuple[str, str], ...] = (
        (r"\b0-0-0\b", "O-O-O"),
        (r"\b0-0\b", "O-O"),
        (r"(?<=[a-h1-8QRBNO])\+\+", "#"),
        (r"=/\+", "⩱"),
        (r"\+/=", "⩲"),
        (r"-/\+", "∓"),
        (r"\+/-", "±"),
    )

    # Numero de lance: consome **todos** os pontos consecutivos.
    # A versao anterior parava em 3, e por isso `10. ...` virava `10... .`
    # -- o ponto orfao que foi parar nos PGN ja exportados.
    # O lookahead impede que decimais de prosa (`2.5 vezes`) sejam tocados, e
    # `\d{1,3}` impede que anos (`1945.`) sejam lidos como numero de lance.
    #
    # A virgula depois das reticencias (`2. ..., C3AR`) e do estilo espanhol e
    # e consumida junto: sem isso o lance das pretas era lido como das brancas.
    MOVE_NUMBER_PATTERN = r"\b(\d{1,3})\s*((?:\.\s*)+)(?:,\s*)?(?=[A-Za-z(]|[0O]-[0O]|--|$)"

    # Livro ingles numera sem ponto: `1 P-K4 P-K4 2 N-KB3`. O numero so vira
    # numero de lance se o que vem depois tiver cara de lance -- descritivo
    # (`P-K4`, `N-KB3`, `P4R`, `PxP`) ou SAN (`e4`, `Nf3`, `O-O`). Prosa como
    # `2 Brühl-Philidor` ou `London 1783` nao casa: tem minuscula, ou quatro digitos.
    UNDOTTED_MOVE_NUMBER_PATTERN = (
        r"(?<![\w.\-/])(\d{1,3})[ \t]+(?="
        r"(?:KT|Kt|[PNBRQKCTDA]){1,3}(?:-|[x×])"  # P-K4, N-KB3, PxP
        r"|[PCTDRA][1-8](?:[A-Z]|[ \t,;)!?+]|$)"  # P4R, C3AR, T1D
        r"|[PCTDRA][A-Z]{1,2}[1-8x×]"  # CR5R, PDxP
        r"|[KQRBNCDTA]?[a-h]?[1-8]?x?[a-h][1-8]"  # e4, Nf3, Cxe5
        r"|[O0]-[O0]"
        r")"
    )
    # Palavras que antecedem um numero que nao e de lance (`diagrama 23 ...`).
    NOT_A_MOVE_NUMBER_BEFORE = re.compile(
        r"(?<![^\W\d_])(?:diagram|diagrama|dia|fig|figura|figure|page|p|pag|pág|página|pagina|no|n|nº|núm|num|número|numero"
        r"|chapter|capítulo|capitulo|game|partida|ex|exercício|exercise|ejemplo|exemplo|example)\.?\s*$",
        re.IGNORECASE,
    )

    # Xeque solto (`Q-R5 +`, `Dh5 +`): o OCR separa o sinal do lance, e sozinho
    # ele ia para o PGN como comentario `{+}`.
    # So depois de algo que termina como lance (`Q-R5`, `BxP`, `O-O`): prosa
    # termina em minuscula, e `brancas + pretas` fica como esta.
    DETACHED_CHECK_PATTERN = r"(?<=[A-Z0-9])[ \t]+(\+\+|\+|#)(?=[ \t\n)]|$)"

    # Captura com espacos (`P x P`, `B xP`), como o OCR entrega os livros ingleses.
    SPACED_CAPTURE_PATTERN = (
        r"(?<![A-Za-z0-9])((?:KT|Kt|[PNBRQKCTDA]){1,3})(?:[ \t]+[x×][ \t]*|[ \t]*[x×][ \t]+)"
        r"(?=(?:KT|Kt|[PNBRQKCTDA]))"
    )
    # Origem entre parenteses na descritiva: `C(4D)5C`, `T(1A)1D`, `N(Q2)-B3`.
    # Sem isso o `(` abria uma variante no meio do lance.
    DESCRIPTIVE_ORIGIN_PATTERN = (
        r"(?<![A-Za-z0-9])((?:KT|Kt|[PNBRQKCTDA])[A-Z]{0,2})\(([1-8]?[A-Z]{0,3}[1-8]?)\)(-?)(?=[1-8A-Zx×])"
    )

    def __init__(self, locale: str = "en"):
        # `locale` ja nao decide letra de peca aqui (isso e do CandidateResolver),
        # mas segue registrado para quem monta o pipeline a partir do normalizador.
        self.locale = locale

    # ------------------------------------------------------------------
    # Mapeamento de spans (o que permite marcar o lance no texto bruto)
    # ------------------------------------------------------------------

    def _expand_spans(self, source_spans: list[tuple[int, int]], output_length: int) -> list[tuple[int, int]]:
        if output_length <= 0:
            return []

        if not source_spans:
            return [(0, 0)] * output_length

        if output_length == 1:
            return [(source_spans[0][0], source_spans[-1][1])]

        source_length = len(source_spans)
        if output_length <= source_length:
            spans: list[tuple[int, int]] = []
            for index in range(output_length):
                chunk_start = int(index * source_length / output_length)
                chunk_end = max(chunk_start + 1, int((index + 1) * source_length / output_length))
                chunk = source_spans[chunk_start:chunk_end]
                spans.append((chunk[0][0], chunk[-1][1]))
            return spans

        tail_anchor = source_spans[-1][1]
        spans = list(source_spans)
        spans.extend([(tail_anchor, tail_anchor)] * (output_length - source_length))
        return spans

    def _normalize_unicode(self, text: str, raw_spans: list[tuple[int, int]]) -> NormalizedText:
        normalized_chars: list[str] = []
        normalized_spans: list[tuple[int, int]] = []

        for char, raw_span in zip(text, raw_spans, strict=False):
            translated = self.UNICODE_REPLACEMENTS.get(char, char)
            if not translated:
                continue

            normalized_char = unicodedata.normalize("NFKC", translated)
            if not normalized_char:
                continue

            normalized_chars.append(normalized_char)
            normalized_spans.extend(self._expand_spans([raw_span], len(normalized_char)))

        return NormalizedText("".join(normalized_chars), normalized_spans)

    def _replace_pattern(
        self, text: str, raw_spans: list[tuple[int, int]], pattern: str, replacement
    ) -> NormalizedText:
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0

        for match in re.finditer(pattern, text):
            start, end = match.span()
            parts.append(text[cursor:start])
            spans.extend(raw_spans[cursor:start])

            replacement_text = replacement(match) if callable(replacement) else replacement
            source_spans = raw_spans[start:end]
            parts.append(replacement_text)
            spans.extend(self._expand_spans(source_spans, len(replacement_text)))
            cursor = end

        parts.append(text[cursor:])
        spans.extend(raw_spans[cursor:])
        return NormalizedText("".join(parts), spans)

    def _apply_outside_comments(self, text: str, raw_spans: list[tuple[int, int]], transform) -> NormalizedText:
        """Executa `transform` so fora de `{...}`.

        O limite do comentario e o de `comment_span_end`: fechado, ele pode
        ocupar varias linhas; aberto, para no fim da linha, para que digitar `{`
        nao faca o resto do capítulo parar de ser analisado.
        """
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0
        index = 0

        while index < len(text):
            if text[index] != "{":
                index += 1
                continue

            if cursor < index:
                normalized = transform(text[cursor:index], raw_spans[cursor:index])
                parts.append(normalized.text)
                spans.extend(normalized.raw_spans)

            comment_slice_end = comment_span_end(text, index)

            parts.append(text[index:comment_slice_end])
            spans.extend(raw_spans[index:comment_slice_end])
            index = comment_slice_end
            cursor = comment_slice_end

        if cursor < len(text):
            normalized = transform(text[cursor:], raw_spans[cursor:])
            parts.append(normalized.text)
            spans.extend(normalized.raw_spans)

        return NormalizedText("".join(parts), spans)

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _replace_book_symbols(self, text: str, raw_spans: list[tuple[int, int]]) -> NormalizedText:
        return self._replace_pattern(
            text,
            raw_spans,
            BOOK_SYMBOL_PATTERN,
            lambda match: BOOK_SYMBOL_ALIASES[match.group(0)],
        )

    @staticmethod
    def _format_descriptive_origin(match: re.Match) -> str:
        piece, origin = match.group(1), match.group(2)
        following = match.string[match.end() : match.end() + 1]
        # `N(2)xP` e captura: o traco so entra antes de uma casa de destino.
        separator = "" if following in ("x", "×") else "-"
        return f"{piece}/{origin}{separator}"

    def _format_undotted_move_number(self, match: re.Match, whole: str) -> str:
        number = match.group(1)
        if int(number) == 0 or self.NOT_A_MOVE_NUMBER_BEFORE.search(whole[: match.start()]):
            return match.group(0)
        return f"{number}. "

    @staticmethod
    def _format_move_number(match: re.Match) -> str:
        number = match.group(1)
        dots = match.group(2).count(".")
        return f"{number}... " if dots >= 2 else f"{number}. "

    def normalize_with_mapping(self, raw_text: str, base_offset: int = 0) -> NormalizedText:
        raw_spans = [(base_offset + index, base_offset + index + 1) for index in range(len(raw_text))]

        # Simbolo de livro **antes** do NFKC, e nao depois: a fonte de simbolos
        # da Thinkers/Quality/Everyman reaproveita codepoints latinos, e o NFKC
        # converte `²` em `2`, `³` em `3` e `¹` em `1`. Depois dele, `Rad8³`
        # chega ao tokenizador como `Rad83` e a avaliacao ja nao existe.
        normalized = self._apply_outside_comments(raw_text, raw_spans, self._replace_book_symbols)
        text, raw_spans = normalized.text, normalized.raw_spans

        normalized = self._normalize_unicode(text, raw_spans)
        text, raw_spans = normalized.text, normalized.raw_spans

        glyph_chars: list[str] = []
        glyph_spans: list[tuple[int, int]] = []
        for char, raw_span in zip(text, raw_spans, strict=False):
            replacement = self.PIECE_GLYPHS.get(char, char)
            if not replacement:
                continue
            glyph_chars.append(replacement)
            glyph_spans.extend(self._expand_spans([raw_span], len(replacement)))
        text = "".join(glyph_chars)
        raw_spans = glyph_spans

        for pattern, replacement in self.SYMBOL_PATTERNS:
            normalized = self._apply_outside_comments(
                text,
                raw_spans,
                lambda current_text, current_spans, p=pattern, r=replacement: self._replace_pattern(
                    current_text, current_spans, p, r
                ),
            )
            text, raw_spans = normalized.text, normalized.raw_spans

        descriptive_steps: tuple[tuple[str, Callable[[re.Match], str]], ...] = (
            (self.DETACHED_CHECK_PATTERN, lambda match: match.group(1)),
            (self.SPACED_CAPTURE_PATTERN, lambda match: f"{match.group(1)}x"),
            (self.DESCRIPTIVE_ORIGIN_PATTERN, self._format_descriptive_origin),
        )
        for step_pattern, step_replacement in descriptive_steps:
            normalized = self._apply_outside_comments(
                text,
                raw_spans,
                lambda current_text, current_spans, p=step_pattern, r=step_replacement: self._replace_pattern(
                    current_text, current_spans, p, r
                ),
            )
            text, raw_spans = normalized.text, normalized.raw_spans

        normalized = self._apply_outside_comments(
            text,
            raw_spans,
            lambda current_text, current_spans: self._replace_pattern(
                current_text,
                current_spans,
                self.UNDOTTED_MOVE_NUMBER_PATTERN,
                lambda match: self._format_undotted_move_number(match, current_text),
            ),
        )
        text, raw_spans = normalized.text, normalized.raw_spans

        return self._apply_outside_comments(
            text,
            raw_spans,
            lambda current_text, current_spans: self._replace_pattern(
                current_text,
                current_spans,
                self.MOVE_NUMBER_PATTERN,
                self._format_move_number,
            ),
        )

    def normalize(self, raw_text: str) -> str:
        return self.normalize_with_mapping(raw_text).text
