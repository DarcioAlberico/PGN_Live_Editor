"""Resolucao de tokens ambiguos **com o tabuleiro na mao**.

Esta e a inversao central da Fase 2. Antes, o normalizador trocava `C`->`N`,
`ch`->`+` e `mate`->`#` no texto inteiro, as cegas, antes de qualquer analise --
e por isso destruia prosa (`chances` -> `+ances`, `Cada` -> `Nada`).

Aqui as transformacoes ambiguas so sao tentadas sobre um token que ja foi
classificado como candidato a lance, e uma hipotese so e aceita se resultar num
lance **legal na posicao corrente**. Cada hipotese carrega a confianca e a
explicacao que o painel de alertas mostra ao usuario.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import chess
import Levenshtein

from .issues import Candidate

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Letras de peca por idioma de notacao.
#
# `R` -> `K` e o caso delicado: em portugues, espanhol, frances e italiano o Rei
# e `R`, mas `R` tambem e a Torre em ingles. O conflito nao precisa de regra
# especial -- a hipotese `exato` vale 1.0 e so cede quando nao da lance legal.
# Numa posicao em que a torre pode ir para h1, `Rh1` continua sendo a torre;
# quando ela nao pode, o Rei entra. Sem esta entrada, um livro em portugues
# perdia a partida inteira no primeiro lance de rei.
LOCALE_PIECE_MAPS: dict[str, dict[str, str]] = {
    "en": {},
    "pt": {"R": "K", "D": "Q", "T": "R", "B": "B", "C": "N"},
    "es": {"R": "K", "D": "Q", "T": "R", "A": "B", "C": "N"},
    "de": {"D": "Q", "T": "R", "L": "B", "S": "N"},
    "fr": {"R": "K", "D": "Q", "T": "R", "F": "B", "C": "N"},
    "it": {"R": "K", "D": "Q", "T": "R", "A": "B", "C": "N"},
}

ENGLISH_PIECE_LETTERS = frozenset("KQRBN")

# Confusoes de OCR conhecidas, aplicadas so no inicio de um token de lance.
DEFAULT_OCR_PREFIXES: dict[str, str] = {
    "4J": "N",
    "tj": "N",
    "tD": "N",
    "tt": "R",
    "lD": "Q",
    "il": "B",
    "i.": "B",
}

# Um token so e considerado candidato a lance se tiver cara de notacao:
# ao menos uma letra, ao menos um digito de casa, e nenhum caractere estranho.
#
# Os sinais de avaliacao entram no conjunto porque o livro imprime `Bg5!` e
# `Cd5?` colados. Um `Bg5!` em ingles ja se salvava (o SAN casa e o `!` vira NAG
# a parte), mas `Tg5!` em portugues morria aqui, antes de chegar ao resolvedor
# que sabe tirar o sufixo -- e o lance sumia sem alerta nenhum.
MOVE_CANDIDATE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-+#=!?∞±∓⩱⩲□]{1,7}$")
ANNOTATION_SUFFIX_RE = re.compile(r"(?:[!?]{1,2}|[∞±∓⩱⩲□])+$")

# Limiares de aplicacao (SPEC 2.3).
CONFIDENCE_AUTO_APPLY = 0.90
CONFIDENCE_PROVISIONAL = 0.60


def looks_like_move(token: str) -> bool:
    """Filtro barato que impede prosa de virar candidato a lance.

    `Cada`, `chances` e `Chega` nao passam (sem digito de casa); `Cf3`, `tDc6`
    e `Dh5ch` passam.
    """
    if not MOVE_CANDIDATE_RE.match(token):
        return False
    if not any(char.isalpha() for char in token):
        # `1945`, `26`, `1/2`: numero puro nunca e lance.
        return False
    if not any(char.isdigit() for char in token):
        # Sem digito so sobra o roque; `Cada` e `chances` param aqui.
        return token.upper().replace("0", "O") in {"O-O", "O-O-O"}
    # Digitos fora de 1-8 continuam valendo: o OCR erra a casa tanto quanto a
    # letra da peca, e perder um lance em silencio e pior que um alerta a mais.
    return True


def _load_data_file(name: str) -> dict[str, str]:
    path = DATA_DIR / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


class CandidateResolver:
    """Propoe lances legais para um token que nao era SAN valido."""

    def __init__(self, locale: str = "pt", dictionary: dict[str, str] | None = None):
        self.locale = locale
        self.ocr_prefixes = dict(DEFAULT_OCR_PREFIXES)
        self.ocr_prefixes.update(_load_data_file("substitutions.json"))
        self.dictionary: dict[str, str] = dict(dictionary or {})
        # O documento provou estar escrito na notacao do idioma? Ver
        # `notation_is_confirmed` e `_expand`.
        self.notation_confirmed = False

    def set_locale(self, locale: str) -> None:
        self.locale = locale

    def set_notation_confirmed(self, confirmed: bool) -> None:
        self.notation_confirmed = confirmed

    def proof_letters(self) -> set[str]:
        """Letras que so existem na notacao deste idioma.

        `C`, `D` e `T` nao sao letras de peca do SAN ingles: um `Cf3` legal no
        documento prova que ele esta em portugues. `B` nao prova nada (e Bispo
        nos dois) e `R` menos ainda -- e justamente a letra em disputa.
        """
        return {letter for letter in self._piece_map() if letter not in ENGLISH_PIECE_LETTERS}

    def collides_with_english(self, token: str) -> bool:
        """`Rc7` quer dizer Rei em portugues e Torre em ingles."""
        letter = token[:1]
        mapping = self._piece_map()
        return letter in ENGLISH_PIECE_LETTERS and mapping.get(letter, letter) != letter

    def learn(self, raw_token: str, san: str) -> None:
        """Grava uma correcao confirmada pelo usuario."""
        self.dictionary[raw_token] = san

    # ------------------------------------------------------------------
    # Geracao de hipoteses
    # ------------------------------------------------------------------

    def _piece_map(self) -> dict[str, str]:
        return LOCALE_PIECE_MAPS.get(self.locale, {})

    def _expand(self, token: str) -> list[tuple[str, float, str, str]]:
        """(texto, confianca, fonte, explicacao) para cada hipotese textual."""
        seen: set[str] = set()
        states: list[tuple[str, float, str, str]] = []

        def push(text: str, confidence: float, source: str, rationale: str):
            if text and text not in seen:
                seen.add(text)
                states.append((text, confidence, source, rationale))

        # A semente vale 1.0 sempre: as etapas seguintes multiplicam por ela, e
        # rebaixa-la aqui rebaixaria junto toda hipotese derivada. O desconto da
        # leitura inglesa e aplicado no fim, em `resolve`, so a ela mesma.
        push(token, 1.0, "exato", token)

        # 1. Dicionario do usuario: casamento exato do token inteiro.
        if token in self.dictionary:
            push(self.dictionary[token], 0.99, "dicionario", f"{token} -> {self.dictionary[token]} (dicionario)")

        # As etapas seguintes sao cumulativas: cada uma trabalha sobre o que a
        # anterior produziu, e a confianca e o produto dos fatores aplicados.
        for stage in (self._strip_annotation, self._strip_novelty, self._suffix_words, self._ocr_prefix, self._fix_case, self._locale_piece):
            for text, confidence, source, rationale in list(states):
                result = stage(text)
                if result is None:
                    continue
                new_text, factor, new_source, note = result
                if new_text == text:
                    continue
                # Fonte e explicacao sao acumuladas para que o painel mostre a
                # cadeia inteira (ex.: `Dh5ch` -> sufixo -> `Dh5+` -> idioma -> `Qh5+`).
                combined_source = new_source if source == "exato" else f"{source}+{new_source}"
                combined_note = note if source == "exato" else f"{rationale}; {note}"
                push(new_text, confidence * factor, combined_source, combined_note)

        return states

    def _strip_annotation(self, token: str):
        stripped = ANNOTATION_SUFFIX_RE.sub("", token)
        if stripped == token or not stripped:
            return None
        return (stripped, 1.0, "anotacao", f"sem o sinal de avaliacao: {token} -> {stripped}")

    def _strip_novelty(self, token: str):
        # `Nf3N` = novidade teorica. Nao confundir com promocao `e8=N`.
        if len(token) > 2 and token.endswith("N") and not token.endswith("=N"):
            return (token[:-1], 0.98, "novidade", f"marca de novidade: {token} -> {token[:-1]}")
        return None

    def _suffix_words(self, token: str):
        """`ch` = cheque e `mate` = mate em livros antigos, **so como sufixo**."""
        lowered = token.lower()
        for suffix, replacement in (("mate", "#"), ("mat", "#"), ("ch", "+")):
            if lowered.endswith(suffix) and len(token) > len(suffix) + 1:
                converted = token[: -len(suffix)] + replacement
                return (converted, 0.97, "sufixo", f"{token} -> {converted}")
        return None

    def _ocr_prefix(self, token: str):
        for bad, good in sorted(self.ocr_prefixes.items(), key=lambda item: -len(item[0])):
            if token.startswith(bad) and len(token) > len(bad):
                converted = good + token[len(bad) :]
                return (converted, 0.92, "ocr", f"ruido de OCR: {token} -> {converted}")
        return None

    def _fix_case(self, token: str):
        if token[:1].islower() and token[:1] in "kqrbnptcdlsaf":
            converted = token[0].upper() + token[1:]
            return (converted, 0.96, "caixa", f"letra de peca minuscula: {token} -> {converted}")
        return None

    def _locale_piece(self, token: str):
        mapping = self._piece_map()
        letter = token[:1]
        if letter in mapping and mapping[letter] != letter and len(token) > 1:
            converted = mapping[letter] + token[1:]
            return (converted, 0.95, "idioma", f"notacao {self.locale}: {token} -> {converted}")
        return None

    # ------------------------------------------------------------------
    # Levenshtein com limiar proporcional
    # ------------------------------------------------------------------

    def _levenshtein_candidates(self, token: str, board: chess.Board) -> list[Candidate]:
        legal_sans = [board.san(move) for move in board.legal_moves]
        if not legal_sans:
            return []

        core = ANNOTATION_SUFFIX_RE.sub("", token) or token
        # Um token curto tem pouca informacao: distancia 2 em `Kd5` "conserta"
        # para praticamente qualquer coisa. Por isso o limiar acompanha o tamanho.
        max_distance = 1 if len(core) <= 3 else 2

        scored = sorted((Levenshtein.distance(core, san), san) for san in legal_sans)
        best_distance, best_san = scored[0]
        if best_distance == 0 or best_distance > max_distance:
            return []

        confidence = 0.75 if best_distance == 1 else 0.55

        tied = len(scored) > 1 and scored[1][0] == best_distance
        if tied:
            confidence = min(confidence, 0.40)

        if _piece_letter(core) != _piece_letter(best_san):
            confidence = min(confidence, 0.60)
        elif _destination(core) != _destination(best_san):
            confidence = min(confidence, 0.70)

        rationale = f"lance legal mais proximo ({best_distance} caractere(s) de diferenca)"
        if tied:
            others = ", ".join(san for distance, san in scored[1:3] if distance == best_distance)
            rationale += f"; empatado com {others}"

        return [Candidate(san=best_san, confidence=confidence, source="levenshtein", rationale=rationale)]

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def _exact_reading_confidence(self, token: str) -> tuple[float, str]:
        """Quanto vale ler o token como esta, sem traduzir nada.

        Normalmente 1.0 -- e a leitura do proprio PGN. Ela so cede quando **o
        documento provou** estar noutra notacao e a letra muda de sentido entre
        os dois idiomas: num final de torre em portugues, `Rc7` costuma ser
        legal como Torre **e** como Rei, e escolher a torre destroi a partida
        inteira dali para frente. Sem essa prova -- um PGN ingles importado, por
        exemplo -- nada muda e `Rc7` continua sendo a torre.
        """
        if self.notation_confirmed and self.collides_with_english(token):
            return (0.80, f"leitura inglesa de '{token}'; o documento usa notacao {self.locale}")
        return (1.0, "lance legal")

    def resolve(self, token: str, board: chess.Board) -> list[Candidate]:
        """Hipoteses legais para `token`, da mais confiavel para a menos."""
        candidates: list[Candidate] = []
        seen_sans: set[str] = set()
        exact_confidence, exact_rationale = self._exact_reading_confidence(token)

        for text, confidence, source, rationale in self._expand(token):
            if source == "exato":
                confidence, rationale = exact_confidence, exact_rationale

            try:
                move = board.parse_san(text)
            except (ValueError, AssertionError):
                continue

            san = board.san(move)
            if san in seen_sans:
                continue
            seen_sans.add(san)
            candidates.append(
                Candidate(
                    san=san,
                    confidence=round(confidence, 3),
                    source=source,
                    rationale=rationale,
                )
            )

        if not candidates:
            candidates = [
                candidate
                for candidate in self._levenshtein_candidates(token, board)
                if candidate.san not in seen_sans
            ]

        candidates.sort(key=lambda candidate: -candidate.confidence)
        return candidates


def _piece_letter(san: str) -> str:
    """Letra da peca de um SAN ingles; vazio para lance de peao."""
    if san[:1] in "KQRBN":
        return san[0]
    if san.startswith("O-O"):
        return "K"
    return ""


def _destination(san: str) -> str:
    match = re.search(r"([a-h][1-8])(?:=[QRBN])?[+#]?$", san)
    return match.group(1) if match else ""
