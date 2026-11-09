import sys
from pathlib import Path
from typing import Literal

PIECE_CLASSES = [
    "empty",
    "P",
    "N",
    "B",
    "R",
    "Q",
    "K",
    "p",
    "n",
    "b",
    "r",
    "q",
    "k",
]

PIECE_TO_IDX = {name: idx for idx, name in enumerate(PIECE_CLASSES)}
IDX_TO_CLASS = {idx: name for name, idx in PIECE_TO_IDX.items()}

BOARD_SIZE = 800
CELL_SIZE = BOARD_SIZE // 8
MODEL_IMAGE_SIZE = 64

# Abaixo disso a casa entra em `BoardPrediction.uncertain_squares` (S-10). O valor e alto
# de proposito: o modelo e confiante ate quando erra (media ~0,80 nas casas erradas contra
# ~0,999 nas certas), entao um limiar baixo nao separaria nada.
UNCERTAIN_SQUARE_THRESHOLD = 0.90

# Decodificacao sujeita as regras do xadrez (S-11) em vez de argmax por casa.
# O efeito medido esta em docs/BASELINE.md; ligado por padrao porque o argmax nao tem
# nenhuma obrigacao de produzir posicao legal.
CONSTRAINED_DECODING = True

# Teto de casas que o reparo pode reescrever. Alto demais e o decodificador "conserta"
# uma leitura ruim inventando uma posicao legal e errada -- pior que admitir a falha.
MAX_DECODE_CHANGES = 6

# Gate de exportacao (S-15): abaixo desta confianca minima por casa a posicao vai para o
# arquivo de revisao em vez do PGN principal. Medido no split de teste: a confianca minima
# fica >= 0,90 em quase todo tabuleiro exato, e a media nas casas erradas e ~0,75 -- entao
# 0,80 pega a maior parte do erro sem mandar tabuleiro bom para revisao. Provisorio ate a
# calibracao da S-28 dar um numero derivado da curva.
ACCEPT_MIN_CONFIDENCE = 0.80

OrientationMode = Literal["auto", "0", "180"]

# Orientacao decidida por diagrama em vez de um checkbox global (S-13).
DEFAULT_ORIENTATION_MODE: OrientationMode = "auto"

# A partir desta diferenca de min_confidence entre as duas orientacoes, a confianca decide
# sozinha. Medido no split de teste: a margem mediana e 0,925 e o percentil 5 e 0,220 --
# acima de 0,20 a confianca acertou 100% das 320 vezes.
ORIENTATION_DECISIVE_MARGIN = 0.20

# Abaixo da margem decisiva a confianca vira ruido (medido no 1937 Kemeri: duas leituras de
# pe corretas com margem de 0,001 e 0,019) e quem decide e a estrutura da posicao. Diferenca
# minima, em filas, entre o prior de peoes de uma orientacao e o da outra para ele decidir.
# 1,0 fila e conservador: nos casos reais medidos a diferenca foi de 5 a 8 filas.
ORIENTATION_PAWN_PRIOR_MARGIN = 1.0

# TTA (S-29): sete vistas por tabuleiro em vez de uma. Medido no split de teste: 316
# tabuleiros exatos contra 315, ou seja um em 320, por 6x o tempo de inferencia. Desligado.
TTA_ENABLED = False

# Aplicar ou nao a temperatura calibrada (S-28) que o checkpoint carrega.
#
# Desligado por medicao, nao por preguica. O temperature scaling minimiza a NLL de
# validacao e consegue: a NLL cai (0,00403 -> 0,00301 no res32). Mas as duas coisas que o
# produto usa pioram:
#
# | com T calibrado | ECE no teste | rejeitados pelo gate |
# |---|---|---|
# | phase5, T=1,00 | 0,000265 | 6 de 320 |
# | phase5, T=1,85 | 0,000837 | 12 de 320 |
#
# O motivo e estrutural: o modelo acerta 99,98% das casas, e ai confianca 0,9998 ja e
# quase perfeitamente calibrada. Achata-la afasta a confianca da verdade em vez de
# aproximar -- o ECE penaliza confianca abaixo do acerto tanto quanto acima. A NLL melhora
# porque e dominada pelas poucas casas erradas com confianca alta; o ECE, que pondera por
# contagem, vai no sentido oposto. Os dois objetivos discordam, e o que o criterio de
# aceite da S-28 nomeia e o ECE.
#
# O custo pratico de aplicar seria dobrar a fila de revisao sem corrigir uma casa. A
# temperatura continua sendo ajustada e gravada no checkpoint: ela e diagnostico util
# (T=1,85 diz que o modelo e confiante demais no sentido da NLL) e volta a valer se um dia
# a acuracia por casa cair para uma faixa em que haja erro de calibracao para corrigir.
APPLY_CALIBRATED_TEMPERATURE = False

