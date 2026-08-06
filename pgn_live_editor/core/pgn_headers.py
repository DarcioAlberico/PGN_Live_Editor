import re

STANDARD_PGN_HEADERS = (
    ("Event", "PGN Live Editor Export"),
    ("Site", "?"),
    ("Date", "????.??.??"),
    ("Round", "?"),
    ("White", "?"),
    ("Black", "?"),
)

HEADER_LINE_RE = re.compile(r'^\[(?P<tag>[A-Za-z0-9_]+)\s+"(?P<value>(?:[^"\\]|\\.)*)"\]\s*$')


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
