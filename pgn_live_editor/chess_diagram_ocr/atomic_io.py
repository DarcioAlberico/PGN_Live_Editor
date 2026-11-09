"""Escrita de arquivo que não deixa arquivo pela metade (S-25).

Três arquivos do projeto são reescritos inteiros a cada mudança e lidos na abertura
seguinte: o estado da app (`data/app_tkinter_state.json`), a fila de revisão
(`data/review_queue.json`) e o `labels.csv` quando a UI de dataset regrava um rótulo.
Nos três, `path.write_text(...)` trunca o arquivo antes de escrever o conteúdo novo --
uma interrupção nesse intervalo deixa 0 byte no lugar do que existia.

O reparo é o mesmo padrão nos três: escrever num temporário **no mesmo diretório** e
renomear por cima. `os.replace` é atômico dentro do mesmo volume, no Windows inclusive; o
temporário precisa ser vizinho porque renomear entre volumes não é.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TENTATIVAS_DE_TROCA = 5
"""Quantas vezes insistir no `os.replace` antes de desistir com recado (S-373)."""

ESPERA_ENTRE_TROCAS = 0.08
"""Segundos da primeira espera; as seguintes crescem em progressão (0,08 → 0,4 s)."""


def _substituir(temp_path: Path, path: Path) -> None:
    """`os.replace`, com a segunda chance e a frase que o Windows não dá (S-373).

    No POSIX renomear por cima de um arquivo aberto funciona: quem o tinha aberto continua
    lendo o inode antigo. **No Windows, não.** Um `handle` aberto no destino sem
    `FILE_SHARE_DELETE` faz o `MoveFileEx` falhar com `ERROR_ACCESS_DENIED`, e o Python o
    entrega como `PermissionError: [WinError 5] Acesso negado`, sem dizer qual arquivo o
    prendia nem que essa é a causa. Acontece com o `labels.csv` aberto no Excel -- que é
    exatamente o programa em que alguém abriria um CSV --, com o `.json` de estado aberto no
    editor, e com o antivírus, que segura o arquivo recém-criado por alguns milissegundos
    para varrer.

    Os dois casos pedem respostas diferentes e esta função dá as duas: o antivírus solta
    sozinho, então **insistir** resolve; o Excel não solta, então o que resta é **dizer** o
    que aconteceu. O que não pode continuar é a mensagem crua, que mandava o usuário procurar
    permissão de pasta num problema que é de arquivo aberto.

    O arquivo antigo fica intacto nas duas saídas: `os.replace` ou troca tudo, ou não troca
    nada.
    """
    ultima: OSError | None = None
    for tentativa in range(TENTATIVAS_DE_TROCA):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            ultima = exc
            if tentativa + 1 < TENTATIVAS_DE_TROCA:
                time.sleep(ESPERA_ENTRE_TROCAS * (tentativa + 1))

    espera = ESPERA_ENTRE_TROCAS * sum(range(1, TENTATIVAS_DE_TROCA))
    raise PermissionError(
        f"Não foi possível gravar {path}: o sistema recusou substituir o arquivo por "
        f"{espera:.1f} s seguidos. No Windows isso quer dizer que ele está aberto em outro "
        f"programa -- o Excel e o Bloco de Notas seguram o arquivo enquanto a janela estiver "
        f"aberta. Feche-o e repita a operação; o arquivo anterior continua intacto e nada foi "
        f"gravado pela metade. Detalhe do sistema: {ultima}"
    ) from ultima


def atomic_write_text(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
    """Grava `payload` em `path` sem passar por um estado de arquivo truncado."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        newline="\n",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            # Sem o fsync, o rename pode chegar ao disco antes do conteudo: o arquivo
            # existe, tem o nome certo e esta vazio -- exatamente o que se quis evitar.
            os.fsync(handle.fileno())
        _substituir(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent) + "\n")