# Teto de diagramas por pagina. Era 8, espalhado por seis arquivos, e cortava diagrama de
# verdade: as paginas de exercicio do "A Matter of Endgame Technique" tem grade 3x3, e o
# nono diagrama -- o de menor score entre nove praticamente empatados -- caia fora sem que
# nada avisasse.
#
# Medido em 6 livros, 15 paginas de cada, com teto 8 contra teto 30: so o Aagaard perde
# algo (55 contra 58 diagramas), e nenhum outro livro ganha um unico candidato a mais.
# Ou seja, subir o teto nao admite lixo -- quem filtra e o piso de score de `detect_boards`,
# nao este numero. 12 cobre grade 3x3 e 3x4 com folga.
DEFAULT_MAX_BOARDS = 12

ReadingOrder = Literal["row", "column"]

# Ordem em que os diagramas de uma pagina sao numerados (S-14). Um unico padrao aqui e o
# que faz o "diagrama 2" da GUI ser o mesmo do header [Diagram "2"] no PGN: antes a GUI
# usava "row" (padrao de detect_boards) e a exportacao passava "column", e numa pagina de
# duas colunas a numeracao divergia -- justamente quando o usuario quer conferir.
# "column" e o correto para a maioria dos livros de xadrez, que sao de duas colunas.
DEFAULT_READING_ORDER: ReadingOrder = "column"


def _project_root() -> Path:
    """Onde ficam `data/`, `models/`, `PDF/` e `PGN/`.

    Num checkout, é a raiz do repositório: `src/chess_diagram_ocr/config.py` → dois níveis
    acima. **Num bundle do PyInstaller isso apontaria para dentro do pacote** (S-55), que é
    somente-leitura na prática e some a cada reinstalação -- e o `labels.csv` é trabalho
    humano acumulado, o último arquivo que se quer dentro de um diretório descartável.

    Congelado, a raiz é a pasta que **contém** o executável. O usuário vê `data/`,
    `models/`, `PDF/` e `PGN/` ao lado do `.exe`, pode fazer backup copiando a pasta, e
    reinstalar não apaga rótulo nenhum.

    `sys.frozen` é posto pelo bootloader do PyInstaller; `sys.executable` é o `.exe` no modo
    `--onedir`, que é o modo que este projeto usa (ver `packaging/cvoff.spec` para por quê).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()

"""Onde ficam os recursos **somente-leitura** que vão dentro do bundle (`assets/`).

Igual a `PROJECT_ROOT` num checkout. Congelado, é a pasta interna do PyInstaller -- e a
distinção importa: imagem de peça é dado do programa, `labels.csv` é dado do usuário, e
misturar os dois é o que faz reinstalar apagar trabalho.
"""

DEFAULT_DATASET_CSV = PROJECT_ROOT / "data" / "labels.csv"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "piece_classifier.pt"

DEFAULT_SPLITS_PATH = PROJECT_ROOT / "data" / "splits.csv"
"""A partição, aqui e em nenhum outro lugar (S-383).

Ela estava declarada **seis vezes** dentro de `cli/`, sob dois nomes -- `DEFAULT_SPLITS_PATH`
em quatro comandos e `DEFAULT_SPLITS` em dois -- e por dois caminhos diferentes:
`PROJECT_ROOT / "data" / "splits.csv"` num grupo e `DEFAULT_DATASET_CSV.parent / "splits.csv"`
no outro. Hoje as duas fórmulas dão o mesmo arquivo, e é justamente aí que mora o defeito:
mudar o `labels.csv` de pasta faria metade dos comandos seguir e a outra metade ficar, e a
diferença apareceria como "o `cvoff-eval` mede outro conjunto que o `cvoff-audit`"."""

"""DPI de renderização de página. Era o literal `220` em doze declarações de `--dpi` (S-383).

O número tem razão medida -- é o DPI em que a detecção da S-08 foi calibrada -- e um literal
repetido doze vezes é um número sem dono: mudá-lo obrigaria a achar as doze."""
