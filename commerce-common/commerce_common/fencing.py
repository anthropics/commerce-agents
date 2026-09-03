# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Sanitizing and fencing text the model reads as data. Each role defines one ``Fence``;
its label is a source literal, never built from runtime values, so untrusted text cannot
reproduce the boundary. Every pattern here is linear on hostile input: it runs on the
event loop before truncation.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

# Zero-width, bidi, and format controls: the usual carriers for hidden instructions.
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD),  # soft hyphen
    (0x200B, 0x200F),  # zero-width space/joiners, LRM/RLM
    (0x2028, 0x2029),  # line/paragraph separators
    (0x202A, 0x202E),  # bidi embedding/overrides
    (0x2060, 0x2064),  # word joiner, invisible operators
    (0x2066, 0x2069),  # bidi isolates
    (0x061C, 0x061C),  # Arabic letter mark
    (0x180E, 0x180E),  # Mongolian vowel separator
    (0x206A, 0x206F),  # deprecated format controls
    (0xFE00, 0xFE0F),  # variation selectors
    (0xFFF9, 0xFFFB),  # interlinear annotation controls
    (0xFEFF, 0xFEFF),  # byte-order mark / zero-width no-break space
    (0xE0000, 0xE007F),  # tag characters, which spell invisible ASCII
    (0xE0100, 0xE01EF),  # variation selectors supplement
    # The remaining General Category Cf code points. Prefix and interlinear format
    # controls render as nothing in most stacks, so they carry hidden text the same
    # way the ranges above do.
    (0x0600, 0x0605),  # Arabic number/year/footnote/sign marks
    (0x06DD, 0x06DD),  # Arabic end of ayah
    (0x070F, 0x070F),  # Syriac abbreviation mark
    (0x0890, 0x0891),  # Arabic pound/piastre marks above
    (0x08E2, 0x08E2),  # Arabic disputed end of ayah
    (0x110BD, 0x110BD),  # Kaithi number sign
    (0x110CD, 0x110CD),  # Kaithi number sign above
    (0x13430, 0x13438),  # Egyptian hieroglyph format controls
    (0x1BCA0, 0x1BCA3),  # Duployan shorthand format controls
    (0x1D173, 0x1D17A),  # musical beam/slur/phrase controls
    # Not Cf, but they render as nothing and survive NFKC, so they break a label or a
    # role word just as well. U+3164 and U+FFA0 fold to U+1160 before this runs.
    (0x034F, 0x034F),  # combining grapheme joiner
    (0x115F, 0x1160),  # Hangul choseong/jungseong fillers
    (0x2800, 0x2800),  # braille pattern blank
)
_INVISIBLE = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _INVISIBLE_RANGES) + "]")

# C0/C1 control characters except tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# A forged turn boundary: a blank line, then a full role word and a colon. Mid-sentence
# role words, single-newline headings, and one-letter list markers ("A:") do not match.
_TURN_INDICATOR = re.compile(
    r"((?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)[ \t]*)(human|assistant|system|user)[ \t]*:",
    re.IGNORECASE,
)

# The same marker at the start of a body: the fence's own newline would complete the
# blank line, which the in-body pattern cannot see, so it is applied at wrap time.
_LEADING_TURN_INDICATOR = re.compile(r"^(\s*)(human|assistant|system|user)[ \t]*:", re.IGNORECASE)

# Transcript and tool-call markup, optionally namespaced. Only tag-shaped text matches
# (bare, closing, or with name="value" attributes), so "<system requirements>" passes;
# `parameter` and `result` count only when namespaced. Quantifiers are bounded and
# non-adjacent, which is what keeps this linear on unclosed input.
_TAG_ATTRS = (
    r"(?:[ \t]+[\w:.-]{1,40}[ \t]*=[ \t]*(?:\"[^\"]{0,200}\"|'[^']{0,200}'|[^\s\"'>]{1,200})){0,8}"
)
_SPECIAL_TOKEN = re.compile(
    r"<[ \t]*/?[ \t]*(?:"
    r"(?:[a-z][\w.-]{0,30}:)?(?:transcript|conversation|function_calls|function_results"
    r"|invoke|tool_use|tool_result|system|human|user|assistant)"
    r"|[a-z][\w.-]{0,30}:(?:parameter|result)"
    r")\b" + _TAG_ATTRS + r"[ \t]*/?>"
    r"|<\|[^|<>\r\n]{1,64}\|>",
    re.IGNORECASE,
)

