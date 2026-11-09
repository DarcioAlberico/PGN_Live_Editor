"""Leitura do checkpoint do classificador de peças, com os metadados gravados no treino.

O treino mora no projeto de origem (ChessVisionOFF); aqui só se carrega o modelo para
inferência. A escrita do checkpoint e os utilitários de treino que vieram junto na
cópia foram removidos por não terem uso neste programa (R-008 do docs/ROADMAP_V3.md).
Dois formatos são aceitos: `{"model_state": ..., "metadata": ...}` (formato 2) e o
dicionário de pesos cru (formato 1, legado).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


CHECKPOINT_FORMAT = 2
"""1 = `{"model_state": ...}` cru (pré-Fase 5). 2 = com metadados."""


@dataclass(frozen=True)
class Checkpoint:
    """Um checkpoint carregado, com os metadados separados dos pesos."""

    state: dict[str, Any]
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    temperature: float = 1.0
    """Temperatura da calibração (S-28). 1,0 significa não calibrado."""

    @property
    def is_legacy(self) -> bool:
        """Checkpoint sem metadados: gravado antes da Fase 5.

        Continua carregando para inferência de propósito -- `piece_classifier_baseline.pt`
        é a única forma de reproduzir os números do BASELINE.md, e recusá-lo tornaria o
        baseline do projeto inverificável. O que ele **não** pode fazer é ser retomado
        para treino, porque aí a arquitetura precisa ser conhecida.
        """
        return int(self.metadata.get("checkpoint_format", 1)) < CHECKPOINT_FORMAT

    @property
    def arch_version(self) -> str:
        return str(self.metadata.get("arch_version", ""))

    @property
    def class_names(self) -> list[str]:
        names = self.metadata.get("class_names")
        return list(names) if names else []

    @property
    def best_metric(self) -> float | None:
        value = self.metadata.get("best_metric")
        return float(value) if value is not None else None


def _load_raw(path: Path, *, map_location: str) -> Any:
    # `import torch` aqui dentro, e não no topo: este módulo não usa torch em NENHUM escopo
    # de módulo (medido: 0 usos), e o topo dele é o que faz `qt/janela.py` exigir torch para
    # abrir -- pela cadeia qt.campo -> field_eval -> checkpoint. Um import dentro de função
    # custa uma consulta a `sys.modules` por chamada, e `torch.load` sozinho custa cinco
    # ordens de grandeza mais do que isso.
    import torch

    try:
        return torch.load(path, map_location=map_location, weights_only=True)
    except TypeError:
        logger.debug("torch.load sem weights_only nesta versao; usando fallback.")
        return torch.load(path, map_location=map_location)


def load_checkpoint(path: Path, *, map_location: str = "cpu") -> Checkpoint:
    """Le um checkpoint .pt nos dois formatos em uso."""
    path = Path(path)
    raw = _load_raw(path, map_location=map_location)

    if isinstance(raw, dict) and "model_state" in raw:
        state = raw["model_state"]
        metadata = dict(raw.get("metadata") or {})
        stored = raw.get("temperature", metadata.get("temperature"))
        temperature = 1.0 if stored is None else float(stored)
    else:
        state = raw
        metadata = {}
        temperature = 1.0

    if not isinstance(state, dict):
        raise ValueError(f"Checkpoint inválido em {path}: esperado dict de pesos, obtido {type(state).__name__}.")

    if temperature <= 0:
        raise ValueError(f"Temperatura inválida em {path}: {temperature}. Deve ser positiva.")

    return Checkpoint(state=state, path=path, metadata=metadata, temperature=temperature)


def load_state_dict(path: Path, *, map_location: str) -> dict[str, Any]:
    """Compatibilidade: só os pesos. Prefira `load_checkpoint`."""
    return load_checkpoint(Path(path), map_location=map_location).state


FINGERPRINT_DIGITS = 16
"""Quantos dígitos hexadecimais do sha256 entram na impressão digital.

16 dígitos são 64 bits. Colisão acidental entre dois checkpoints do mesmo projeto é
inconcebível nessa largura, e a impressão continua cabendo numa linha de tabela -- que é
onde ela vai ser lida."""


def checkpoint_fingerprint(path: Path, *, digits: int = FINGERPRINT_DIGITS) -> str:
    """Hash curto do **conteúdo** do `.pt`, para dizer qual modelo sem depender do caminho.

    Complemento de `checkpoint_identity`, e a diferença entre as duas é o orçamento de quem
    chama. Aquela responde *"trocaram o arquivo debaixo de mim?"* a cada retomada de
    exportação, e por isso não pode ler 8,7 MB; esta responde *"que modelo produziu este
    número?"* uma vez por relatório, onde ler o arquivo inteiro custa menos que renderizar
    uma página de PDF.

    O caminho não serve para essa pergunta: o treino reescreve sempre
    `models/piece_classifier.pt`, então quatro medições de quatro modelos citam o mesmo
    caminho. O conteúdo distingue; o nome não.

    Lê em blocos porque um checkpoint não cabe confortavelmente na memória de quem já tem o
    modelo carregado, e arquivo ausente ou ilegível devolve `""` -- um relatório não deve
    morrer por não conseguir se identificar.
    """
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
                digest.update(bloco)
    except OSError:
        return ""
    return digest.hexdigest()[:digits]


"""Corte do texto de `unreadable`. Um traceback de `torch` inteiro num JSON versionado é
diff sem informação, e o que decide já está nas primeiras palavras."""
