"""What a vote read, as two values code computes, beside the identity of the topic.

A **Claim**'s fingerprint names the topic: this lane, this action, this place.
It is deliberately blind to everything a claim says, because a topic has to be
recognisable in a later run whose wording moves
(:mod:`evals.harness.fingerprint`). That blindness has a cost, and the
2026-09-09 audit measured it: every retained case 01 title and explanation was
replaced with text asserting that no attacker action exists, and the scorer
still returned 12 matches. A vote keyed by the topic alone outlives a rewrite of
everything the voter judged.

This module is the other half, and ADR 0029 is the record that decided its
shape. A vote records **two** digests of what it was shown, and the two answer
two different questions:

``structural``
    What a substance vote judges: the verdict the critic reached, the
    catalogued grounds the claim rests on, and the package's own ratings where
    its record grades harm. Quotes and prose are left out.

``prose``
    What a style vote judges: the description and the mitigations, the words on
    the page.

**Prose is out of the structural digest because it was measured, not because it
is unimportant.** Over the six case 01 STRIDE runs of one configuration, a
digest reading the description and the mitigations was shared by 0 of 234 pairs
of runs on one fingerprint: a model writes new prose every run, so a substance
vote bound to prose would go stale on every sweep and no standing would ever
carry forward. The structural digest was shared by 155 of 234. That is the whole
argument for the split, and ADR 0029 carries the table.

**Read off the record's fields, never off a framework name.**
:data:`STRUCTURAL_FIELDS` and :data:`PROSE_FIELDS` name the shared judgement
fields a package's record may declare, and a claim contributes whichever ones
its own record carries. A package that grades nothing declares no severity and
digests less; a package nobody has written yet digests what it declares. This is
the table-not-branch rule applied to a record instead of a registry, and
``tests/test_evals_content.py`` checks both tables against the shared types
:mod:`analysis_service.claims` defines.

Whitespace is the one thing normalised in the prose digest, because the page is
HTML and HTML collapses it: two strings that render as one string must digest as
one value, or a re-wrapped paragraph would expire a vote over a difference
nobody can see. Case, punctuation and word order are left alone — a reader sees
those, so the value has to move on them.

Both values are versioned in their own prefix, the way a fingerprint is, so a
ledger holding two versions cannot compare across them by accident. Bumping a
version does **not** re-key anything: these values say what a past reviewer was
shown, and recomputing them under a new rule would rewrite that record. So a
bump expires the older votes' claim on the current content rather than moving
them, which is the opposite of
:func:`~evals.harness.ledger.rekey` and is why the digests and the fingerprint
are separate values.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

from analysis_service.claims import Claim, Ground, Mitigation, Severity, Verdict
from analysis_service.parsing import ascii_int

#: Every version of the structural digest this build computes, and the one a
#: caller gets when it does not choose.
STRUCTURAL_VERSIONS = (1,)
STRUCTURAL_VERSION = 1

#: The same, for the prose digest. Versioned apart because the two rules change
#: for different reasons: the structural one moves when a package declares a new
#: judgement field, and the prose one when the page shows different words.
PROSE_VERSIONS = (1,)
PROSE_VERSION = 1

#: How each value announces which rule produced it.
STRUCTURAL_PREFIX = "s"
PROSE_PREFIX = "p"


class ContentError(ValueError):
    """A claim cannot be digested, or a stored digest cannot be read."""


def _verdict_parts(verdict: Verdict) -> tuple[str, ...]:
    """The critic's conclusion, and nothing it wrote about it.

    ``status`` alone. ``reason`` and ``rejected_because`` are prose, and
    ``related_unknowns`` already reach the digest as grounds.
    """
    return (verdict.status,)


def _severity_parts(severity: Severity) -> tuple[str, ...]:
    """The two axes a package grades, never the band they derive.

    ``level`` is shipped arithmetic over these two, so digesting it would count
    one fact twice; ``justification`` is prose and belongs to the other digest.
    """
    return (severity.likelihood, severity.impact)


#: Which shared judgement fields the structural digest reads, and what it takes
#: from each. **Keyed, never branched**: a claim contributes the entries its own
#: record declares, so no framework is named here and a package that grades
#: nothing contributes nothing.
#:
#: ``tests/test_evals_content.py`` checks this against the judgement types
#: :mod:`analysis_service.claims` defines, in both directions — a table nobody
#: compares to its registry fails as quietly as the ``if`` it replaced.
STRUCTURAL_FIELDS: dict[str, Callable[[Any], tuple[str, ...]]] = {
    "verdict": _verdict_parts,
    "severity": _severity_parts,
}


def _mitigation_parts(mitigations: Iterable[Mitigation]) -> tuple[str, ...]:
    """Every mitigation's words, in the order the page prints them."""
    return tuple(
        part for item in mitigations for part in (item.summary, item.detail) if part
    )


