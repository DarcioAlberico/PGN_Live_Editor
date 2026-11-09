"""Em que idioma esta a notacao do texto colado?

Nao e uma resposta sobre a **prosa** -- um livro em espanhol pode ter a notacao
inglesa -- e sim sobre as letras dos lances. Cada idioma tem sinais que so ele
usa: `N` e `Q` so existem no SAN ingles, `A` de alfil so no espanhol (e no
italiano), `S` e `L` so no alemao, `F` so no frances; na descritiva, `P-K4` e
inglesa, `A5C` e `P4AD` espanholas, `B5C` e `P4BD` portuguesas.

A deteccao so **sugere**: quem troca o idioma e o usuario, na caixa do editor.
Sem sinal claro -- um texto so com `e4`, `Rc1` e `Tc1`, que servem a varios
idiomas -- ela nao diz nada.
"""

from __future__ import annotations

import re

from .normalizer import COMMENT_RE

# Quanto texto olhar. Um capitulo basta para decidir, e o livro inteiro (meio
# milhao de caracteres) custaria a cada pausa na digitacao.
SAMPLE_CHARS = 60_000

_SQUARE = r"[a-h]?[1-8]?x?[a-h][1-8]"

# (idiomas que o sinal indica, padrao). Um sinal que serve a dois idiomas conta
# para os dois; o que decide e o sinal exclusivo.
SIGNALS: tuple[tuple[tuple[str, ...], re.Pattern[str]], ...] = (
    # SAN
    (("en",), re.compile(rf"(?<![\w-])[NQ]{_SQUARE}\b")),
    (("pt", "es", "fr", "it"), re.compile(rf"(?<![\w-])[CD]{_SQUARE}\b")),
    (("pt", "es", "fr", "it", "de"), re.compile(rf"(?<![\w-])T{_SQUARE}\b")),
    # `A` de alfil: espanhol ou italiano. O espanhol leva um pouco mais -- e o
    # que aparece nos livros deste programa, e a notacao dos dois e identica.
    (("es", "es", "it"), re.compile(rf"(?<![\w-])A{_SQUARE}\b")),
    (("de",), re.compile(rf"(?<![\w-])[SL]{_SQUARE}\b")),
    (("fr",), re.compile(rf"(?<![\w-])F{_SQUARE}\b")),
    # Descritiva inglesa: `P-K4`, `N-KB3`, `QN-Q2`, `PxP`
    (("en",), re.compile(r"(?<![\w-])(?:[KQ]?(?:KT|[RNB])?)?(?:KT|[PNBRQK])-[KQ]?(?:KT|[RNB])?[1-8]\b")),
    (("en",), re.compile(r"(?<![\w-])(?:[KQ]?[RNB])?(?:KT|[NBQ])x(?:[KQ]?[RNB])?(?:KT|[PNBRQK])\b")),
    # Descritiva espanhola: bispo `A`, colunas `AD`/`AR`
    (("es",), re.compile(r"(?<![\w-])[PCATDR][RD]?[1-8]A[RD]?\b|(?<![\w-])A[RD]?[1-8](?:[TCDR][RD]?|A[RD]?)?\b")),
    (("es",), re.compile(r"(?<![\w-])[PCATDR][RD]?[x×]A\b|(?<![\w-])Ax[PCATDR]\b")),
    # Descritiva portuguesa: bispo `B`, colunas `BD`/`BR`
    (("pt",), re.compile(r"(?<![\w-])[PCBTDR][RD]?[1-8]B[RD]?\b|(?<![\w-])B[RD]?[1-8](?:[TCDR][RD]?|B[RD]?)\b")),
    # Descritiva com casa antes da coluna, comum a portugues e espanhol: `P4R`, `C3C`
    (("pt", "es"), re.compile(r"(?<![\w-])[PCTDR][RD]?[1-8](?:[TCDR][RD]?)\b")),
)

MIN_SIGNALS = 3

# `B` de bispo serve ao ingles e ao portugues; ao lado de `C`/`D`/`T` so pode
# ser portugues.
_BISHOP_SAN = re.compile(rf"(?<![\w-])B{_SQUARE}\b")
_LATIN_PIECE_SAN = SIGNALS[1][1]


def detect_notation_locale(text: str) -> str | None:
    """O idioma com sinal exclusivo claramente mais forte, ou `None`."""
    sample = COMMENT_RE.sub(" ", text[:SAMPLE_CHARS])
    scores: dict[str, float] = {}
    for locales, pattern in SIGNALS:
        hits = len(pattern.findall(sample))
        if not hits:
            continue
        for locale in locales:
            # Sinal compartilhado vale menos que exclusivo: `Cf3` nao decide
            # entre portugues e espanhol, `Af4` decide.
            scores[locale] = scores.get(locale, 0.0) + hits / len(locales)
    if _LATIN_PIECE_SAN.search(sample):
        scores["pt"] = scores.get("pt", 0.0) + len(_BISHOP_SAN.findall(sample))
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < MIN_SIGNALS or best_score < 1.5 * runner_up:
        return None
    return best
