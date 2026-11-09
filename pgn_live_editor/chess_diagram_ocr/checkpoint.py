"""Leitura e escrita do checkpoint, com os metadados que a S-27 pede.

O checkpoint antigo era `{"model_state": ...}` e mais nada. Três consequências que
apareceram na prática, não em revisão de código:

1. **Retomar zerava o controle de melhor época.** `best_val_loss` recomeçava em infinito
   e a primeira época da retomada sobrescrevia o arquivo mesmo se fosse pior. Foi o que
   aconteceu ao treinar o baseline (ver BASELINE.md); a Fase 1 registrou a pendência.
2. **Trocar a arquitetura descartava pesos em silêncio.** `strict=False` no
   `load_state_dict` faz uma CNN de entrada 48×48 aceitar os pesos de uma de 64×64,
   ignorando as camadas que não batem -- e treinar a partir de meio modelo aleatório.
3. **Não dava para saber que dados produziram um checkpoint.** Qual semente, qual split,
   quantas amostras: nada disso estava gravado, então "reproduza este número" não era uma
   instrução executável.
"""

from __future__ import annotations

import hashlib
import io
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_bytes
from .config import PIECE_CLASSES, PROJECT_ROOT

logger = logging.getLogger(__name__)


def __getattr__(name: str) -> Any:
    """`checkpoint.torch` continua resolvendo -- e só importa torch quando alguém o pede.

    O `import torch` saiu do topo deste módulo para que `qt/janela.py` abra sem torch (o
    instalador leve não o leva dentro; ver
    `packaging/pendencias/0001-torch-fora-do-escopo-de-modulo.patch`). Tirá-lo do topo, porém,
    tira o **nome** `checkpoint.torch`, e é por esse nome que a guarda da S-57 alcança o
    `torch.save` para simular um disco cheio:

        patch("chess_diagram_ocr.checkpoint.torch.save", side_effect=OSError("disco cheio"))

    Esta é a forma que o PEP 562 dá para "atributo de módulo preguiçoso", e ela responde a
    pergunta sem desfazer o conserto: o nome existe, o import acontece **na hora do acesso**, e
    quem acessa recebe o mesmo objeto de `sys.modules["torch"]` que o `import torch` de dentro
    de `_load_raw` e de `save_checkpoint` receberá depois. Por isso o remendo do teste é visto
    lá dentro, e por isso a guarda continua cobrando o que cobrava.

    **Não é um atalho para reintroduzir o torch no escopo de módulo**: importar este módulo não
    dispara nada: só tocar em `checkpoint.torch` dispara.
    """
    if name == "torch":
        import torch

        return torch
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


def save_checkpoint(
    path: Path,
    state: dict[str, Any],
    *,
    metadata: dict[str, Any],
    temperature: float = 1.0,
) -> None:
    """Grava o checkpoint sem passar por um estado de arquivo pela metade (S-57).

    `torch.save` direto no destino trunca o arquivo antes de escrever os 8,7 MB. O
    `atomic_io` existe exatamente para isso desde a S-25, e o docstring dele lista os três
    arquivos que protegia -- estado da app, fila de revisão e `labels.csv`. O checkpoint não
    estava na lista, e é o pior dos quatro para deixar pela metade: é o maior, o mais demorado
    de escrever, e o único cuja escrita acontece numa thread de fundo enquanto outra pode
    estar lendo o mesmo caminho (a exportação de um livro leva dezenas de minutos, e o treino
    regrava uma vez por época que melhora).
    """
    import torch

    payload = {
        "model_state": state,
        "metadata": {**metadata, "checkpoint_format": CHECKPOINT_FORMAT},
        "temperature": float(temperature),
    }
    buffer = io.BytesIO()
    torch.save(payload, buffer)
    atomic_write_bytes(Path(path), buffer.getvalue())


def checkpoint_identity(path: Path) -> str:
    """Identidade barata do arquivo de checkpoint: `<tamanho>-<mtime_ns>` (S-57).

    Serve para dizer se o `.pt` de agora é o mesmo de antes sem lê-lo. `export_checkpoint`
    guardava só o **caminho** para decidir se um parcial podia ser retomado, e o treino
    reescreve sempre `models/piece_classifier.pt`: exportar metade de um livro, cancelar,
    treinar e retomar produzia um PGN com metade das posições lidas por um modelo e metade
    por outro, sem aviso.

    Tamanho e mtime em vez de hash do conteúdo porque a chamada acontece a cada retomada e
    ler 8,7 MB para comparar seria caro para o que se quer saber -- que é "trocaram o arquivo
    debaixo de mim?", e não "estes dois arquivos são idênticos". Arquivo ausente devolve
    `""`, que nunca casa com nada.
    """
    path = Path(path)
    try:
        info = path.stat()
    except OSError:
        return ""
    return f"{info.st_size}-{info.st_mtime_ns}"


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


