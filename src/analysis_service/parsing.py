"""Reading a value out of a string or a file this service does not control.

One rule, one reader. Four call sites parse an integer out of somebody else's
string — two HTTP headers a client sets, a retention setting an operator sets,
and a version segment read back off a stored digest or fingerprint — and each
of them has to answer the same question first: does this string spell a number
``int`` will accept?

``str.isdigit`` is not that question, and never was. It passes ``"²"``, which
``int`` refuses with a ``ValueError``, and it passes fullwidth ``"１００"``,
which ``int`` accepts as 100 — so one value has two spellings and a guard that
uses it lets both through as the same number. It also says nothing about
length, and ``int`` refuses a string past 4300 digits by raising.

Both refusals surface as a traceback where the caller wanted a decision: a 500
on a header a client controls, or an unhandled ``ValueError`` in a preflight
that meant to report a malformed value by name.

A number a JSON file holds has its own question: is it a finite amount of zero
or more? ``bool`` is an ``int`` subclass, and ``json`` reads ``NaN`` and
``Infinity``, so ``isinstance`` alone is not that question either.
:func:`non_negative` answers it.

Three more questions about a file's values have one reader each here.
:func:`is_version` asks whether a version is the integer a loader expects,
because TOML and JSON read ``9.0`` and ``true`` as values equal to an integer.
:func:`strings` asks whether a value is a list of strings, because a string
in that place reads one character at a time. :func:`signer` asks who signed a
file, because a value of spaces reads as a signature. :data:`Signer` is the
model field that asks it as the file loads.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, TypeGuard

from pydantic import BeforeValidator

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


def non_negative(value: object) -> TypeGuard[int | float]:
    """Whether ``value`` is a finite number of zero or more, and not a bool."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def is_version(value: object, expected: int) -> bool:
    """Whether ``value`` is the integer ``expected``, and not a float or a bool."""
    return type(value) is int and value == expected


def strings(value: object) -> TypeGuard[list[str]]:
    """Whether ``value`` is a list that holds strings only."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def signer(value: object) -> str | None:
    """Who ``value`` names as a signer, or ``None`` where nobody signed.

    ``None`` and ``""`` are unsigned. Any other value that names nobody, a
    non-string or a string of whitespace, raises ``ValueError``: it reads as
    signed and names no person.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"the signer {value!r} names nobody")
    return value


#: A model field that says who signed, read through :func:`signer` as it loads.
#: It holds a name or ``None``, so ``is None`` is the whole unsigned test.
Signer = Annotated[str | None, BeforeValidator(signer)]
