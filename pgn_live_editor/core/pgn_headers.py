import re

from .issues import INVALID_DATE, INVALID_RESULT, Issue

STANDARD_PGN_HEADERS = (
    ("Event", "PGN Live Editor Export"),
    ("Site", "?"),
    ("Date", "????.??.??"),
    ("Round", "?"),
    ("White", "?"),
    ("Black", "?"),
)

SEVEN_TAG_ROSTER = tuple(tag for tag, _default in STANDARD_PGN_HEADERS) + ("Result",)

VALID_RESULTS = ("1-0", "0-1", "1/2-1/2", "*")

HEADER_LINE_RE = re.compile(r'^\[(?P<tag>[A-Za-z0-9_]+)\s+"(?P<value>(?:[^"\\]|\\.)*)"\]\s*$')

_PGN_DATE_RE = re.compile(r'^[\d?]{4}\.[\d?]{2}\.[\d?]{2}$')
_YEAR_ONLY_RE = re.compile(r'^(\d{4})$')
_YEAR_MONTH_RE = re.compile(r'^(\d{4})[.\-/](\d{1,2})$')
_ISO_RE = re.compile(r'^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})$')
_DMY_RE = re.compile(r'^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$')


def normalize_pgn_date(value: str) -> str:
    """Converte o que os livros trazem para o formato `AAAA.MM.DD` do PGN.

    `1945` -> `1945.??.??` -- exatamente o caso que passou batido nos arquivos
    ja exportados, onde `[Date "1945"]` nao e uma data PGN valida.
    """
    value = (value or "").strip()
    if not value or value in {"?", "??"}:
        return "????.??.??"
    if _PGN_DATE_RE.match(value):
        return value

    match = _YEAR_ONLY_RE.match(value)
    if match:
        return f"{match.group(1)}.??.??"

    match = _YEAR_MONTH_RE.match(value)
    if match:
        return f"{match.group(1)}.{int(match.group(2)):02d}.??"

    match = _ISO_RE.match(value)
    if match:
        return f"{match.group(1)}.{int(match.group(2)):02d}.{int(match.group(3)):02d}"

    match = _DMY_RE.match(value)
    if match:
        return f"{match.group(3)}.{int(match.group(2)):02d}.{int(match.group(1)):02d}"

    return "????.??.??"


def normalize_pgn_result(value: str) -> str:
    value = (value or "").strip()
    if value in VALID_RESULTS:
        return value
    collapsed = value.replace(" ", "").replace("½", "1/2")
    if collapsed in VALID_RESULTS:
        return collapsed
    if collapsed in {"1/2", "0.5-0.5", "="}:
        return "1/2-1/2"
    return "*"


def _header_span(raw_text: str, tag: str, region_start: int = 0, region_end: int | None = None) -> tuple[int, int]:
    """Posicao de um cabecalho **dentro da regiao pedida**.

    A regiao importa desde que um documento passou a poder conter varias
    partidas: sem ela, o alerta de `Date` da partida 7 apontaria para o `Date`
    da partida 1.
    """
    region_end = len(raw_text) if region_end is None else region_end
    match = re.compile(rf'^\[{re.escape(tag)}\s+"[^"]*"\]', re.MULTILINE).search(
        raw_text, region_start, region_end
    )
    return (match.start(), match.end()) if match else (region_start, region_start)


def validate_headers(
    headers: dict[str, str],
    raw_text: str = "",
    region_start: int = 0,
    region_end: int | None = None,
) -> list[Issue]:
    """Alertas sobre cabecalhos que sairiam invalidos no PGN."""
    issues: list[Issue] = []

    raw_date = headers.get("Date")
    if raw_date is not None:
        normalized = normalize_pgn_date(raw_date)
        if normalized != raw_date.strip():
            start, end = _header_span(raw_text, "Date", region_start, region_end)
            issues.append(Issue(
                severity="warning",
                code=INVALID_DATE,
                message=f"Data '{raw_date}' nao esta no formato PGN; sera exportada como '{normalized}'.",
                raw_start=start,
                raw_end=end,
                raw_text=raw_date,
            ))

    raw_result = headers.get("Result")
    if raw_result is not None and raw_result.strip() not in VALID_RESULTS:
        normalized = normalize_pgn_result(raw_result)
        start, end = _header_span(raw_text, "Result", region_start, region_end)
        issues.append(Issue(
            severity="warning",
            code=INVALID_RESULT,
            message=f"Resultado '{raw_result}' nao e valido; sera exportado como '{normalized}'.",
            raw_start=start,
            raw_end=end,
            raw_text=raw_result,
        ))

    return issues


