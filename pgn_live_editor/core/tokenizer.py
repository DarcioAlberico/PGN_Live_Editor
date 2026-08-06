import re
from dataclasses import dataclass

from .normalizer import NormalizedText


@dataclass
class Token:
    type: str
    value: str
    start_index: int
    end_index: int
    normalized_start_index: int | None = None
    normalized_end_index: int | None = None


class PGNTokenizer:
    """
    Scans the normalized text and yields tokens that represent structural concepts:
    - MOVE_NUMBER (e.g. '1.', '12...')
    - SAN_MOVE (e.g. 'Nf3', 'e4', 'O-O-O')
    - COMMENT (e.g. '{brilliant}')
    - VARIANT_OPEN / VARIANT_CLOSE
    - RESULT (e.g. '1-0', '*')
    - NAG (e.g. '$1', '!?')
    - TEXT for loose OCR residue
    """

    TRADITIONAL_NAG_PATTERN = r'(?:!!|\?\?|!\?|\?!|!|\?)'
    SYMBOLIC_NAG_PATTERN = r'(?:∞|±|∓|⩲|⩱|=|□|⨀|⊙)'
    NAG_PATTERN = rf'(?:\$\d+|{TRADITIONAL_NAG_PATTERN}|{SYMBOLIC_NAG_PATTERN})'

    TOKEN_PATTERNS = [
        ('MOVE_NUMBER', r'\b\d+\s*(?:\.\.\.|\.\.)?\s*\.'),
        (
            'SAN_MOVE',
            r'(?<!\S)(?:'
            r'[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[KQRBN])?[\+#]?|'
            r'[O0]-[O0]-[O0][\+#]?|'
            r'[O0]-[O0][\+#]?'
            rf')(?=\s|$|[(){{}}]|{NAG_PATTERN})'
        ),
        ('COMMENT', r'\{[^}]*\}'),
        ('VARIANT_OPEN', r'\('),
        ('VARIANT_CLOSE', r'\)'),
        ('RESULT', r'(?:\b(?:1-0|0-1|1/2-1/2)\b|\*)'),
        ('NAG', NAG_PATTERN),
        ('TEXT', r'[^\s()]+')
    ]

    def __init__(self):
        regex_parts = [f'(?P<{name}>{pattern})' for name, pattern in self.TOKEN_PATTERNS]
        self.tokenizer_regex = re.compile('|'.join(regex_parts))

    def tokenize(self, source: str | NormalizedText) -> list[Token]:
        if isinstance(source, NormalizedText):
            text = source.text
            normalized = source
        else:
            text = source
            normalized = None

        tokens: list[Token] = []
        for match in self.tokenizer_regex.finditer(text):
            for name, _pattern in self.TOKEN_PATTERNS:
                value = match.group(name)
                if not value or not value.strip():
                    continue

                normalized_start = match.start(name)
                normalized_end = match.end(name)

                if normalized is None:
                    start_index = normalized_start
                    end_index = normalized_end
                else:
                    start_index, end_index = normalized.raw_span_for(normalized_start, normalized_end)

                tokens.append(Token(
                    type=name,
                    value=value.strip(),
                    start_index=start_index,
                    end_index=end_index,
                    normalized_start_index=normalized_start,
                    normalized_end_index=normalized_end
                ))
                break

        return tokens
