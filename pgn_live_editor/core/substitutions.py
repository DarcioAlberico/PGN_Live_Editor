"""Modelo do dicionario de OCR: substituicao com **escopo explicito**.

O escopo e a peca que faltava. Ate a fase 2 as substituicoes eram aplicadas ao
documento inteiro, as cegas, e por isso `ch -> +` transformava `chances` em
`+ances`. Hoje nada disso toca prosa -- toda substituicao so e tentada sobre um
token que ja tem cara de lance, e so e aceita se der um lance legal na posicao --
mas o **alcance dentro do token** continua importando:

| Escopo | Casa | Exemplo |
|---|---|---|
| `token` | o token inteiro, exato | `Bh5` -> `Bb5` |
| `prefixo` | so o comeco do token | `4J` -> `N`, e `4Jf3` vira `Nf3` |
| `livre` | qualquer trecho do token | `1` -> `l` no meio de uma casa |

`livre` e o unico que ainda pode surpreender, e por isso a interface o marca
como arriscado: dentro de um token de lance ele e inofensivo, mas e o escopo que
mais gera lance legal errado.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SCOPE_TOKEN = "token"
SCOPE_PREFIX = "prefixo"
SCOPE_FREE = "livre"
SCOPES = (SCOPE_TOKEN, SCOPE_PREFIX, SCOPE_FREE)

SCOPE_LABEL = {
    SCOPE_TOKEN: "token inteiro",
    SCOPE_PREFIX: "início do lance",
    SCOPE_FREE: "qualquer trecho (arriscado)",
}

LAYER_BASE = "base"
LAYER_GLOBAL = "global"
LAYER_PROJECT = "projeto"
# Da menos especifica para a mais especifica: a ultima vence.
LAYER_ORDER = (LAYER_BASE, LAYER_GLOBAL, LAYER_PROJECT)

LAYER_LABEL = {
    LAYER_BASE: "embutido",
    LAYER_GLOBAL: "meu dicionário",
    LAYER_PROJECT: "deste projeto",
}

ANY_LOCALE = "*"


@dataclass(frozen=True)
class Substitution:
    pattern: str
    replacement: str
    scope: str = SCOPE_TOKEN
    locale: str = ANY_LOCALE
    layer: str = LAYER_PROJECT
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """Duas entradas com a mesma chave sao a mesma regra: a mais especifica vence."""
        return (self.scope, self.pattern)

    def applies_to(self, locale: str) -> bool:
        return self.locale in (ANY_LOCALE, locale)

    def apply(self, token: str) -> str | None:
        """O token depois desta substituicao, ou `None` se ela nao casa."""
        if not self.pattern:
            return None

        if self.scope == SCOPE_TOKEN:
            return self.replacement if token == self.pattern else None

        if self.scope == SCOPE_PREFIX:
            if token.startswith(self.pattern) and len(token) > len(self.pattern):
                return self.replacement + token[len(self.pattern) :]
            return None

        if self.scope == SCOPE_FREE:
            if self.pattern in token:
                converted = token.replace(self.pattern, self.replacement)
                return converted if converted != token else None
            return None

        return None

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "replacement": self.replacement,
            "scope": self.scope,
            "locale": self.locale,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: Mapping, layer: str = LAYER_PROJECT) -> Substitution | None:
        pattern = str(payload.get("pattern", "")).strip()
        if not pattern:
            return None

        scope = str(payload.get("scope", SCOPE_TOKEN))
        return cls(
            pattern=pattern,
            replacement=str(payload.get("replacement", "")),
            scope=scope if scope in SCOPES else SCOPE_TOKEN,
            locale=str(payload.get("locale", ANY_LOCALE)) or ANY_LOCALE,
            layer=layer,
            note=str(payload.get("note", "")),
        )


class Dictionary:
    """Substituicoes ja resolvidas entre as camadas, prontas para o resolvedor."""

    def __init__(self, entries: Iterable[Substitution] = ()):
        self.entries: list[Substitution] = list(entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __eq__(self, other) -> bool:
        return isinstance(other, Dictionary) and self.entries == other.entries

    # ------------------------------------------------------------------

    def for_locale(self, locale: str) -> list[Substitution]:
        return [entry for entry in self.entries if entry.applies_to(locale)]

    def by_scope(self, scope: str, locale: str) -> list[Substitution]:
        return [entry for entry in self.for_locale(locale) if entry.scope == scope]

    def token_map(self, locale: str) -> dict[str, str]:
        return {entry.pattern: entry.replacement for entry in self.by_scope(SCOPE_TOKEN, locale)}

    def prefix_map(self, locale: str) -> dict[str, str]:
        return {entry.pattern: entry.replacement for entry in self.by_scope(SCOPE_PREFIX, locale)}

    def free_list(self, locale: str) -> list[tuple[str, str]]:
        return [(entry.pattern, entry.replacement) for entry in self.by_scope(SCOPE_FREE, locale)]

    # ------------------------------------------------------------------

    def with_entry(self, entry: Substitution) -> Dictionary:
        """Uma copia com `entry` no lugar da regra de mesma chave, se houver."""
        kept = [other for other in self.entries if other.key != entry.key]
        return Dictionary([*kept, entry])

    def without(self, key: tuple[str, str]) -> Dictionary:
        return Dictionary([entry for entry in self.entries if entry.key != key])

    def to_list(self) -> list[dict]:
        return [entry.to_dict() for entry in self.entries]

    def as_config(self) -> tuple[tuple[str, str, str, str], ...]:
        """Forma imutavel e ordenada -- e o que atravessa a fronteira de thread."""
        return tuple(sorted((entry.scope, entry.pattern, entry.replacement, entry.locale) for entry in self.entries))

    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Iterable[tuple[str, str, str, str]]) -> Dictionary:
        return cls(
            Substitution(scope=scope, pattern=pattern, replacement=replacement, locale=locale)
            for scope, pattern, replacement, locale in config
        )

    @classmethod
    def from_token_map(cls, mapping: Mapping[str, str], layer: str = LAYER_PROJECT) -> Dictionary:
        """Compatibilidade com o formato antigo `{padrao: substituicao}`."""
        return cls(
            Substitution(pattern=pattern, replacement=replacement, scope=SCOPE_TOKEN, layer=layer)
            for pattern, replacement in mapping.items()
        )

    @classmethod
    def from_payload(cls, payload, layer: str = LAYER_PROJECT) -> Dictionary:
        """Le tanto o formato novo (lista) quanto o antigo (mapa)."""
        if isinstance(payload, Mapping):
            entries = payload.get("entries")
            if entries is None:
                return cls.from_token_map(payload, layer)
            payload = entries

        if not isinstance(payload, list):
            return cls()

        parsed = (Substitution.from_dict(item, layer) for item in payload if isinstance(item, Mapping))
        return cls(entry for entry in parsed if entry is not None)


def merge_layers(*layers: Dictionary) -> Dictionary:
    """Junta as camadas na ordem dada; a **ultima** vence em caso de conflito."""
    resolved: dict[tuple[str, str], Substitution] = {}
    for layer in layers:
        for entry in layer:
            resolved[entry.key] = entry
    return Dictionary(resolved.values())


# Confusoes de OCR conhecidas, embutidas. Todas de prefixo: e o comeco do token
# que o scanner erra, porque e onde esta a letra da peca.
DEFAULT_OCR_PREFIXES: dict[str, str] = {
    "4J": "N",
    "tj": "N",
    "tD": "N",
    "tt": "R",
    "lD": "Q",
    "il": "B",
    "i.": "B",
}


def load_base_dictionary() -> Dictionary:
    """Camada embutida: os prefixos daqui mais os de `data/substitutions.json`."""
    prefixes = dict(DEFAULT_OCR_PREFIXES)

    path = DATA_DIR / "substitutions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}

    if isinstance(payload, dict):
        prefixes.update({str(key): str(value) for key, value in payload.items()})

    return Dictionary(
        replace(
            Substitution(pattern=pattern, replacement=replacement, scope=SCOPE_PREFIX),
            layer=LAYER_BASE,
        )
        for pattern, replacement in prefixes.items()
    )