def write_image(path: Path, image_bgr: Any) -> Path:
    """Grava a imagem e **levanta** quando o OpenCV recusa. Devolve o caminho (S-111).

    `cv2.imwrite` não levanta: devolve `False`. Disco cheio, pasta em rede fora do ar,
    antivírus segurando o arquivo -- em todos, quem chamava seguia adiante e gravava a linha
    no `labels.csv` apontando para um PNG que não existe. O prejuízo é o trabalho humano
    daquela correção, e ele aparece semanas depois no relatório da auditoria, na linha
    "rótulos cujo PNG sumiu -- descartados em silêncio no treino".

    A ordem de quem chama já é favorável -- a imagem vem antes do CSV --, então levantar aqui
    deixa o CSV intacto e o erro chega a quem está na frente da tela.

    **O OpenCV não abre o arquivo, nós abrimos.** `cv2.imwrite` recebe o caminho como
    `std::string` e o entrega ao `fopen` estreito do CRT, que o converte pela code page ANSI
    do processo -- cp1252 nesta máquina. Um livro russo em
    `review_cache/_Болеславский_И_Избранные_партии/` falhava aí: o `mkdir` do Python usa a API
    wide e criava a pasta, o `imwrite` devolvia `False` e a mensagem acima mandava conferir
    espaço em disco e permissão, que estavam ambos em ordem. Codificar em memória e escrever
    os bytes pelo Python tira o CRT do caminho -- `net_correction.py` já fazia assim.

    Não é escrita atômica, e é de propósito: o nome do PNG é único por timestamp
    (`dataset.py`) ou determinístico e reescrito por inteiro (`review_queue.py`), então não há
    versão anterior a proteger. O que faltava era conferir a resposta.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import cv2  # local: o `atomic_io` é importado por módulos que não dependem de OpenCV

    try:
        ok, buffer = cv2.imencode(path.suffix, image_bgr)
    except cv2.error as exc:  # extensão que nenhum codec reconhece
        raise OSError(f"O OpenCV não sabe codificar '{path.suffix}' para gravar {path}.") from exc
    if not ok:
        raise OSError(f"O OpenCV não conseguiu codificar a imagem de {path}.")

    try:
        path.write_bytes(buffer.tobytes())
    except OSError as exc:
        raise OSError(
            f"Não foi possível gravar {path}: {exc}. Verifique espaço em disco e permissão de escrita."
        ) from exc
    return path


def read_image(path: Path | str) -> Any | None:
    """Lê uma imagem em BGR. `None` quando ela não existe ou não decodifica.

    Contrapartida de `write_image`, e pelo mesmo motivo: `cv2.imread` sofre da mesma conversão
    ANSI do caminho, então um PNG sob nome cirílico voltava `None` mesmo com `path.exists()`
    dizendo `True` -- "a imagem não existe mais" numa imagem que estava lá. Ler os bytes pelo
    Python e decodificar em memória resolve as duas pontas.

    Devolve `None` em vez de levantar para que os sete pontos de chamada mantenham o
    tratamento que já tinham para o `None` do `imread`.
    """
    import cv2
    import numpy as np

    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def read_board_image(path_text: str | Path) -> Any | None:
    """Lê a imagem de um tabuleiro do disco **em RGB**. `None` se ela não existe mais.

    Mora aqui e não no painel porque não é decisão de tela nenhuma: é `read_image` mais a troca de
    BGR para RGB, que é a ordem em que o resto do programa carrega tabuleiro. Os dois frontends
    abrem a mesma miniatura da fila de revisão e a mesma amostra do dataset, e um segundo leitor
    seria o lugar onde um deles inverteria os canais sem que nada avisasse -- peça branca virando
    preta é um defeito que a suíte não vê e o olho vê na hora.

    `exists()` antes de ler porque a chamada é a resposta a "esta linha do dataset ainda tem
    imagem?", e o `None` de arquivo ausente é o caso esperado, não o excepcional.
    """
    import cv2

    caminho = Path(path_text)
    if not caminho.exists():
        return None
    imagem_bgr = read_image(caminho)
    if imagem_bgr is None:
        return None
    return cv2.cvtColor(imagem_bgr, cv2.COLOR_BGR2RGB)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Versão binária, para o `labels.csv` que o pandas escreve via buffer."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _substituir(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
