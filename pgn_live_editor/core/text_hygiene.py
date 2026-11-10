"""Higiene do texto que entra no editor (R-107 do docs/ROADMAP_V3.md).

Um surrogate UTF-16 sem par nao e caractere nenhum: e meio caractere que o
OCR, um PDF mal gerado ou a area de transferencia deixou passar. O Qt o aceita
e o devolve intacto, mas ele nao existe em UTF-8 -- o texto so explodia ao
salvar, com UnicodeEncodeError (S-13). Aqui ele vira U+FFFD, **um para um**:
o comprimento do texto nao muda, entao nenhum span calculado antes se desloca,
e o parser marca o lugar (`INVALID_CHARACTER`) para o usuario conferir.

Um caractere de verdade fora do plano basico (um emoji, por exemplo) chega ao
Python ja inteiro e nao e tocado.
"""

from __future__ import annotations

import re

REPLACEMENT_CHARACTER = "�"

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def replace_lone_surrogates(text: str) -> tuple[str, int]:
    """O texto com cada surrogate solitario trocado por U+FFFD, e quantos foram."""
    return _LONE_SURROGATE.subn(REPLACEMENT_CHARACTER, text)
