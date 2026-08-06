import re
import unicodedata
from dataclasses import dataclass


@dataclass
class NormalizedText:
    text: str
    raw_spans: list[tuple[int, int]]

    def raw_span_for(self, start: int, end: int) -> tuple[int, int]:
        if start >= end or start < 0 or end > len(self.raw_spans):
            return (start, end)

        spans = self.raw_spans[start:end]
        return (min(span[0] for span in spans), max(span[1] for span in spans))


class LiveTextNormalizer:
    """
    Cleans up common OCR artifacts and normalizes chess pieces
    to standard English algebraic notation, with support for locales.
    """

    PIECE_GLYPHS = {
        '\u2654': 'K', '\u2655': 'Q', '\u2656': 'R', '\u2657': 'B', '\u2658': 'N', '\u2659': '',
        '\u265A': 'K', '\u265B': 'Q', '\u265C': 'R', '\u265D': 'B', '\u265E': 'N', '\u265F': ''
    }

    LOCALE_MAPPINGS = {
        'pt': {'D': 'Q', 'T': 'R', 'B': 'B', 'C': 'N'},
        'es': {'D': 'Q', 'T': 'R', 'A': 'B', 'C': 'N'},
        'de': {'K': 'K', 'D': 'Q', 'T': 'R', 'L': 'B', 'S': 'N'}
    }

    OCR_SUBSTITUTIONS = {
        "4J": "N",
        "tj": "N",
        "0-0-0": "O-O-O",
        "0-0": "O-O",
        "ch": "+",
        "mate": "#",
        "++": "#",
        "=/+": "\u2A71",
        "+/=": "\u2A72",
        "+-": "\u00B1",
        "-+": "\u2213",
        "=": "="
    }

    UNICODE_REPLACEMENTS = {
        '\u2013': '-',
        '\u2014': '-',
        '\u2212': '-',
        '\u2032': "'",
        '`': "'",
        '\u00B4': "'",
        '\u201C': '"',
        '\u201D': '"',
    }

    def __init__(self, locale: str = 'en'):
        self.locale = locale
        self.custom_substitutions = self.OCR_SUBSTITUTIONS.copy()

    def load_custom_dictionary(self, dictionary: dict[str, str]):
        """Load user-defined OCR correction dictionary."""
        self.custom_substitutions.update(dictionary)

    def _expand_spans(self, source_spans: list[tuple[int, int]], output_length: int) -> list[tuple[int, int]]:
        if output_length <= 0:
            return []

        if not source_spans:
            return [(0, 0)] * output_length

        if output_length == 1:
            return [(source_spans[0][0], source_spans[-1][1])]

        source_length = len(source_spans)
        if output_length <= source_length:
            spans: list[tuple[int, int]] = []
            for index in range(output_length):
                chunk_start = int(index * source_length / output_length)
                chunk_end = max(chunk_start + 1, int((index + 1) * source_length / output_length))
                chunk = source_spans[chunk_start:chunk_end]
                spans.append((chunk[0][0], chunk[-1][1]))
            return spans

        tail_anchor = source_spans[-1][1]
        spans = list(source_spans)
        spans.extend([(tail_anchor, tail_anchor)] * (output_length - source_length))
        return spans

    def _normalize_unicode(self, text: str, raw_spans: list[tuple[int, int]]) -> NormalizedText:
        normalized_chars: list[str] = []
        normalized_spans: list[tuple[int, int]] = []

        for char, raw_span in zip(text, raw_spans, strict=False):
            translated = self.UNICODE_REPLACEMENTS.get(char, char)
            normalized_char = unicodedata.normalize('NFKC', translated)
            if not normalized_char:
                continue

            normalized_chars.append(normalized_char)
            normalized_spans.extend(self._expand_spans([raw_span], len(normalized_char)))

        return NormalizedText("".join(normalized_chars), normalized_spans)

    def _replace_pattern(
        self,
        text: str,
        raw_spans: list[tuple[int, int]],
        pattern: str,
        replacement
    ) -> NormalizedText:
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0

        for match in re.finditer(pattern, text):
            start, end = match.span()
            parts.append(text[cursor:start])
            spans.extend(raw_spans[cursor:start])

            replacement_text = replacement(match) if callable(replacement) else replacement
            source_spans = raw_spans[start:end]
            parts.append(replacement_text)
            spans.extend(self._expand_spans(source_spans, len(replacement_text)))
            cursor = end

        parts.append(text[cursor:])
        spans.extend(raw_spans[cursor:])
        return NormalizedText("".join(parts), spans)

    def _replace_literal(
        self,
        text: str,
        raw_spans: list[tuple[int, int]],
        old: str,
        new: str
    ) -> NormalizedText:
        if not old:
            return NormalizedText(text, raw_spans)
        return self._replace_pattern(text, raw_spans, re.escape(old), new)

    def _apply_piece_translation(self, text: str, raw_spans: list[tuple[int, int]]) -> NormalizedText:
        if self.locale == 'en':
            return NormalizedText(text, raw_spans)

        mapping = self.LOCALE_MAPPINGS.get(self.locale, {})
        if not mapping:
            return NormalizedText(text, raw_spans)

        def repl(match):
            piece = match.group(1)
            rest = match.group(2)
            if piece in mapping:
                return mapping[piece] + rest
            return match.group(0)

        return self._replace_pattern(text, raw_spans, r'\b([A-Z])([a-h\-\+x=#1-8]+)\b', repl)

    def _apply_outside_comments(self, text: str, raw_spans: list[tuple[int, int]], transform) -> NormalizedText:
        parts: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0
        index = 0

        while index < len(text):
            if text[index] != '{':
                index += 1
                continue

            if cursor < index:
                normalized = transform(text[cursor:index], raw_spans[cursor:index])
                parts.append(normalized.text)
                spans.extend(normalized.raw_spans)

            comment_end = text.find('}', index)
            if comment_end == -1:
                comment_end = len(text) - 1

            comment_slice_end = comment_end + 1
            parts.append(text[index:comment_slice_end])
            spans.extend(raw_spans[index:comment_slice_end])
            index = comment_slice_end
            cursor = comment_slice_end

        if cursor < len(text):
            normalized = transform(text[cursor:], raw_spans[cursor:])
            parts.append(normalized.text)
            spans.extend(normalized.raw_spans)

        return NormalizedText("".join(parts), spans)

    def normalize_with_mapping(self, raw_text: str, base_offset: int = 0) -> NormalizedText:
        text = raw_text
        raw_spans = [(base_offset + index, base_offset + index + 1) for index in range(len(raw_text))]

        normalized = self._normalize_unicode(text, raw_spans)
        text, raw_spans = normalized.text, normalized.raw_spans

        glyph_chars: list[str] = []
        glyph_spans: list[tuple[int, int]] = []
        for char, raw_span in zip(text, raw_spans, strict=False):
            replacement = self.PIECE_GLYPHS.get(char, char)
            if not replacement:
                continue
            glyph_chars.append(replacement)
            glyph_spans.extend(self._expand_spans([raw_span], len(replacement)))
        text = "".join(glyph_chars)
        raw_spans = glyph_spans

        for bad_ocr, good_san in sorted(self.custom_substitutions.items(), key=lambda item: len(item[0]), reverse=True):
            normalized = self._apply_outside_comments(
                text,
                raw_spans,
                lambda current_text, current_spans, old=bad_ocr, new=good_san: self._replace_literal(current_text, current_spans, old, new)
            )
            text, raw_spans = normalized.text, normalized.raw_spans

        normalized = self._apply_outside_comments(text, raw_spans, self._apply_piece_translation)
        text, raw_spans = normalized.text, normalized.raw_spans

        normalized = self._apply_outside_comments(
            text,
            raw_spans,
            lambda current_text, current_spans: self._replace_pattern(
                current_text,
                current_spans,
                r'\b(\d+)((?:\s*\.){1,3})\s*',
                lambda match: (
                    f"{match.group(1)}... "
                    if match.group(2).count('.') >= 2
                    else f"{match.group(1)}. "
                )
            )
        )
        return normalized

    def normalize(self, raw_text: str) -> str:
        return self.normalize_with_mapping(raw_text).text