def _next_line_is_a_header(lines: list[str], index: int) -> bool:
    for line in lines[index:]:
        stripped = line.strip()
        if not stripped:
            continue
        return HEADER_LINE_RE.match(stripped) is not None
    return False


def extract_pgn_headers(raw_text: str) -> tuple[dict[str, str], str, int]:
    lines = raw_text.splitlines(keepends=True)
    consumed = 0
    index = 0
    found_headers = False
    headers: dict[str, str] = {}

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            # Linha em branco no meio do bloco de cabecalhos e PGN malformado,
            # mas aparece o tempo todo em texto de OCR. Antes, tudo o que vinha
            # depois dela era lido como movetext -- e `[White "..."]` ia parar
            # no corpo da partida, virando prosa.
            if found_headers and not _next_line_is_a_header(lines, index + 1):
                consumed += len(lines[index])
                index += 1
                break
            consumed += len(lines[index])
            index += 1
            continue

        match = HEADER_LINE_RE.match(stripped)
        if not match:
            if found_headers:
                break
            return ({}, raw_text, 0)

        found_headers = True
        headers[match.group("tag")] = _unescape_tag_value(match.group("value"))
        consumed += len(lines[index])
        index += 1

    if not found_headers:
        return ({}, raw_text, 0)

    while index < len(lines) and not lines[index].strip():
        consumed += len(lines[index])
        index += 1

    return (headers, raw_text[consumed:], consumed)


def build_header_text(headers: dict[str, str] | None = None, result: str = "*") -> str:
    merged = {tag: value for tag, value in STANDARD_PGN_HEADERS}
    extras: dict[str, str] = {}

    for tag, value in (headers or {}).items():
        if tag in merged:
            merged[tag] = value
        elif tag != "Result":
            extras[tag] = value

    lines = [f'[{tag} "{_escape_tag_value(merged[tag])}"]' for tag, _default in STANDARD_PGN_HEADERS]
    lines.extend(f'[{tag} "{_escape_tag_value(value)}"]' for tag, value in extras.items())
    lines.append(f'[Result "{_escape_tag_value((headers or {}).get("Result", result))}"]')
    return "\n".join(lines) + "\n\n"


def insert_or_replace_headers(raw_text: str) -> str:
    headers, body_text, _body_offset = extract_pgn_headers(raw_text)
    header_block = build_header_text(headers)
    return header_block + body_text.lstrip("\r\n")


def render_header_block(headers: dict[str, str]) -> str:
    """O bloco de cabecalhos como ele fica no texto bruto, com a linha em branco."""
    if not headers:
        return ""
    lines = [f'[{tag} "{_escape_tag_value(value)}"]' for tag, value in headers.items()]
    return "\n".join(lines) + "\n\n"


def merge_headers(existing: dict[str, str], updates: dict[str, str | None]) -> dict[str, str]:
    """Aplica alteracoes preservando a ordem em que os cabecalhos ja estavam.

    Valor `None` remove o cabecalho -- e assim que `SetUp` sai quando o `FEN`
    sai, sem que ninguem precise lembrar de apaga-lo a mao.
    """
    pending = dict(updates)
    merged: dict[str, str] = {}

    source = existing or {tag: default for tag, default in STANDARD_PGN_HEADERS} | {"Result": "*"}

    for tag, value in source.items():
        if tag in pending:
            new_value = pending.pop(tag)
            if new_value is None:
                continue
            merged[tag] = new_value
        else:
            merged[tag] = value

    for tag, value in pending.items():
        if value is not None:
            merged[tag] = value

    # O padrao PGN exige os sete cabecalhos obrigatorios primeiro e nesta ordem;
    # o que vier depois (FEN, SetUp, ECO, Annotator) mantem a ordem que tinha.
    ordered = {tag: merged[tag] for tag in SEVEN_TAG_ROSTER if tag in merged}
    ordered.update({tag: value for tag, value in merged.items() if tag not in ordered})
    return ordered


def upsert_headers(
    raw_text: str,
    updates: dict[str, str | None],
    region_start: int = 0,
    region_end: int | None = None,
) -> tuple[int, int, str] | None:
    """Trecho a substituir e novo bloco de cabecalhos, ou `None` se nada muda.

    Devolve posicoes em vez de texto pronto para que quem chama aplique a
    mudanca num unico bloco de desfazer, como todos os gestos da fase 3.
    """
    region_end = len(raw_text) if region_end is None else region_end
    chunk = raw_text[region_start:region_end]
    existing, _body, consumed = extract_pgn_headers(chunk)

    merged = merge_headers(existing, updates)
    block = render_header_block(merged)
    if block == chunk[:consumed]:
        return None

    return (region_start, region_start + consumed, block)


def _escape_tag_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_tag_value(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")
