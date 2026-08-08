"""As tres camadas do dicionario de OCR (SPEC 5.2).

    embutida  ->  minha (global)  ->  deste projeto
    (base)        (do usuario)        (mais especifica, vence)

A ordem tem uma razao pratica: o ruido de OCR de um scanner especifico e do
**usuario** e vale para todos os livros dele; a leitura errada de uma fonte
especifica e do **projeto** e nao deve contaminar os outros. A camada embutida
so carrega o que e verdade em qualquer digitalizacao.

Aqui tambem mora o "aprendiz": `register_acceptance` conta quantas vezes a mesma
correcao ja foi aceita a mao, e na terceira o programa oferece grava-la. Tres e
o numero da SPEC, e a razao e boa -- uma vez e acaso, duas e coincidencia, tres
e o scanner fazendo sempre a mesma coisa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.normalizer import LiveTextNormalizer
from ..core.substitutions import (
    LAYER_GLOBAL,
    LAYER_PROJECT,
    SCOPE_TOKEN,
    Dictionary,
    Substitution,
    load_base_dictionary,
    merge_layers,
)
from ..core.tokenizer import PGNTokenizer
from .project_service import app_data_dir

GLOBAL_DICTIONARY_FILE = "dictionary.json"

#: Aceitacoes da mesma correcao antes de o programa oferecer aprende-la.
LEARN_AFTER_ACCEPTANCES = 3


def global_dictionary_path() -> Path:
    return app_data_dir() / GLOBAL_DICTIONARY_FILE


def load_global_dictionary() -> Dictionary:
    path = global_dictionary_path()
    if not path.is_file():
        return Dictionary()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Dictionary()
    return Dictionary.from_payload(payload, LAYER_GLOBAL)


def save_global_dictionary(dictionary: Dictionary) -> None:
    try:
        global_dictionary_path().write_text(
            json.dumps({"entries": dictionary.to_list()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Preferencia de dicionario nao vale travar o programa.
        pass


@dataclass
class SubstitutionUse:
    """O que uma substituicao alcanca no texto que esta aberto agora."""

    entry: Substitution
    tokens: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tokens)


class DictionaryService:
    """Junta as camadas e responde por elas."""

    def __init__(self, locale: str = "pt"):
        self.locale = locale
        self.base = load_base_dictionary()
        self.user = load_global_dictionary()
        self.project = Dictionary()

    # ------------------------------------------------------------------

    def set_locale(self, locale: str) -> None:
        self.locale = locale

    def set_project(self, dictionary: Dictionary) -> None:
        self.project = dictionary

    def merged(self) -> Dictionary:
        """O que o resolvedor enxerga: a camada mais especifica vence."""
        return merge_layers(self.base, self.user, self.project)

    def layer(self, layer: str) -> Dictionary:
        return {"base": self.base, LAYER_GLOBAL: self.user, LAYER_PROJECT: self.project}[layer]

    # ------------------------------------------------------------------

    def learn(
        self,
        pattern: str,
        replacement: str,
        scope: str = SCOPE_TOKEN,
        layer: str = LAYER_PROJECT,
        note: str = "",
    ) -> Substitution:
        """Grava uma substituicao na camada pedida e devolve a entrada criada."""
        entry = Substitution(
            pattern=pattern,
            replacement=replacement,
            scope=scope,
            locale=self.locale,
            layer=layer,
            note=note,
        )

        if layer == LAYER_GLOBAL:
            self.user = self.user.with_entry(entry)
            save_global_dictionary(self.user)
        else:
            self.project = self.project.with_entry(entry)

        return entry

    def forget(self, key: tuple[str, str], layer: str) -> None:
        if layer == LAYER_GLOBAL:
            self.user = self.user.without(key)
            save_global_dictionary(self.user)
        elif layer == LAYER_PROJECT:
            self.project = self.project.without(key)
        # A camada embutida nao se apaga: sobrepoe-se com uma entrada de cima.

    def move_to_global(self, key: tuple[str, str]) -> Substitution | None:
        """Promove uma regra do projeto para valer em todos os livros."""
        entry = next((item for item in self.project if item.key == key), None)
        if entry is None:
            return None

        self.project = self.project.without(key)
        return self.learn(entry.pattern, entry.replacement, entry.scope, LAYER_GLOBAL, entry.note)

    # ------------------------------------------------------------------
    # Aprendizado por repeticao
    # ------------------------------------------------------------------

    @staticmethod
    def acceptance_key(raw_text: str, san: str) -> str:
        return f"{raw_text}→{san}"

    @classmethod
    def register_acceptance(cls, counters: dict[str, int], raw_text: str, san: str) -> int:
        """Conta mais uma aceitacao manual desta correcao e devolve o total."""
        key = cls.acceptance_key(raw_text, san)
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    @staticmethod
    def should_offer_to_learn(times_accepted: int) -> bool:
        return times_accepted == LEARN_AFTER_ACCEPTANCES

    # ------------------------------------------------------------------
    # Teste ao vivo
    # ------------------------------------------------------------------

    def preview(self, text: str, dictionary: Dictionary | None = None) -> dict[tuple[str, str], SubstitutionUse]:
        """Que tokens do texto atual cada substituicao alcanca.

        E a resposta a pergunta que a SPEC chama de "teste ao vivo": antes de
        gravar uma regra, ver **onde ela pega** neste livro. O texto passa pelo
        mesmo tokenizador da analise, entao o que aparece aqui e exatamente o
        que o resolvedor vai ver.
        """
        source = dictionary if dictionary is not None else self.merged()
        uses = {entry.key: SubstitutionUse(entry=entry) for entry in source.for_locale(self.locale)}
        if not uses or not text.strip():
            return uses

        normalized = LiveTextNormalizer(locale=self.locale).normalize_with_mapping(text)
        tokens = [
            token.value for token in PGNTokenizer().tokenize(normalized) if token.type in {"MOVE_CANDIDATE", "SAN_MOVE"}
        ]

        for token in tokens:
            for use in uses.values():
                if use.entry.apply(token) is not None:
                    use.tokens.append(token)

        return uses
