"""Quebra o texto normalizado em tokens estruturais.

Novidades da Fase 2:

- `MOVE_CANDIDATE`: token com cara de lance que nao e SAN valido. Antes ele caia
  no catch-all `TEXT` e era **absorvido como comentario** -- foi assim que
  `tDc6` sumiu de uma partida sem gerar um unico aviso.
- comentario delimitado por linha: uma `{` sem fechamento nao engole mais o
  resto do documento enquanto o usuario digita.
- `NULL_MOVE` (`--`, `Z0`) para nao quebrar a arvore em posicoes de estudo.
- `RESULT` so no fim de uma linha, para que um `*` de prosa nao vire resultado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .candidates import looks_like_move
from .nag_table import suffix_nag_pattern, symbolic_nag_pattern
from .normalizer import COMMENT_PATTERN, NormalizedText

# Tipos de token que estabelecem "contexto de notacao" ao redor de um candidato.
#
# `COMMENT` esta aqui porque o livro escreve `18.Tf2 {e se} Dc8 {entao} 19.Tc1`
# o tempo todo: um lance entre dois comentarios continua sendo um lance. Sem
# isso ele era descartado como prosa -- e um lance perdido em silencio e o pior
# defeito possivel neste programa (D3). O preco e um alerta visivel a mais
# quando prosa entre chaves tem cara de lance, o que e exatamente o negocio que
# a SPEC manda fazer: duvidar em vez de adivinhar.
#
# Os parenteses de variante tambem: `(2 P-KB4 PxP)` e `... N-KB3 (2 P-KB4` perdiam
# o lance da ponta, engolido como prosa, quando o vizinho do outro lado era texto.
NOTATION_NEIGHBOURS = {
    "MOVE_NUMBER",
    "SAN_MOVE",
    "MOVE_CANDIDATE",
    "NULL_MOVE",
    "COMMENT",
    "VARIANT_OPEN",
    "VARIANT_CLOSE",
}


@dataclass
class Token:
    type: str
    value: str
    start_index: int
    end_index: int
    normalized_start_index: int | None = None
    normalized_end_index: int | None = None
    # Um candidato so vira alerta se estiver cercado de notacao. Isso impede
    # que prosa com numeros ("C45", "1945") encha o painel de falsos erros.
    in_notation_context: bool = False


class PGNTokenizer:
    TRADITIONAL_NAG_PATTERN = r"(?:!!|\?\?|!\?|\?!|!|\?)"
    # Vem da tabela unica (`nag_table`), do glifo mais longo para o mais curto:
    # a alternancia do `re` para no primeiro que casar, e sem essa ordem `+`
    # casaria antes de `+-` e o "brancas ganham" do livro viraria outra coisa.
    SYMBOLIC_NAG_PATTERN = symbolic_nag_pattern()
    # Sufixo de livro (SPEC 2.2): so casa colado no lance anterior. E o que
    # resolve o `N` de novidade sem confundi-lo com o cavalo.
    SUFFIX_NAG_PATTERN = suffix_nag_pattern()
    NAG_PATTERN = rf"(?:\$\d+|{TRADITIONAL_NAG_PATTERN}|{SYMBOLIC_NAG_PATTERN}|{SUFFIX_NAG_PATTERN})"

    TOKEN_PATTERNS = [
        # O limite do comentario mora no normalizador: o tokenizador usa
        # exatamente o mesmo padrao, para os dois nunca discordarem.
        ("COMMENT", COMMENT_PATTERN),
        ("MOVE_NUMBER", r"\b\d+\s*(?:\.\.\.|\.\.)?\s*\."),
        (
            "SAN_MOVE",
            r"(?<!\S)(?:"
            r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[KQRBN])?[\+#]?|"
            r"[O0]-[O0]-[O0][\+#]?|"
            r"[O0]-[O0][\+#]?"
            rf")(?=\s|$|[(){{}}]|{NAG_PATTERN})",
        ),
        ("NULL_MOVE", r"(?<![\w-])(?:--|Z0)(?=\s|$|[(){}])"),
        ("VARIANT_OPEN", r"\("),
        ("VARIANT_CLOSE", r"\)"),
        ("RESULT", r"(?:1-0|0-1|1/2-1/2|\*)(?=[ \t]*$)"),
        ("NAG", NAG_PATTERN),
        ("MOVE_CANDIDATE", r"[^\s(){};,]+"),
        ("TEXT", r"[^\s()]+"),
    ]

    def __init__(self) -> None:
        regex_parts = [f"(?P<{name}>{pattern})" for name, pattern in self.TOKEN_PATTERNS]
        self.tokenizer_regex = re.compile("|".join(regex_parts), re.MULTILINE)

    def tokenize(self, source: str | NormalizedText) -> list[Token]:
        if isinstance(source, NormalizedText):
            text = source.text
            normalized = source
        else:
            text = source
            normalized = None

        tokens: list[Token] = []
        for match in self.tokenizer_regex.finditer(text):
            for name, _pattern in self.TOKEN_PATTERNS:
                value = match.group(name)
                if not value or not value.strip():
                    continue

                token_type = name
                cleaned = value.strip()

                # A final period is sentence punctuation in prose-heavy book
                # notation (`... B3T.`), not part of the descriptive token.
                if token_type == "MOVE_CANDIDATE" and cleaned.endswith("."):
                    cleaned = cleaned.rstrip(".")
                    if not cleaned:
                        continue

                # O regex de candidato e amplo de proposito; o filtro semantico
                # ("tem cara de lance?") decide se ele vira candidato ou prosa.
                # Prosa volta com a pontuacao que tinha: o ponto so sai de quem
                # continua lance (N-04; antes "good." chegava ao PGN como "good").
                if token_type == "MOVE_CANDIDATE" and not looks_like_move(cleaned):
                    token_type = "TEXT"
                    cleaned = value.strip()

                normalized_start = match.start(name)
                normalized_end = match.end(name)
                if token_type == "MOVE_CANDIDATE":
                    normalized_end -= len(value.strip()) - len(cleaned)

                if normalized is None:
                    start_index = normalized_start
                    end_index = normalized_end
                else:
                    start_index, end_index = normalized.raw_span_for(normalized_start, normalized_end)

                tokens.append(
                    Token(
                        type=token_type,
                        value=cleaned,
                        start_index=start_index,
                        end_index=end_index,
                        normalized_start_index=normalized_start,
                        normalized_end_index=normalized_end,
                    )
                )
                break

        self._mark_notation_context(tokens)
        return tokens

    @staticmethod
    def _mark_notation_context(tokens: list[Token]) -> None:
        separators = {",", ";"}
        for index, token in enumerate(tokens):
            if token.type != "MOVE_CANDIDATE":
                continue

            previous = index - 1
            while previous >= 0 and tokens[previous].type == "TEXT" and tokens[previous].value in separators:
                previous -= 1
            following = index + 1
            while (
                following < len(tokens) and tokens[following].type == "TEXT" and tokens[following].value in separators
            ):
                following += 1
            previous_type = tokens[previous].type if previous >= 0 else None
            next_type = tokens[following].type if following < len(tokens) else None
            token.in_notation_context = previous_type in NOTATION_NEIGHBOURS or next_type in NOTATION_NEIGHBOURS


def comment_is_unclosed(value: str) -> bool:
    return value.startswith("{") and not value.endswith("}")
