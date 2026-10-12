"""A result kept by the content it was built from (ADR 0078).

The early list and the capability effects table are each rebuilt on every
read of a paused job. A read that changes no answer reads the same content,
so :func:`remembered` builds it once and keeps it.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import TypeVar

__all__ = ["REMEMBERED", "Memo", "remembered"]

#: How many built values each memo keeps, newest last.
REMEMBERED = 64

_Built = TypeVar("_Built")

#: A memo: each key's held objects and its built value, oldest first.
Memo = OrderedDict[str, tuple[tuple[object, ...], _Built]]


def remembered(
    memo: Memo[_Built],
    parts: Sequence[str],
    build: Callable[[], _Built],
    *,
    held: Sequence[object] = (),
) -> _Built:
    """``build()`` once for its content, then the kept value.

    **A memo is keyed by content, never by a job.** ``parts`` is everything
    the build reads that has a text form, so a checkpoint, a selection, a
    policy and the saved answers each give their own key, and a changed
    answer is a new key with no invalidation to get wrong. ``held`` is each
    object the build reads that has none, such as a registry of functions:
    the key holds its identity, and the memo keeps a reference to it, so the
    identity cannot pass to another object while the key is kept. A kept
    value is immutable and shared. The memo holds :data:`REMEMBERED` values
    and drops the oldest.
    """
    key = hashlib.sha256(
        "\x1f".join([*parts, *(str(id(each)) for each in held)]).encode("utf-8")
    ).hexdigest()
    if key in memo:
        memo.move_to_end(key)
        return memo[key][1]
    built = build()
    memo[key] = (tuple(held), built)
    while len(memo) > REMEMBERED:
        memo.popitem(last=False)
    return built
