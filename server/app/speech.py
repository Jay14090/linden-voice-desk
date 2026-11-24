"""Text rewrites applied just before TTS.

The LLM writes phone numbers as digits ("91827 36450"), which it copies
reliably. Turning them into words is done here in code, because when the
model did it itself it sometimes dropped or swapped a digit.
"""

from __future__ import annotations

import re

_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

# a 10-digit mobile, optionally +91 / 0 prefixed, with optional space or dash groupings
_PHONE = re.compile(r"(?<![\d])(?:\+?91[\s-]?|0)?((?:\d[\s-]?){9}\d)(?![\d])")


def spell_digits(digits: str) -> str:
    """'9182736450' -> 'nine one eight two seven, three six four five zero'."""
    words = [_WORDS[int(c)] for c in digits]
    return " ".join(words[:5]) + ", " + " ".join(words[5:])


def spell_phone_numbers_text(text: str) -> str:
    def repl(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        return spell_digits(digits) if len(digits) == 10 else m.group(0)

    return _PHONE.sub(repl, text)


async def spell_phone_numbers(text: str, text_type: str) -> str:
    """Pipecat TextTransform: (text, text_type) -> text."""
    return spell_phone_numbers_text(text)
