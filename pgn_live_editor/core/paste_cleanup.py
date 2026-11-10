"""Limpeza do texto que vem de um PDF de xadrez.

Substitui o antigo *Join Lines* (uma unica regex) por um conjunto de passos
independentes, cada um desligavel, com previa antes de aplicar. Nada aqui muda
o sentido de uma palavra: sao operacoes de layout, nao de notacao.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .nag_table import map_book_symbols

# Linha que e so um numero de pagina, com ou sem enfeite: "12", "- 12 -", "[12]".
PAGE_NUMBER_RE = re.compile(r"^\s*[-–—\[\|]?\s*\d{1,4}\s*[-–—\]\|]?\s*$")
# Marca de nota de rodape colada num lance: "Nf3¹", "e4*", "Bb5†".
FOOTNOTE_MARK_RE = re.compile(r"(?<=[a-h1-8QRBNO])[¹²³⁰-₟†‡\*]+(?=\s|$)")
INVISIBLE_RE = re.compile(r"[­​‌‍﻿]")


@dataclass
class CleanupOptions:
    #: Precisa vir **antes** de `remove_footnote_marks`: `²` e `³` sao ao
    #: mesmo tempo avaliacao de livro e marca de nota de rodape, e num livro
    #: de xadrez sao avaliacao em praticamente toda ocorrencia -- 212 delas
    #: so no Najdorf Bg5 Revisited, contra zero notas de rodape.
    map_book_symbols: bool = True
    remove_invisible: bool = True
    dehyphenate: bool = True
    join_wrapped_lines: bool = True
    remove_page_numbers: bool = True
    remove_repeated_headers: bool = True
    remove_footnote_marks: bool = True
    collapse_whitespace: bool = True


@dataclass
class CleanupReport:
    text: str
    removed_lines: list[str] = field(default_factory=list)
    joined_hyphens: int = 0
    joined_lines: int = 0
    removed_marks: int = 0
    mapped_symbols: int = 0

    def summary(self) -> str:
        parts: list[str] = []
        if self.mapped_symbols:
            parts.append(f"{self.mapped_symbols} símbolo(s) de livro convertido(s)")
        if self.joined_hyphens:
            parts.append(f"{self.joined_hyphens} palavra(s) remontada(s)")
        if self.joined_lines:
            parts.append(f"{self.joined_lines} quebra(s) de linha juntada(s)")
        if self.removed_lines:
            parts.append(f"{len(self.removed_lines)} linha(s) removida(s)")
        if self.removed_marks:
            parts.append(f"{self.removed_marks} marca(s) de nota removida(s)")
        return " | ".join(parts) if parts else "Nada a limpar."


# Resultado e movetext se repetem de verdade (tres partidas que terminam `1-0`):
# nunca sao cabecalho/rodape de pagina. Antes eram apagados como tal (N-11).
_NEVER_RUNNING_HEADER_RE = re.compile(r"^(?:1-0|0-1|1/2-1/2|½-½|\*)$|\d+\s*\.")


def _repeated_lines(lines: list[str], minimum_occurrences: int = 3) -> set[str]:
    """Cabecalhos e rodapés correntes: a mesma linha curta repetida a cada pagina."""
    counter = Counter(
        line.strip()
        for line in lines
        if 3 <= len(line.strip()) <= 80 and not _NEVER_RUNNING_HEADER_RE.search(line.strip())
    )
    return {line for line, count in counter.items() if count >= minimum_occurrences}


def clean_pdf_text(text: str, options: CleanupOptions | None = None) -> CleanupReport:
    options = options or CleanupOptions()
    report = CleanupReport(text=text)

    if options.map_book_symbols:
        text, report.mapped_symbols = map_book_symbols(text)

    if options.remove_invisible:
        text = INVISIBLE_RE.sub("", text)

    if options.dehyphenate:
        # So junta quando a continuacao vem em minuscula: preserva "Ruy-\nLopez"
        # e nomes compostos que quebraram no fim da linha.
        text, count = re.subn(r"(\w)[-‐‑]\s*\n\s*([a-zà-ÿ])", r"\1\2", text)
        report.joined_hyphens = count

    lines = text.splitlines()

    if options.remove_page_numbers or options.remove_repeated_headers:
        repeated = _repeated_lines(lines) if options.remove_repeated_headers else set()
        kept: list[str] = []
        for line in lines:
            stripped = line.strip()
            if options.remove_page_numbers and stripped and PAGE_NUMBER_RE.match(stripped):
                report.removed_lines.append(stripped)
                continue
            if repeated and stripped in repeated:
                report.removed_lines.append(stripped)
                continue
            kept.append(line)
        lines = kept

    text = "\n".join(lines)

    if options.remove_footnote_marks:
        text, count = FOOTNOTE_MARK_RE.subn("", text)
        report.removed_marks = count

    if options.join_wrapped_lines:
        # Quebra simples vira espaco; linha em branco (parágrafo) e preservada.
        # Linha so com espacos tambem e linha em branco (N-11).
        text = re.sub(r"(?m)^[ \t]+$", "", text)
        text, count = re.subn(r"(?<!\n)\n(?!\n)", " ", text)
        report.joined_lines = count

    if options.collapse_whitespace:
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" +([,.;:!?])", r"\1", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.rstrip() for line in text.splitlines())

    report.text = text.strip()
    return report
