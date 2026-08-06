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


def _header_span(raw_text: str, tag: str) -> tuple[int, int]:
    match = re.search(rf'^\[{re.escape(tag)}\s+"[^"]*"\]', raw_text, re.MULTILINE)
    return (match.start(), match.end()) if match else (0, 0)


def validate_headers(headers: dict[str, str], raw_text: str = "") -> list[Issue]:
    """Alertas sobre cabecalhos que sairiam invalidos no PGN."""
    issues: list[Issue] = []

    raw_date = headers.get("Date")
    if raw_date is not None:
        normalized = normalize_pgn_date(raw_date)
        if normalized != raw_date.strip():
            start, end = _header_span(raw_text, "Date")
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
        start, end = _header_span(raw_text, "Result")
        issues.append(Issue(
            severity="warning",
            code=INVALID_RESULT,
            message=f"Resultado '{raw_result}' nao e valido; sera exportado como '{normalized}'.",
            raw_start=start,
            raw_end=end,
            raw_text=raw_result,
        ))

    return issues


def extract_pgn_headers(raw_text: str) -> tuple[dict[str, str], str, int]:
    lines = raw_text.splitlines(keepends=True)
    consumed = 0
    index = 0
    found_headers = False
    headers: dict[str, str] = {}

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            if found_headers:
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


def _escape_tag_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_tag_value(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")
