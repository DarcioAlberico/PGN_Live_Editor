"""Modelo de alerta do parser.

Regra do projeto (SPEC 0, principio 3): **duvidar e melhor que adivinhar**.
Tudo que o parser corrige, descarta ou nao entende vira um `Issue` visivel,
com o trecho exato no texto bruto, o grau de confianca e as acoes possiveis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["info", "warning", "error"]

# Acoes que o painel de alertas oferece.
ACTION_ACCEPT = "accept"  # grava a correcao no texto bruto
ACTION_IGNORE = "ignore"  # silencia este token nesta sessao/projeto
ACTION_LEARN = "learn"  # grava no dicionario do projeto
ACTION_TO_COMMENT = "to_comment"  # envolve o trecho em chaves


@dataclass(frozen=True)
class Candidate:
    """Uma hipotese de lance legal para um token que nao era SAN valido."""

    san: str
    confidence: float
    source: str  # 'dicionario' | 'idioma' | 'ocr' | 'levenshtein'
    rationale: str

    def __str__(self) -> str:
        return f"{self.san} ({self.confidence:.0%}, {self.source})"


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    raw_start: int
    raw_end: int
    raw_text: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    is_cascade: bool = False

    @property
    def best_candidate(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    # Compatibilidade com o formato antigo `(posicao, mensagem, nivel)`.
    def __getitem__(self, index: int):
        return (self.raw_start, self.message, self.severity)[index]

    def __len__(self) -> int:
        return 3


# Codigos usados pelo parser --------------------------------------------------

UNRECOGNIZED_MOVE = "UNRECOGNIZED_MOVE"
AUTO_CORRECTED = "AUTO_CORRECTED"
AMBIGUOUS_MOVE = "AMBIGUOUS_MOVE"
ILLEGAL_MOVE = "ILLEGAL_MOVE"
UNCLOSED_BRACE = "UNCLOSED_BRACE"
UNBALANCED_PAREN = "UNBALANCED_PAREN"
ORPHAN_VARIATION = "ORPHAN_VARIATION"
INVALID_DATE = "INVALID_DATE"
INVALID_RESULT = "INVALID_RESULT"
MISSING_SETUP = "MISSING_SETUP"

# Fase 5: nao vem do parser, vem do motor de xadrez (services/engine_service.py).
# Lance de linha principal que despenca a avaliacao sem o livro marcar com '?'.
SUSPECT_BLUNDER = "SUSPECT_BLUNDER"

SEVERITY_ORDER: dict[str, int] = {"error": 0, "warning": 1, "info": 2}

SEVERITY_LABEL: dict[str, str] = {
    "error": "Erro",
    "warning": "Aviso",
    "info": "Info",
}


def sort_issues(issues: list[Issue]) -> list[Issue]:
    """Erros primeiro; dentro de cada nivel, na ordem em que aparecem no texto."""
    return sorted(issues, key=lambda issue: (SEVERITY_ORDER.get(issue.severity, 9), issue.raw_start))


def count_by_severity(issues: list[Issue]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        if issue.severity in counts:
            counts[issue.severity] += 1
    return counts
