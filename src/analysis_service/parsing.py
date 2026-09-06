"""Reading a number out of a string this service does not control.

One rule, one reader. Three call sites parse an integer out of somebody else's
string — an HTTP header a client sets, and a version segment read back off a
stored fingerprint — and each of them has to answer the same question first:
does this string spell a number ``int`` will accept?

``str.isdigit`` is not that question, and never was. It passes ``"²"``, which
``int`` refuses with a ``ValueError``, and it passes fullwidth ``"１００"``,
which ``int`` accepts as 100 — so one value has two spellings and a guard that
uses it lets both through as the same number. It also says nothing about
length, and ``int`` refuses a string past 4300 digits by raising.

Both refusals surface as a traceback where the caller wanted a decision: a 500
on a header a client controls, or an unhandled ``ValueError`` in a preflight
that meant to report a malformed value by name.
"""

from __future__ import annotations

import re

_ASCII_DIGITS = re.compile(r"[0-9]+")


def ascii_int(value: str, *, max_digits: int) -> int | None:
    """The integer ``value`` spells, or ``None`` where it spells none.

    ``None`` rather than an exception, because every caller has a better answer
    than raising: a header that is not a number is a header that was not sent,
    and a fingerprint that is not one is refused by name.

    ``max_digits`` is the caller's own bound and has no default. What counts as
    too long depends on what the number means, and a shared guess would be
    either too small for one caller or no bound at all for another.
    """
    if len(value) > max_digits or _ASCII_DIGITS.fullmatch(value) is None:
        return None
    return int(value)
