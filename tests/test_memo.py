"""A result kept by the content it was built from (ADR 0078)."""

from __future__ import annotations

import gc
import weakref
from collections import OrderedDict

from analysis_service.memo import REMEMBERED, Memo, remembered


class _Registry:
    """An object with no text form, which a memo key holds by identity."""


def test_the_same_content_builds_once():
    memo: Memo[list[str]] = OrderedDict()
    built = []

    def build():
        built.append("once")
        return built

    registry = _Registry()
    first = remembered(memo, ("a", "b"), build, held=(registry,))
    again = remembered(memo, ("a", "b"), build, held=(registry,))
    assert first is again and built == ["once"]
    assert remembered(memo, ("a", "c"), lambda: ["other"], held=(registry,)) == [
        "other"
    ]


def test_a_held_object_lives_while_its_key_is_kept():
    """A key holds the object's identity. While the key is kept, the object
    cannot be collected, so no other object can take its identity and read
    a value built from the first."""
    memo: Memo[str] = OrderedDict()
    registry = _Registry()
    alive = weakref.ref(registry)
    remembered(memo, ("a",), lambda: "built", held=(registry,))
    del registry
    gc.collect()
    assert alive() is not None
    for n in range(REMEMBERED):
        remembered(memo, (str(n),), lambda: "newer")
    gc.collect()
    assert alive() is None, "a dropped key releases what it held"


def test_the_memo_keeps_the_newest_values():
    memo: Memo[int] = OrderedDict()
    for n in range(REMEMBERED + 3):
        remembered(memo, (str(n),), lambda n=n: n)
    assert len(memo) == REMEMBERED
    assert remembered(memo, ("0",), lambda: -1) == -1, "the oldest was dropped"
    assert remembered(memo, (str(REMEMBERED + 2),), lambda: -1) == REMEMBERED + 2
