"""A **Claim**'s identity as a value code computes, so two runs agree without a judge.

A vote is worth collecting once. It is only worth collecting once if the thing
voted on can be recognised again in a later run, whose wording moves and whose
claim IDs are fresh every time. That recogniser is this module: a fingerprint
over the fields a claim carries, never over its prose.

**Versioned, and the version is the point.** A fingerprint is a hash, so
improving the recogniser changes every key. That would be fatal if a vote stored
only the hash — so a vote stores its :class:`Components`, and re-keying the whole
ledger under a new version is a pure recompute over stored fields, offline, with
no provider and no re-vote. The rule this repository already applies to a judge
change ("a new judge silently re-scores history") is answered here by making the
re-score explicit, total and free.

**Version 1 reads what a claim carries today.** Framework, lane and the
endpoint-resolved **Element** IDs. It has a measured cost:
``tests/test_evals_identity.py``'s ``endpoint subset`` row prices element
agreement alone at 14 false splits over 200 labelled pairs and 23 false merges
over 287 reference pairs.

**Version 2 adds the action verb**, which is what closes most of that gap: it
costs one more false split and removes twenty of the 23 false merges, and
:class:`~evals.harness.identity.SubsetVerbIdentity` scores 185/200 against the
recorded labels where element agreement alone scores 111.

It is the default. :class:`~stride_service.report.Claim` carries the verb and
:class:`~stride_service.frameworks.stride.record.DraftThreat` requires it, so a
finding out of a live run fingerprints at version 2 like a reference claim does.

**Version 1 stays computable, and that is not a compatibility shim.** A package
whose claims carry a catalog identifier composes no verb, so version 1 is the
rule its findings are keyed under — and any ledger row written before the field
existed re-keys to version 2 by recomputation rather than by a re-vote.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from evals.harness.identity import FlowMap, endpoint_form
from evals.harness.verbs import check_verb
from stride_service.report import FrameworkName

#: The version a caller gets when it does not choose. Bumping it is a re-keying
#: event, so it is a reviewed edit rather than a default that drifts — and a
#: cheap one, because :func:`~evals.harness.ledger.rekey` recomputes the whole
#: ledger from stored components with no re-vote and no provider.
#:
#: **Version 2 since the record carried the field.** Version 1 was the default
#: only while :class:`~stride_service.report.Claim` had no verb, which made a
#: finding out of a live run unfingerprintable at version 2. It has one now, so
#: the default is the rule that measures better.
DEFAULT_VERSION = 2

#: Which rule keys each framework's findings. **Keyed, never branched**, and
#: checked against ``PACKAGES`` by ``tests/test_evals_fingerprint.py`` — a table
#: nobody compares to its registry fails as quietly as the ``if`` it replaced.
#:
#: The entries are not a preference. They follow from what a package's claims
#: are: an open claim set has no identifier behind it, so the action is half of
#: what makes two claims one finding and the rule must read it. A claim carrying
#: a catalog requirement identifier already *is* identified, composes no verb,
#: and version 1 is not a lesser rule for it but the whole of the right one.
#:
#: So a package added to ``PACKAGES`` and missing here raises at the first
#: finding it produces, which is the question its author should answer: does
#: this package's claim carry its own identity, or compose one?
VERSION_FOR: dict[FrameworkName, int] = {
    "stride": 2,
    "asvs": 1,
}

#: Every version this module can compute. A key missing here raises rather than
#: falling back — a fingerprint quietly computed under the wrong rule is a vote
#: silently attached to the wrong finding.
SUPPORTED_VERSIONS = (1, 2)


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

    def to_json(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "lane": self.lane,
            "targets": list(self.targets),
            "verb": self.verb,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Components:
        try:
            return cls(
                framework=raw["framework"],
                lane=raw["lane"],
                targets=tuple(raw["targets"]),
                verb=raw.get("verb"),
            )
        except (KeyError, TypeError) as exc:
            raise FingerprintError(f"malformed components: {exc}") from exc


def version_for(framework: FrameworkName) -> int:
    """Which fingerprint version keys this framework's findings.

    Raises on a framework the table does not name, rather than falling back to
    :data:`DEFAULT_VERSION` — a package quietly keyed under the weaker rule
    would produce a ledger whose rows nobody could tell apart from the stronger
    one's, and the version in the value would say the wrong thing.
    """
    try:
        return VERSION_FOR[framework]
    except KeyError:
        raise FingerprintError(
            f"no fingerprint version is declared for {framework!r};"
            " add it to VERSION_FOR — 2 if its claims compose an identity from"
            " an action and a place, 1 if they carry a catalog identifier"
        ) from None


def components_for(
    framework: FrameworkName,
    lane: str,
    element_ids: Iterable[str],
    flows: FlowMap,
    verb: str | None = None,
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
    )


def fingerprint(components: Components, version: int = DEFAULT_VERSION) -> str:
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
    # NUL joins the parts because it cannot occur in any of them, so no value
    # can impersonate a boundary between two others.
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"v{version}:{digest[:16]}"


def version_of(value: str) -> int:
    """Which version produced this fingerprint, read back off the value."""
    head, _, rest = value.partition(":")
    if not rest or not head.startswith("v") or not head[1:].isdigit():
        raise FingerprintError(f"{value!r} is not a fingerprint")
    return int(head[1:])
