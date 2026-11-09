"""OCR de **texto** para paginas escaneadas, com o OCR embutido no Windows.

Metade dos livros antigos de xadrez circula como PDF de imagem: nao ha camada
de texto, e o painel de PDF ficava vazio -- o livro inteiro teria de ser
digitado. O Windows 10/11 traz um OCR proprio (`Windows.Media.Ocr`) que le bem
a notacao (`C3AR`, `P4AD`, `D×P`) e nao pede instalacao nenhuma alem dos
pacotes `winrt-*` do extra `text-ocr`.

O modulo e opcional do mesmo jeito que o motor de xadrez: sem os pacotes, ou
fora do Windows, `text_ocr_available()` responde `False` e o botao nao aparece.

Uma peculiaridade decide o desenho: o OCR do Windows **descarta** palavras que
nao parecem palavra -- `PxP` e `AxP` somem no meio da frase, `CxP` fica -- e o
que some muda com a resolucao. Por isso, depois da leitura da pagina, cada
espaco largo demais entre duas palavras (e o fim de linha antes da margem da
coluna) e recortado e relido sozinho, em mais de uma resolucao: fora da frase,
`PxP` e lido. Medido nos livros de teste, isso rende o mesmo que ler a pagina
inteira varias vezes, por uma fracao do tempo.

A saida passa por `tidy_ocr_notation`, que so conserta o que o OCR estraga na
**notacao** e nunca toca em prosa; o resto (hifenizacao, lances ilegiveis) e
trabalho da limpeza de colagem e do resolvedor, que ja sabem faze-lo.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Resolucoes em que a pagina e lida. A primeira da a ordem das linhas; as
# outras so completam palavras que ela perdeu.
OCR_DPIS = (200, 300, 250)
OCR_DPI = OCR_DPIS[0]
FULL_PAGE_PASSES = 1


class TextOcrUnavailable(RuntimeError):
    """Nao ha OCR de texto utilizavel nesta maquina."""


def _engine():
    """O motor de OCR do Windows no idioma do usuario, ou `None`."""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return None
    try:
        return OcrEngine.try_create_from_user_profile_languages()
    except OSError:
        return None


def text_ocr_available() -> bool:
    return _engine() is not None


@dataclass(frozen=True)
class OcrPage:
    text: str
    lines: int


@dataclass(frozen=True)
class Word:
    """Uma palavra lida, com a caixa em pontos PDF (independe da resolucao)."""

    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def overlap(self, other: Word) -> float:
        """Interseccao sobre a menor das duas areas."""
        width = min(self.right, other.right) - max(self.x, other.x)
        height = min(self.bottom, other.bottom) - max(self.y, other.y)
        if width <= 0 or height <= 0:
            return 0.0
        smaller = min(self.width * self.height, other.width * other.height) or 1.0
        return (width * height) / smaller


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(word.y for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word.bottom for word in self.words)

    @property
    def left(self) -> float:
        return min(word.x for word in self.words)

    @property
    def right(self) -> float:
        return max(word.right for word in self.words)

    def text(self) -> str:
        return " ".join(word.text for word in sorted(self.words, key=lambda word: word.x))


def _recognize_lines(engine, data: bytes, width: int, height: int, dpi: int) -> list[Line]:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    writer = DataWriter()
    writer.write_bytes(bytes(data))
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height)

    async def run():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(run())
    scale = 72 / dpi
    lines: list[Line] = []
    for line in result.lines:
        words = [
            Word(
                word.text,
                word.bounding_rect.x * scale,
                word.bounding_rect.y * scale,
                word.bounding_rect.width * scale,
                word.bounding_rect.height * scale,
            )
            for word in line.words
        ]
        if words:
            lines.append(Line(words))
    return lines


def merge_readings(readings: Sequence[list[Line]]) -> list[Line]:
    """Junta leituras da mesma pagina: a primeira manda na ordem, as outras completam.

    Uma palavra de outra leitura entra se nenhuma palavra ja presente ocupa o
    mesmo lugar. Vai para a linha da mesma altura que a cobre na horizontal;
    sem linha assim, vira linha nova logo depois da mais proxima acima.
    """
    if not readings:
        return []
    merged = [Line(list(line.words)) for line in readings[0]]
    for reading in readings[1:]:
        for line in reading:
            for word in line.words:
                if not looks_like_text(word.text):
                    continue  # ruido da imagem de um diagrama, borda da pagina
                if any(word.overlap(existing) > 0.3 for current in merged for existing in current.words):
                    continue
                _place(merged, word)
    return merged


# O que uma leitura extra pode acrescentar: letras (com acento), digitos e a
# pontuacao da notacao. `ve„árawa` e `wwww„` sao o tabuleiro lido como texto.
_TEXT_CHARS_RE = re.compile(r"^[A-Za-zÀ-ÿ0-9.,;:()!?+#=×'\-]+$")


def looks_like_text(text: str) -> bool:
    if not _TEXT_CHARS_RE.match(text):
        return False
    letters = sum(char.isalnum() for char in text)
    if letters == 0:
        return False
    # Diagrama lido como texto vira sequencia de letras repetidas (`wwww`, `zzz`).
    return not re.search(r"(.)\1\1", text.lower())


def _place(lines: list[Line], word: Word) -> None:
    middle = (word.y + word.bottom) / 2
    best: Line | None = None
    best_gap = float("inf")
    for line in lines:
        if not line.top <= middle <= line.bottom:
            continue
        # Mesma altura; entre as colunas, a mais perto na horizontal.
        gap = max(0.0, line.left - word.right, word.x - line.right)
        if gap < best_gap:
            best, best_gap = line, gap
    if best is not None and best_gap < 3 * word.height:
        best.words.append(word)
        return

    new_line = Line([word])
    above = [
        index
        for index, line in enumerate(lines)
        if line.bottom <= middle and line.left < word.right and word.x < line.right + 3 * word.height
    ]
    position = max(above, key=lambda index: lines[index].bottom) + 1 if above else len(lines)
    lines.insert(position, new_line)


def recognize_renders(renders: Sequence[tuple[bytes, int, int, int]]) -> OcrPage:
    """OCR de uma pagina ja rasterizada em uma ou mais resolucoes.

    Cada item e `(bgra, largura, altura, dpi)` -- o `QImage.Format_ARGB32` na
    memoria. Bloqueia: chamar fora da thread da interface.
    """
    engine = _engine()
    if engine is None:
        raise TextOcrUnavailable(
            "O OCR de texto precisa do Windows 10/11 e dos pacotes do extra 'text-ocr' "
            "(uv pip install winrt-Windows.Media.Ocr ...)."
        )
    # Pagina inteira: `FULL_PAGE_PASSES` leituras. Medido nos livros de teste,
    # uma segunda leitura inteira custa 4x e acrescenta ~1 lance por pagina --
    # a releitura dos buracos, que so recorta o que falta, rende o mesmo por
    # uma fracao do tempo. As outras resolucoes ficam para ela.
    readings = [
        _recognize_lines(engine, data, width, height, dpi) for data, width, height, dpi in renders[:FULL_PAGE_PASSES]
    ]
    lines = merge_readings(readings)
    if renders:
        _reread_missing_lines(engine, lines, renders)
        _reread_gaps(engine, lines, renders)
    return OcrPage(text=tidy_ocr_notation("\n".join(line.text() for line in lines)), lines=len(lines))


# Um espaco entre palavras e ~0.3 da altura da letra. Bem mais que isso, no
# meio da linha, e uma palavra que o OCR jogou fora.
GAP_FACTOR = 1.4
# Margem branca em volta do recorte: o OCR do Windows nao le texto colado na borda.
_CROP_MARGIN_PX = 40


def _column_right(lines: list[Line], line: Line) -> float:
    """Onde termina a coluna desta linha: a linha mais comprida que a cobre."""
    return max(
        (other.right for other in lines if other.left < line.right and line.left < other.right),
        default=line.right,
    )


def _gaps(lines: list[Line], page_right: float = float("inf")) -> list[tuple[Line, float, float]]:
    """(linha, x inicial, x final) de cada buraco suspeito, em pontos."""
    found: list[tuple[Line, float, float]] = []
    for line in lines:
        words = sorted(line.words, key=lambda word: word.x)
        height = max(word.height for word in words)
        for left, right in zip(words, words[1:], strict=False):
            if right.x - left.right > GAP_FACTOR * height:
                found.append((line, left.right, right.x))
        # A palavra final some tanto quanto a do meio: olhar ate a margem da
        # coluna -- e nunca alem da coluna vizinha, que na mesma altura tem outro texto.
        end = min(page_right, _column_right(lines, line))
        neighbours = [
            other.left
            for other in lines
            if other is not line
            and other.left > words[-1].right
            and other.top < line.bottom
            and line.top < other.bottom
        ]
        if neighbours:
            end = min(end, min(neighbours) - height)
        if end - words[-1].right > 2 * height:
            found.append((line, words[-1].right, end))
    return found


def _crop_bgra(data: bytes, width: int, height: int, box: tuple[int, int, int, int]) -> tuple[bytes, int, int]:
    """Recorte `(x0, y0, x1, y1)` em pixels, com margem branca em volta."""
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3])
    crop_width, crop_height = x1 - x0, y1 - y0
    margin = _CROP_MARGIN_PX
    out_width = crop_width + 2 * margin
    white_row = b"\xff" * (out_width * 4)
    rows = [white_row] * margin
    side = b"\xff" * (margin * 4)
    for y in range(y0, y1):
        start = (y * width + x0) * 4
        rows.append(side + data[start : start + crop_width * 4] + side)
    rows.extend([white_row] * margin)
    return b"".join(rows), out_width, crop_height + 2 * margin


_GAP_WORD_RE = re.compile(r"[A-Za-z0-9]")
_LOWER_CAPTURE_RE = re.compile(r"^([pnbrqkcatd]{1,3})x([pnbrqkcatd]{1,3})$")


def _read_gap(engine, line: Line, start: float, end: float, render: tuple[bytes, int, int, int]) -> list[Word]:
    """As palavras que um recorte do buraco devolve, em pontos da pagina."""
    data, width, height, dpi = render
    scale = dpi / 72
    pad = 0.3 * (line.bottom - line.top)
    box = (
        int((start + 1) * scale),
        int((line.top - pad) * scale),
        int((end - 1) * scale),
        int((line.bottom + pad) * scale),
    )
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return []
    crop, crop_width, crop_height = _crop_bgra(data, width, height, box)
    offset = (box[0] - _CROP_MARGIN_PX) / scale
    words: list[Word] = []
    for found_line in _recognize_lines(engine, crop, crop_width, crop_height, dpi):
        for word in found_line.words:
            text = word.text.strip(".,;:()[]'`|Il")
            if len(_GAP_WORD_RE.findall(text)) < 2 or not looks_like_text(text):
                continue  # pedaco de letra cortada na borda do recorte
            capture = _LOWER_CAPTURE_RE.match(text)
            if capture:
                text = f"{capture.group(1).upper()}x{capture.group(2).upper()}"
            words.append(Word(text, word.x + offset, line.top, word.width, line.bottom - line.top))
    return words


def _reread_gaps(engine, lines: list[Line], renders: Sequence[tuple[bytes, int, int, int]]) -> None:
    """Rele, sozinho, cada buraco suspeito da linha; o que vier entra no lugar.

    Recortada do resto da frase, `PxP` e lida (as vezes como `pxp`, com a caixa
    errada, que `_LOWER_CAPTURE_RE` conserta). Cada resolucao e tentada ate uma
    devolver alguma coisa; recorte em branco nao devolve nada.
    """
    _data, width, _height, dpi = renders[0]
    for line, start, end in _gaps(lines, page_right=width / (dpi / 72)):
        for render in renders:
            words = _read_gap(engine, line, start, end, render)
            if words:
                line.words.extend(words)
                break


# Uma linha que falta cabe num espaco de ~1 a 2,6 alturas de linha. Menos que
# isso e entrelinha; mais e paragrafo, titulo ou diagrama -- ali o recorte so
# traz ruido do tabuleiro ou texto da coluna vizinha.
MISSING_LINE_MIN = 1.0
MISSING_LINE_MAX = 2.6
_NOTATION_TOKEN_RE = re.compile(
    r"[PCATDRNBQK][A-Z]{0,2}[1-8]|[a-h][1-8]|[PCATDRNBQK][A-Z]{0,2}x[PCATDRNBQK]|[0O]-[0O]|\d+\."
)


def _missing_line_regions(lines: list[Line]) -> list[tuple[int, Line]]:
    """(indice da linha de cima, sonda cobrindo o espaco) de cada vao suspeito."""
    if len(lines) < 3:
        return []
    heights = sorted(line.bottom - line.top for line in lines)
    typical = heights[len(heights) // 2]
    regions: list[tuple[int, Line]] = []
    for index, line in enumerate(lines):
        width = line.right - line.left
        below = [
            other
            for other in lines
            if other.top > line.bottom
            # Mesma coluna: as duas linhas se cobrem em pelo menos metade da menor.
            and min(line.right, other.right) - max(line.left, other.left) > 0.5 * min(width, other.right - other.left)
        ]
        if not below:
            continue
        following = min(below, key=lambda other: other.top)
        gap = following.top - line.bottom
        if not MISSING_LINE_MIN * typical <= gap <= MISSING_LINE_MAX * typical:
            continue
        left, right = min(line.left, following.left), max(line.right, following.right)
        probe = Line([Word("", left, line.bottom + 1, right - left, gap - 2)])
        regions.append((index, probe))
    return regions


def _reread_missing_lines(engine, lines: list[Line], renders: Sequence[tuple[bytes, int, int, int]]) -> None:
    """Rele o vao entre duas linhas da mesma coluna, onde cabia uma linha inteira.

    So entra o que nao repete palavra ja lida e tem pelo menos um token com
    cara de notacao -- o que se procura aqui e um lance perdido, nao prosa.
    """
    inserts: list[tuple[int, Line]] = []
    for index, probe in _missing_line_regions(lines):
        for render in renders:
            words = [
                word
                for word in _read_gap(engine, probe, probe.left, probe.right, render)
                if not any(word.overlap(existing) > 0.3 for line in lines for existing in line.words)
                and not any(
                    word.text == existing.text
                    for line in lines
                    for existing in line.words
                    if abs(existing.y - word.y) < 3 * (probe.bottom - probe.top)
                )
            ]
            if len(words) >= 2 and any(_NOTATION_TOKEN_RE.search(word.text) for word in words):
                inserts.append((index, Line(words)))
                break
    for index, line in sorted(inserts, key=lambda item: -item[0]):
        lines.insert(index + 1, line)


def rgb_to_bgra(samples: bytes, width: int, height: int) -> bytes:
    """RGB de 24 bits (o pixmap do PyMuPDF sem alfa) -> BGRA opaco, que o OCR quer."""
    pixels = width * height
    if len(samples) != pixels * 3:
        raise ValueError(f"esperados {pixels * 3} bytes RGB, vieram {len(samples)}")
    out = bytearray(pixels * 4)
    out[0::4] = samples[2::3]
    out[1::4] = samples[1::3]
    out[2::4] = samples[0::3]
    out[3::4] = b"\xff" * pixels
    return bytes(out)


def recognize_bgra(data: bytes, width: int, height: int, dpi: int = OCR_DPI) -> OcrPage:
    """OCR de uma unica imagem. Ver `recognize_renders`."""
    return recognize_renders([(data, width, height, dpi)])


# Captura com a vitima em minuscula (`P xc`, `Cxa`): o OCR errou a caixa da letra.
# So quando a letra sozinha nao pode ser coluna de SAN (`exd5` fica como esta):
# depois dela nao vem digito.
_LOWER_VICTIM_RE = re.compile(r"(?<![\w-])([PCATDRNBQK]{1,3})\s*[xX×]\s*([pcatdrnbqk])(?![\w])")
# `C XP`, `P X P`: `X` maiusculo e captura, nao letra.
_CAPITAL_X_RE = re.compile(r"(?<![\w-])([PCATDRNBQK]{1,3})\s*X\s*(?=[PCATDRNBQK](?![a-z]))")


def tidy_ocr_notation(text: str) -> str:
    """Conserta na notacao o que o OCR estraga, sem tocar em palavra de prosa."""
    text = _LOWER_VICTIM_RE.sub(lambda m: f"{m.group(1)}x{m.group(2).upper()}", text)
    text = _CAPITAL_X_RE.sub(lambda m: f"{m.group(1)}x", text)
    return text
