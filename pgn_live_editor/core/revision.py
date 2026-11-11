"""Referencia a um trecho do texto, conferida antes de editar (SPEC_V3 §4.2, R-302).

Um alerta, um registro ou um no da arvore apontam para offsets do texto que
foi analisado. Entre a analise e o gesto o usuario pode ter digitado; aplicar
os offsets velhos ao texto novo edita o lugar errado (U-05). `SpanRef` guarda
o texto de origem e diz onde o trecho esta agora -- ou que nao da para saber.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpanRef:
    # O texto em que `start`/`end` valem. E uma referencia ao str que ja existe
    # (a analise o guarda), nao uma copia.
    origin: str
    start: int
    end: int

    @property
    def expected(self) -> str:
        return self.origin[self.start : self.end]

    def locate(self, current: str) -> tuple[int, int] | None:
        """Onde o trecho esta em `current`, ou `None` se a edicao o atingiu.

        A diferenca entre os dois textos e reduzida ao trecho do meio entre o
        maior prefixo e o maior sufixo comuns -- o que uma digitacao produz. O
        span antes desse trecho fica onde estava; depois dele, desloca pelo
        tamanho da edicao; encostado ou dentro dele, nao se sabe mais.
        """
        if not 0 <= self.start <= self.end <= len(self.origin):
            return None
        if current == self.origin:
            return self.start, self.end
        prefix = _common_prefix(self.origin, current)
        if self.end < prefix:
            return self.start, self.end
        suffix = _common_suffix(self.origin, current, limit=min(len(self.origin), len(current)) - prefix)
        if self.start > len(self.origin) - suffix:
            shift = len(current) - len(self.origin)
            return self.start + shift, self.end + shift
        return None


def _common_prefix(a: str, b: str) -> int:
    """Tamanho do maior prefixo comum, por busca binaria com comparacoes em C."""
    low, high = 0, min(len(a), len(b))
    while low < high:
        middle = (low + high + 1) // 2
        if a[:middle] == b[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def _common_suffix(a: str, b: str, *, limit: int) -> int:
    low, high = 0, max(0, limit)
    while low < high:
        middle = (low + high + 1) // 2
        if a[len(a) - middle :] == b[len(b) - middle :]:
            low = middle
        else:
            high = middle - 1
    return low