_WHITESPACE_RUN = re.compile(r"\s+")

# The configs' default ``max_fenced_chars``.
MAX_FENCED_CHARS = 12_000


@cache
def _marker_pattern(label: str) -> re.Pattern[str]:
    # A marker is the label after an opening bracket, with or without the slash, spaces,
    # attributes, or the closing bracket (``</label x="">``, ``< /label>``, ``</label``).
    return re.compile(rf"<\s*/?\s*{re.escape(label)}(?![A-Za-z0-9_])(?:[^<>]*>)?", re.IGNORECASE)


@dataclass(frozen=True)
class Fence:
    """The tag that wraps third-party content and the notice the static prompt carries
    about it."""

    label: str
    notice: str

    @property
    def open(self) -> str:
        return f"<{self.label}>"

    @property
    def close(self) -> str:
        return f"</{self.label}>"

    def sanitize_text(self, text: str, max_chars: int | None = None) -> str:
        """``max_chars`` bounds the result including the truncation suffix, so a schema
        limit can be passed as is."""
        text = unicodedata.normalize("NFKC", text)
        text = _INVISIBLE.sub("", text)
        text = _CONTROL.sub(" ", text)
        # Markers and tokens are removed to a fixpoint, so one nested inside another
        # (``</label</label>>``) does not reassemble after the inner one goes.
        marker = _marker_pattern(self.label)
        while True:
            stripped = _SPECIAL_TOKEN.sub("[removed]", marker.sub("[removed]", text))
            if stripped == text:
                break
            text = stripped
        text = _TURN_INDICATOR.sub(r"\1\2 -", text)
        if max_chars is not None and len(text) > max_chars:
            suffix = " ...[truncated]"
            if max_chars > len(suffix):
                text = text[: max_chars - len(suffix)] + suffix
            else:
                text = text[:max_chars]
        return text

    def sanitize_value(self, value: Any, max_chars: int | None = None) -> Any:
        if isinstance(value, str):
            return self.sanitize_text(value, max_chars)
        if isinstance(value, dict):
            return {
                self.sanitize_text(str(k), 200): self.sanitize_value(v, max_chars)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            # json.dumps serializes tuples natively, so they must be walked here too.
            return [self.sanitize_value(v, max_chars) for v in value]
        return value

    def fence_payload(self, payload: Any, max_chars: int = MAX_FENCED_CHARS) -> str:
        """The sanitized payload inside the fence. String leaves are sanitized in place;
        any other object is sanitized as it is stringified, so a ``__str__`` cannot carry
        a marker in."""
        sanitized = self.sanitize_value(payload)
        if isinstance(sanitized, str):
            body = sanitized
        else:
            body = json.dumps(
                sanitized, ensure_ascii=False, default=lambda v: self.sanitize_text(str(v))
            )
        if len(body) > max_chars:
            body = body[:max_chars] + " ...[truncated]"
        body = _LEADING_TURN_INDICATOR.sub(r"\1\2 -", body)
        return f"{self.open}\n{body}\n{self.close}"


# Enforced at payload validation rather than in the tool schema, which is frozen.
SUGGESTION_CHIP_MAX_CHARS = 80


def sanitize_label(text: Any, max_chars: int) -> str:
    """Model text shown to a person as one line (a chip, a status line): invisible and
    control characters out, whitespace collapsed, cut to ``max_chars`` with an ellipsis;
    empty when nothing visible is left."""
    line = _INVISIBLE.sub("", str(text or ""))
    line = _CONTROL.sub(" ", line)
    line = _WHITESPACE_RUN.sub(" ", line).strip()
    if len(line) > max_chars:
        line = line[: max_chars - 1].rstrip() + "…"
    return line


def sanitize_suggestion_chips(
    chips: Sequence[str], max_chips: int = 4, max_chars: int = SUGGESTION_CHIP_MAX_CHARS
) -> list[str]:
    """Chips as one-line button labels: each through :func:`sanitize_label`, empty ones
    dropped, at most ``max_chips``."""
    cleaned: list[str] = []
    for chip in chips:
        if label := sanitize_label(chip, max_chars):
            cleaned.append(label)
        if len(cleaned) == max_chips:
            break
    return cleaned


def truncate_display(text: str, max_chars: int) -> str:
    """Text shown to a person, cut at a word boundary with an ellipsis."""
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-—–") + "…"
