"""A **Claim**'s identity as a value code computes, so two runs agree with no model call.

A vote is worth collecting once. It is only worth collecting once if the thing
voted on can be recognised again in a later run, whose wording moves and whose
claim IDs are fresh every time. That recogniser is this module: a fingerprint
over the fields a claim carries, never over its prose.

It is versioned, and the version is the point. A fingerprint is a hash, so
improving the recogniser changes every key. That would be fatal if a vote stored
only the hash, so a vote stores its :class:`Components` instead. Re-keying the
whole ledger under a new version is then a pure recompute over stored fields:
offline, with no provider and no re-vote. A model-scored history has the problem
that a new scorer silently re-scores everything, with no way to recompute the
old numbers. This module answers it by making the re-score explicit, total and
free.

Version 1 reads what a claim carries today: framework, lane, and the
endpoint-resolved **Element** IDs. It has a measured cost.
``tests/test_evals_identity.py``'s ``endpoint subset`` row prices element
agreement alone at 14 false splits of 200, 85 false merges of 115 candidate
negatives, and 23 false merges of 287 reference pairs.

Version 2 adds the action verb, which closes most of that gap. It costs one more
false split, and it takes the candidate merges from 85 to 5. Read the candidate
column rather than the reference one: on reference pairs alone the verb removes
twenty of 23 and version 1 looks survivable, and on the paraphrases a live run
emits it removes eighty of 85.
:class:`~evals.harness.identity.SubsetVerbIdentity` scores 295/315 against the
recorded labels, where element agreement alone scores 200/315.

Version 2 is the default. :class:`~analysis_service.report.Claim` carries the
verb, and :class:`~analysis_service.frameworks.stride.record.DraftThreat`
requires it, so a finding out of a live run fingerprints at version 2 as a
reference claim does.

Version 3 reads a catalog identifier instead of an action. A package whose
claims name a requirement in a published catalog is already identified, so the
rule that keys it is place plus that identifier: ASVS's ``V6.2.1``, in the
chapter it was ruled in, over the elements it names. Version 1 keyed such a
claim by place alone, which made two requirements ruled on one element in one
chapter a single fingerprint, and let one vote answer for both.

Every version stays computable, and that is not a compatibility shim. Which rule
keys a package is :data:`VERSION_FOR`, and the entries follow from what a
package's claims are. A ledger row written under an older rule re-keys by
recomputation rather than by a re-vote.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from analysis_service.frameworks.asvs.record import requirement_of
from analysis_service.report import FrameworkName
from evals.harness.identity import FlowMap, endpoint_form
from evals.harness.verbs import check_verb

#: The version a caller gets when it does not choose. Bumping it is a re-keying
#: event, so it is a reviewed edit rather than a default that drifts — and a
#: cheap one, because :func:`~evals.harness.ledger.rekey` recomputes the whole
#: ledger from stored components with no re-vote and no provider.
#:
#: Which rule keys each framework's findings. **Keyed, never branched**, and
#: checked against ``PACKAGES`` by ``tests/test_evals_fingerprint.py`` — a table
#: nobody compares to its registry fails as quietly as the ``if`` it replaced.
#:
#: The entries are not a preference. They follow from what a package's claims
#: are: an open claim set has no identifier behind it, so the action is half of
#: what makes two claims one finding and the rule must read it. A claim carrying
#: a catalog requirement identifier is identified by that identifier, composes
#: no verb, and is keyed by place plus the identifier.
#:
#: So a package added to ``PACKAGES`` and missing here raises at the first
#: finding it produces, which is the question its author should answer: does
#: this package's claim carry its own identity, or compose one?
VERSION_FOR: dict[FrameworkName, int] = {
    "stride": 2,
    "asvs": 3,
}

#: Which field on a claim names the lane it was reached in. **Keyed, never
#: branched**, and checked against ``PACKAGES`` by
#: ``tests/test_evals_fingerprint.py``.
#:
#: A package names its lane in its own terms — STRIDE reaches a claim in a
#: category, ASVS reaches one in a chapter — and both are the graph's fact
#: rather than anything an agent spelled. A reader that fell back to the
#: framework name keyed every one of a package's findings under one lane, which
#: made two findings in two chapters one fingerprint and let one vote answer
#: for both.
LANE_FIELD: dict[FrameworkName, str] = {
    "stride": "category",
    "asvs": "chapter",
}


#: How a package's claim names its catalog identifier, read off the claim ID.
#: **Keyed, never branched**, and checked against ``PACKAGES`` by
#: ``tests/test_evals_fingerprint.py``.
#:
#: ``None`` is a declaration and not a hole: it says this package's claims carry
#: no identifier, so its findings compose one from an action and a place. The
#: ASVS entry is that package's own parser — the identifier is the standard's,
#: and only the package that owns the catalog can read it out of a claim ID.
IDENTIFIER_OF: dict[FrameworkName, Callable[[str], str] | None] = {
    "stride": None,
    "asvs": requirement_of,
}


#: Every version this module can compute. A key missing here raises rather than
#: falling back — a fingerprint quietly computed under the wrong rule is a vote
#: silently attached to the wrong finding.
SUPPORTED_VERSIONS = (1, 2, 3)

#: Which component each version reads on top of the framework, lane and targets
#: every version hashes. ``None`` says this version reads those three alone.
#:
#: **Keyed, never branched.** This fact used to be an ``if version == 2`` beside
#: an ``if version == 3`` at every site that composed a claim, which is three
#: copies of one table and a fourth version that would have to find all of them.
#: :func:`key_claim` reads this instead, so a caller offers everything its claim
#: carries and the version decides what is kept.
#:
#: ``test_every_supported_version_declares_what_it_reads`` checks it against
#: :data:`SUPPORTED_VERSIONS` in both directions, because a table nobody
#: compares to its registry fails as quietly as the ``if`` it replaced.
EXTRA_COMPONENT: dict[int, str | None] = {
    1: None,
    2: "verb",
    3: "identifier",
}


class FingerprintError(ValueError):
    """The components cannot be fingerprinted under the version asked for."""


@dataclass(frozen=True)
class Components:
    """What a fingerprint is computed from, kept beside every vote.

    Stored rather than derived so that a version bump re-keys the ledger by
    recomputation. ``verb`` is optional because version 1 does not read it and
    no reference claim carries one yet; asking for version 2 without it is an
    error rather than a fingerprint over a silent empty string.

    ``targets`` arrives already endpoint-resolved — the caller holds the
    **Data Flow** map, and a component that re-resolved on every hash would make
    the value depend on a lookup rather than on itself.
    """

    framework: FrameworkName
    lane: str
    targets: tuple[str, ...]
    verb: str | None = None
    #: The catalog identifier this claim names, for a package whose claims carry
    #: one. ``None`` for a package that composes an identity instead, and
    #: version 3 refuses to hash without it.
    identifier: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "lane": self.lane,
            "targets": list(self.targets),
            "verb": self.verb,
            "identifier": self.identifier,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Components:
        try:
            return cls(
                framework=raw["framework"],
                lane=raw["lane"],
                targets=tuple(raw["targets"]),
                verb=raw.get("verb"),
                identifier=raw.get("identifier"),
            )
        except (KeyError, TypeError) as exc:
            raise FingerprintError(f"malformed components: {exc}") from exc


def identifier_of(framework: FrameworkName, claim_id: str) -> str | None:
    """The catalog identifier inside one claim ID, or ``None``.

    ``None`` where the package declares no parser, which is what a claim set
    with no catalog behind it means. A package that declares one and produces a
    claim ID it cannot read raises here rather than keying the finding under an
    empty identifier, where every unreadable ID would collapse into one.
    """
    try:
        reader = IDENTIFIER_OF[framework]
    except KeyError:
        raise FingerprintError(
            f"no identifier reader is declared for {framework!r};"
            " add it to IDENTIFIER_OF — that package's own parser if its claims"
            " name a catalog requirement, None if they compose an identity"
        ) from None
    if reader is None:
        return None
    identifier = reader(claim_id)
    if not identifier:
        raise FingerprintError(
            f"{framework!r} declares a catalog identifier and {claim_id!r}"
            " carries none; a claim ID its own package cannot read is a defect"
            " in the package, never a finding to key"
        )
    return identifier


def lane_field(framework: FrameworkName) -> str:
    """Which field of this package's claim carries its lane.

    Raises on a package the table does not name, for the same reason
    :func:`version_for` does: a claim whose lane cannot be read is a claim that
    would key under a constant, and every finding of that package would share
    one fingerprint per place.
    """
    try:
        return LANE_FIELD[framework]
    except KeyError:
        raise FingerprintError(
            f"no lane field is declared for {framework!r};"
            " add it to LANE_FIELD — the field its claim carries the lane in,"
            " which the graph stamps rather than an agent"
        ) from None


def version_for(framework: FrameworkName) -> int:
    """Which fingerprint version keys this framework's findings.

    Raises on a framework the table does not name, rather than falling back to
    a default — a package quietly keyed under another's rule would produce a
    ledger whose rows nobody could tell apart, and the version in the value
    would say the wrong thing. There is no default to fall back to: this table
    is the only answer to which rule keys a package, and a single value standing
    in for it is what made every ASVS vote fail while STRIDE's passed.
    """
    try:
        return VERSION_FOR[framework]
    except KeyError:
        raise FingerprintError(
            f"no fingerprint version is declared for {framework!r};"
            " add it to VERSION_FOR — 2 if its claims compose an identity from"
            " an action and a place, 3 if they name a catalog requirement"
        ) from None


def components_for(
    framework: FrameworkName,
    lane: str,
    element_ids: Iterable[str],
    flows: FlowMap,
    verb: str | None = None,
    identifier: str | None = None,
) -> Components:
    """Build the components for one claim, resolving its elements once.

    ``element_ids`` goes through :func:`~evals.harness.identity.endpoint_form`,
    which replaces a cited **Data Flow** with the two elements it runs between
    and drops a **Trust Boundary**. That is what makes one place in the graph
    spell one way: the corpus carries the same finding cited as a flow by one
    writer and as the process at the end of that flow by another, and without
    this they would fingerprint differently.
    """
    if verb is not None:
        check_verb(verb)
    return Components(
        framework=framework,
        lane=lane,
        targets=tuple(sorted(endpoint_form(element_ids, flows))),
        verb=verb,
        identifier=identifier,
    )


def fingerprint(components: Components, version: int) -> str:
    """The identity of a claim, as ``v<version>:<16 hex>``.

    The version rides in the value rather than beside it, so a ledger holding
    two versions cannot silently compare across them: the strings differ in
    their first characters, and nothing has to remember to check a field.

    Sixteen hex characters — 64 bits — because the population is a corpus, not a
    namespace: at ten thousand findings the chance of any collision is about one
    in fifty billion, and a shorter value is one a person can read out loud
    while pointing at a row in the ledger.
    """
    if version not in SUPPORTED_VERSIONS:
        raise FingerprintError(
            f"version {version!r} is not one this build computes"
            f" ({', '.join(str(known) for known in SUPPORTED_VERSIONS)})"
        )
    parts = [components.framework, components.lane, *sorted(components.targets)]
    if version == 2:
        if components.verb is None:
            raise FingerprintError(
                "version 2 reads the action verb and this claim carries none;"
                " assign one from evals.harness.verbs, or fingerprint at"
                " version 1"
            )
        parts.append(check_verb(components.verb))
    if version == 3:
        if not components.identifier:
            raise FingerprintError(
                "version 3 reads a catalog identifier and this claim names"
                " none; key a claim that composes its identity at version 2"
            )
        parts.append(components.identifier)
    # NUL joins the parts because it cannot occur in any of them, so no value
    # can impersonate a boundary between two others.
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"v{version}:{digest[:16]}"


def key_claim(
    framework: FrameworkName,
    lane: str,
    element_ids: Iterable[str],
    flows: FlowMap,
    verb: str | None = None,
    identifier: str | None = None,
) -> tuple[str, Components]:
    """One claim's fingerprint and components, under its own framework's rule.

    The single spelling of "which version keys this package, and which
    component that version reads". Every site that keys a produced claim goes
    through here — the review queue, the writing instrument and the tests that
    stand in for both — so a finding counted under one key and asked about
    under another cannot happen by one site being edited and another missed.

    **Offer everything the claim carries.** ``verb`` and ``identifier`` are
    both taken and :data:`EXTRA_COMPONENT` drops whichever the version does not
    read, rather than each caller deciding. A caller that guessed would compose
    a verb into an ASVS key, and the ledger would hold two spellings of one
    finding with nothing to say which was right.

    Returns the fingerprint first because every caller wants it, and the
    components beside it because the queue stores them on the vote — that is
    what lets :func:`~evals.harness.ledger.rekey` recompute the whole ledger
    under a new version with no re-vote.
    """
    version = version_for(framework)
    reads = EXTRA_COMPONENT[version]
    components = components_for(
        framework,
        lane,
        element_ids,
        flows,
        verb=verb if reads == "verb" else None,
        identifier=identifier if reads == "identifier" else None,
    )
    return fingerprint(components, version=version), components


def version_of(value: str) -> int:
    """Which version produced this fingerprint, read back off the value."""
    head, _, rest = value.partition(":")
    if not rest or not head.startswith("v") or not head[1:].isdigit():
        raise FingerprintError(f"{value!r} is not a fingerprint")
    return int(head[1:])