@dataclass(frozen=True)
class CheckpointDescription:
    """Quem é o `.pt` que produziu um número, em campos que cabem num relatório (S-324).

    Existe porque `docs/metrics/` acumulou relatórios que só se distinguem pelo **nome do
    arquivo**: em 2026-08-22 quatro modelos foram medidos sobre as mesmas 66 páginas e os
    quatro JSON saíram idênticos na parte que diz de quem eles são -- que era nenhuma. A
    tabela comparativa dependia de quem gravou ter lembrado o que rodou.

    Os três níveis de resposta, do que decide ao que localiza, e todos os três saem:

    - `sha256` **decide**: dois relatórios com a mesma impressão mediram o mesmo modelo, e o
      caminho pode mentir sobre isso;
    - `best_metric`/`best_epoch` **explicam**: dizem de que treino o arquivo saiu, que é o
      que um humano lendo a tabela quer saber (a Fase 5 já os grava dentro do `.pt`);
    - `path` **localiza**: é por onde se acha o arquivo de novo, quando ele ainda existe.
    """

    path: str
    """Relativo à raiz do projeto quando está dentro dela -- ver `_report_path`."""

    sha256: str = ""
    size_bytes: int = 0
    arch_version: str = ""
    best_metric: float | None = None
    best_metric_name: str = ""
    best_epoch: int | None = None

    train_commit: str = ""
    """`metadata["git_commit"]`: o commit de que saiu o **treino**.

    Renomeado aqui de propósito. Dentro de um relatório de campo, um campo chamado
    `git_commit` seria lido como o commit que fez a medição, que é outra coisa e outro dia."""

    temperature: float = 1.0

    unreadable: str = ""
    """Por que os metadados não puderam ser lidos. Vazio -- e ausente do JSON -- quando deu certo."""

    def as_dict(self) -> dict[str, Any]:
        """As chaves saem sempre, inclusive nulas: o uso é comparar quatro arquivos campo a
        campo, e uma chave que some num deles vira ruído na comparação. A exceção é
        `unreadable`, que só aparece quando há o que dizer."""
        dados: dict[str, Any] = {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "arch_version": self.arch_version,
            "best_metric": round(self.best_metric, 6) if self.best_metric is not None else None,
            "best_metric_name": self.best_metric_name,
            "best_epoch": self.best_epoch,
            "train_commit": self.train_commit,
            "temperature": round(self.temperature, 4),
        }
        if self.unreadable:
            dados["unreadable"] = self.unreadable
        return dados


MOTIVO_MAX = 120
"""Corte do texto de `unreadable`. Um traceback de `torch` inteiro num JSON versionado é
diff sem informação, e o que decide já está nas primeiras palavras."""


def _motivo(exc: BaseException) -> str:
    """Por que o arquivo não foi lido, em uma linha curta e igual em qualquer máquina.

    `FileNotFoundError` tem tratamento próprio porque é o caso comum e porque a mensagem do
    sistema vem traduzida pelo locale e carrega o caminho absoluto -- dois motivos para o
    mesmo relatório sair diferente em duas máquinas, num campo que existe justamente para
    comparar relatórios.
    """
    if isinstance(exc, FileNotFoundError):
        return "arquivo não encontrado"
    texto = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return texto if len(texto) <= MOTIVO_MAX else texto[: MOTIVO_MAX - 1] + "…"


def _report_path(path: Path) -> str:
    """O caminho como ele deve aparecer num relatório versionado.

    Relativo à raiz quando o arquivo está dentro dela. Estes JSON moram em `docs/metrics/` e
    vão para o repositório: um caminho absoluto os torna incomparáveis entre máquinas e
    ainda publica a pasta de quem mediu.
    """
    path = Path(path)
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def describe_checkpoint(path: Path) -> CheckpointDescription:
    """Lê o `.pt` uma vez e devolve quem ele é. **Nunca levanta** (S-324).

    Um relatório que morre porque o modelo sumiu troca um número incompleto por número
    nenhum -- e a informação que mais interessa nesse caso, o caminho que foi pedido, é
    justamente a que ainda existe. Toda falha vira `unreadable`, com o motivo escrito.

    O custo é uma leitura do arquivo mais um `torch.load`, **uma vez por relatório**. Ao lado
    dos minutos de inferência que a medição de campo gasta, é ruído; ao lado da
    `checkpoint_identity`, que roda a cada retomada de exportação, seria caro -- e é por isso
    que são duas funções e não uma.
    """
    path = Path(path)
    try:
        tamanho = path.stat().st_size
    except OSError as exc:
        return CheckpointDescription(path=_report_path(path), unreadable=_motivo(exc))

    impressao = checkpoint_fingerprint(path)

    try:
        # A leitura dos metadados entra no mesmo `try` da carga, e nao depois dela: um
        # `best_epoch` que nao e numero derrubaria a funcao que promete nunca levantar, e um
        # relatorio nao deve morrer por causa de um campo estranho dentro do `.pt`.
        checkpoint = load_checkpoint(path)
        epoca = checkpoint.metadata.get("best_epoch")
        return CheckpointDescription(
            path=_report_path(path),
            sha256=impressao,
            size_bytes=tamanho,
            arch_version=checkpoint.arch_version,
            best_metric=checkpoint.best_metric,
            best_metric_name=str(checkpoint.metadata.get("best_metric_name", "")),
            best_epoch=int(epoca) if epoca is not None else None,
            train_commit=str(checkpoint.metadata.get("git_commit", "")),
            temperature=checkpoint.temperature,
        )
    except Exception as exc:  # noqa: BLE001 - um .onnx, um .pt truncado, um torch mais novo
        # A impressão e o tamanho já identificam o arquivo; o que se perde é o que está
        # **dentro** dele. Metade da identidade vale mais que nenhuma, e o motivo fica escrito.
        logger.debug("Metadados de %s ilegíveis (%s); a identidade sai só com a impressão.", path, exc)
        return CheckpointDescription(
            path=_report_path(path),
            sha256=impressao,
            size_bytes=tamanho,
            unreadable=_motivo(exc),
        )