#: Which fields the prose digest reads. ``description`` sits on every claim;
#: ``mitigations`` only on a record that proposes one. The title is out by ADR
#: 0029's own argument: the identity already carries what it says, and a retitle
#: for readability would expire every vote a sitting produced.
PROSE_FIELDS: dict[str, Callable[[Any], tuple[str, ...]]] = {
    "description": lambda text: (text,),
    "mitigations": _mitigation_parts,
}

#: Runs of whitespace the page collapses into one space. ``\s`` under Unicode
#: covers the separators a model emits, U+2028, U+2029 and U+0085 among them.
_WHITESPACE = re.compile(r"\s+")


def _rendered(text: str) -> str:
    """One string as the page shows it: composed, collapsed and trimmed."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _ground_parts(ground: Ground) -> tuple[str, ...]:
    """One ground as a catalogue entry: its kind, its place, its attribute.

    The quote's text and its source label are left out. A lane that re-quotes a
    longer span of the same sentence is making the same argument, and the
    grounding ladder already proves the span is present.

    The place comes from :attr:`~analysis_service.claims.Ground.place`, which is
    that question's one reader. A second ``or`` chain here read the same two
    fields, so a sixth branch carrying a new place field would have updated
    ``place`` and left this digesting an empty string — two grounds at two
    places sharing one value, and a re-argued claim reading live. ``term``
    stays outside it, because ``place`` answers ``""`` for an
    ``absent-element`` on purpose: that branch names the whole model and has no
    element to point at.
    """
    return (ground.kind, ground.place or ground.term, ground.attribute)


def structural(claim: Claim, version: int = STRUCTURAL_VERSION) -> str:
    """What a substance vote judged, as ``s<version>:<16 hex>``.

    Sixteen hex characters for the reason a fingerprint uses them: the
    population is a corpus rather than a namespace, and a value a person can
    read out loud beside a ledger row is worth more than the collision margin it
    spends.
    """
    _check_version(version, STRUCTURAL_VERSIONS, "structural")
    parts: list[str] = []
    for ground in claim.grounds:
        parts.extend(_ground_parts(ground))
    for name, read in STRUCTURAL_FIELDS.items():
        if name in type(claim).model_fields:
            parts.extend(read(getattr(claim, name)))
    return _digest(STRUCTURAL_PREFIX, version, parts)


def prose(claim: Claim, version: int = PROSE_VERSION) -> str:
    """What a style vote judged, as ``p<version>:<16 hex>``."""
    _check_version(version, PROSE_VERSIONS, "prose")
    parts: list[str] = []
    for name, read in PROSE_FIELDS.items():
        if name in type(claim).model_fields:
            parts.extend(_rendered(value) for value in read(getattr(claim, name)))
    return _digest(PROSE_PREFIX, version, parts)


def _check_version(version: int, known: tuple[int, ...], which: str) -> None:
    if version not in known:
        raise ContentError(
            f"{which} version {version!r} is not one this build computes"
            f" ({', '.join(str(value) for value in known)})"
        )


def _digest(prefix: str, version: int, parts: Iterable[str]) -> str:
    # NUL joins the parts for the reason it joins a fingerprint's: it occurs in
    # none of them, so no value can impersonate a boundary between two others.
    joined = "\0".join(parts)
    return (
        f"{prefix}{version}:{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:16]}"
    )


#: How long a version segment may be, for the reason ``fingerprint`` bounds its
#: own: ``int`` raises on a string past 4300 digits, so an unbounded read
#: answered "this is not a digest" with a traceback.
_VERSION_DIGITS = 2


def version_of(value: str, prefix: str) -> int:
    """Which version produced this digest, read back off the value.

    ``prefix`` is required rather than inferred, so a caller expecting one kind
    cannot be handed the other: a prose digest stored where a structural one
    belongs would read live against every claim.
    """
    head, _, rest = value.partition(":")
    version = (
        ascii_int(head[1:], max_digits=_VERSION_DIGITS)
        if rest and head.startswith(prefix)
        else None
    )
    if version is None:
        raise ContentError(f"{value!r} is not a {prefix!r} digest")
    return version
