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

**Version 2 adds the action verb**, which is what closes most of that gap —
:mod:`evals.harness.verbs` separates 20 of those 23. It is selectable now and
is not the default, because twelve of thirteen cases carry no verb yet:
``tests/test_verb_coverage.py`` names that debt and shrinks it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from evals.harness.identity import FlowMap, endpoint_form
from evals.harness.verbs import check_verb
from stride_service.report import FrameworkName

#: The version a caller gets when it does not choose. Version 1 until the corpus
#: carries verbs; bumping this is a re-keying event, so it is a reviewed edit
#: rather than a default that drifts.
DEFAULT_VERSION = 1

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