def check_compatible(checkpoint: Checkpoint, arch_version: str, *, class_names: list[str] | None = None) -> None:
    """Levanta se os metadados do checkpoint contradizem a arquitetura pedida.

    Isto é uma **mensagem melhor**, não a garantia. Quem garante é o
    `load_state_dict(strict=True)` que vem em seguida: nomes e formatos de todos os
    tensores têm de bater, e no espaço de `ArchConfig` cada fator muda algum deles --
    `channels` muda a primeira convolução, `image_size` muda a entrada da cabeça linear
    (2048 / 4608 / 8192), `head` e `backbone` mudam as próprias chaves. O que esta função
    acrescenta é dizer "este checkpoint é de `cnn-gray-64-linear`, você pediu
    `cnn-gray-32-linear`" em vez de despejar uma lista de tensores incompatíveis.

    **Checkpoint sem metadados não é recusado.** A primeira versão disto recusava, com o
    argumento de que retomar poderia continuar a partir de metade de um modelo aleatório.
    O argumento estava errado: era exatamente isso que o `strict=False` de antes permitia,
    e é exatamente isso que o `strict=True` impede. Recusar só bloqueava quem tem um
    checkpoint anterior à Fase 5 -- que é todo mundo que usava o projeto -- sem comprar
    segurança nenhuma. O que de fato não dá para saber de um checkpoint antigo é qual
    métrica ele atingiu; ver `training._resolve_best_metric`, que mede em vez de supor.
    """
    expected_classes = class_names if class_names is not None else PIECE_CLASSES

    if checkpoint.arch_version and checkpoint.arch_version != arch_version:
        raise ValueError(
            f"{checkpoint.path.name} é da arquitetura '{checkpoint.arch_version}', "
            f"mas o treino pediu '{arch_version}'. Treine do zero (--fresh, ou a caixa "
            f"'Treinar do zero' na interface) ou aponte outro checkpoint."
        )

    if checkpoint.class_names and checkpoint.class_names != expected_classes:
        raise ValueError(
            f"{checkpoint.path.name} foi treinado com outras classes "
            f"({len(checkpoint.class_names)}: {', '.join(checkpoint.class_names[:5])}...). "
            f"Esperadas {len(expected_classes)}."
        )


def _git(*args: str) -> str | None:
    """Roda um `git` na árvore do projeto. `None` quando não deu -- e não dar é normal.

    Um `.exe` congelado não tem `.git`, e um clone de tarball também não. Quem chama trata
    a ausência como "não sei", que é a resposta honesta.
    """
    try:
        resultado = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return resultado.stdout if resultado.returncode == 0 else None


def git_commit() -> str:
    """Commit atual, para o checkpoint dizer de que código ele saiu. Vazio se não der."""
    saida = _git("rev-parse", "--short", "HEAD")
    return saida.strip() if saida is not None else ""


def git_worktree_dirty() -> bool:
    """Se a árvore que está rodando tem mudança não commitada (S-324).

    Anda junto com `git_commit`, e sozinho ele mente. Um relatório que grava `db7abfd` numa
    árvore com 250 linhas de detecção ainda não commitadas aponta para um código que **não**
    é o que rodou -- e aponta com a mesma cara de confiança de um que aponta certo.

    O caso não é hipotético: em 2026-08-22 quatro relatórios de campo em `docs/metrics/`
    foram medidos com uma versão da detecção e um commit posterior mudou o recall de uma das
    páginas de 0,800 para 1,000, sem que nada nos arquivos mudasse.

    `false` quando não há `git` para perguntar -- ver `_git`. É a mesma escolha do
    `git_commit`, que devolve `""`: aqui não se sabe, e não se sabe é o que o commit vazio
    ao lado já diz.
    """
    saida = _git("status", "--porcelain")
    return bool(saida is not None and saida.strip())
