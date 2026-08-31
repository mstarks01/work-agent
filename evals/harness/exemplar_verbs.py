"""Which actions a package's exemplars demonstrate, and which its corpus asks for.

A lane agent learns its verb from the worked drafts in
``frameworks/<name>/lanes/<lane>/exemplars.md``. The reference sets under
``evals/corpus/`` grade it against a verb somebody else chose. Where those two
disagree, :class:`~evals.harness.identity.SubsetVerbIdentity` splits a pair whose
elements match, and the finding is scored as a miss and an over-report at once.

## The measurement, over STRIDE's 18 exemplars and 243 reference claims

**47 of 243 reference claims (19%) name a verb no exemplar in their lane
demonstrates.** 23 are ``must-find``. All 13 cases carry at least one.

They split into two populations, and each wants a different answer:

* **32 near misses** — the lane demonstrates the verb's *family* but not the
  member, so the agent has a wrong neighbour to reach for. ``use-credential``
  x9 against ``guess-credential``, ``disable`` x9 against ``flood``, ``plant``
  x6 against ``alter``.
* **15 with no neighbour** — the lane never demonstrates that family at all.
  ``denial-of-service`` holds most of them: its three exemplars all demonstrate
  ``flood``, and the corpus asks for four integrity verbs in that lane.

One further reading is over the exemplars alone. STRIDE ships exactly one
:class:`Collision`: the ``elevation-of-privilege`` canonical draft says
``abuse-grant`` and the unknown-conditional draft says ``escalate``, and the
second's element set is contained in the first's. That is the identity rule's
own split condition, sitting inside the shipped prompt text.

## Why this is a harness module and not a script

The numbers above move whenever either side is edited, and both sides are
edited often. A count nobody can regenerate is a claim about a tree that no
longer exists.

## Per framework, over each package's own exemplars and its own reference set

Read off :data:`~evals.harness.fingerprint.IDENTIFIER_OF` rather than named: a
package whose claims carry a catalog requirement identifier is identified by
that identifier, composes no verb, and has nothing here to compare. A package
that composes its identity from an action and a place is swept. So a package
added to ``PACKAGES`` is swept or skipped by its own declaration, and neither
answer needs an edit here.

Nothing here needs a provider. It reads shipped text and blessed reference sets,
which is why it can run on every PR.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import get_args

from analysis_service.actions import family_of
from analysis_service.deployment import DEFAULT_FRAMEWORKS_DIR
from analysis_service.frameworks import PACKAGES, package_for, schemas_for
from analysis_service.markdown_loader import MarkdownLoader, split_sections
from analysis_service.report import FrameworkName
from analysis_service.skills import lane_exemplars_doc
from evals.harness.fingerprint import IDENTIFIER_OF
from evals.harness.identity import endpoint_subset
from evals.harness.reference import GoldenCase

__all__ = [
    "Collision",
    "Exemplar",
    "LaneVerbs",
    "Undemonstrated",
    "collisions",
    "corpus_undemonstrated",
    "lane_verbs",
    "undemonstrated",
    "verb_keyed_frameworks",
]

#: A fenced ``json`` draft inside an exemplar file, anchored at the line start
#: so a fence quoted inside a draft cannot end one.
_JSON_BLOCK = re.compile(r"^```json\n(.*?)^```", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Exemplar:
    """One worked draft, reduced to what a verb comparison needs."""

    framework: FrameworkName
    lane: str
    heading: str
    verb: str
    element_ids: tuple[str, ...]


@dataclass(frozen=True)
class LaneVerbs:
    """What one lane's drafts demonstrate, as an agent reading them would learn."""

    framework: FrameworkName
    lane: str
    exemplars: tuple[Exemplar, ...]

    @property
    def verbs(self) -> frozenset[str]:
        return frozenset(exemplar.verb for exemplar in self.exemplars)

    @property
    def families(self) -> frozenset[str]:
        return frozenset(family_of(verb) for verb in self.verbs)

    def siblings(self, verb: str) -> tuple[str, ...]:
        """The demonstrated verbs of ``verb``'s family, in vocabulary order."""
        family = family_of(verb)
        return tuple(sorted(v for v in self.verbs if family_of(v) == family))


@dataclass(frozen=True)
class Undemonstrated:
    """One reference claim whose verb no exemplar in its lane demonstrates.

    ``siblings`` empty means the lane never demonstrates the family either, so
    the agent has nothing to reach for and the miss is a coverage gap. A
    non-empty ``siblings`` is the sharper finding: the lane teaches the family
    and the wrong member of it.
    """

    case: str
    framework: FrameworkName
    lane: str
    verb: str
    tier: str
    claim: str
    siblings: tuple[str, ...]

    @property
    def near_miss(self) -> bool:
        return bool(self.siblings)

    @property
    def must_find(self) -> bool:
        return self.tier == "must-find"


@dataclass(frozen=True)
class Collision:
    """Two drafts in one lane that the identity rule would split.

    Their element sets nest and their verbs differ, which is exactly what
    :class:`~evals.harness.identity.SubsetVerbIdentity` rules as one place and
    two actions. An agent shown both has been shown a choice and no rule for
    making it.
    """

    framework: FrameworkName
    lane: str
    verbs: tuple[str, str]
    headings: tuple[str, str]
    shared_element_ids: tuple[str, ...]


def verb_keyed_frameworks() -> tuple[FrameworkName, ...]:
    """The packages whose claims compose an identity from an action.

    A ``KeyError`` is the intended failure: a package registered in ``PACKAGES``
    and missing from :data:`~evals.harness.fingerprint.IDENTIFIER_OF` has not
    answered whether its claims carry their own identity, and that is the
    question this sweep depends on.
    """
    return tuple(name for name in PACKAGES if IDENTIFIER_OF[name] is None)


def lane_verbs(framework: FrameworkName) -> tuple[LaneVerbs, ...]:
    """One package's exemplars, per lane, in the package's declared lane order."""
    loader = MarkdownLoader(DEFAULT_FRAMEWORKS_DIR / framework)
    record = _proposal_type(framework)
    lanes = []
    for lane in package_for(framework).lanes:
        sections = split_sections(loader.load(lane_exemplars_doc(lane)))
        drafts = []
        for heading, body in sections.items():
            for block in _JSON_BLOCK.findall(body):
                draft = record.model_validate(json.loads(block))
                drafts.append(
                    Exemplar(
                        framework=framework,
                        lane=lane,
                        heading=heading,
                        verb=draft.verb,
                        element_ids=tuple(draft.affected_element_ids),
                    )
                )
        lanes.append(LaneVerbs(framework=framework, lane=lane, exemplars=tuple(drafts)))
    return tuple(lanes)


def undemonstrated(
    cases: Iterable[GoldenCase], framework: FrameworkName
) -> tuple[Undemonstrated, ...]:
    """Every reference claim of ``framework`` whose verb its lane never works.

    Cases that declare no reference set for the package are skipped rather than
    counted empty, on :func:`~evals.harness.triggers.corpus_recall`'s reasoning:
    a case a package's **Precondition** refuses carries no claims, and reading
    zero disagreements out of it would report agreement nobody measured.
    """
    by_lane = {entry.lane: entry for entry in lane_verbs(framework)}
    found = []
    for case in cases:
        if framework not in case.frameworks:
            continue
        for claim in case.claims_for(framework):
            entry = by_lane[claim.lane]
            if claim.verb is None or claim.verb in entry.verbs:
                continue
            found.append(
                Undemonstrated(
                    case=case.id,
                    framework=framework,
                    lane=claim.lane,
                    verb=claim.verb,
                    tier=claim.tier,
                    claim=claim.claim,
                    siblings=entry.siblings(claim.verb),
                )
            )
    return tuple(found)


def corpus_undemonstrated(
    cases: Sequence[GoldenCase],
) -> tuple[Undemonstrated, ...]:
    """The same reading over every package that composes a verb, in registry order."""
    return tuple(
        entry
        for framework in verb_keyed_frameworks()
        for entry in undemonstrated(cases, framework)
    )


def collisions(framework: FrameworkName) -> tuple[Collision, ...]:
    """Exemplar pairs inside one lane that name one place and two actions.

    **Same family only.** Two verbs of two families over one element set are two
    findings — reading a store and flooding it are both true of one store — and
    reporting them would bury the case that matters under every lane's ordinary
    coverage. Within one family the two drafts are two names for one shape, and
    the agent that read both was shown a choice and no rule for making it.

    The element halves are compared with an empty **Data Flow** map, because the
    exemplar systems are prose in ``prompts/analyze.md`` and no graph resolves
    their flow IDs. That makes the test the conservative one: a pair reported
    here nests on the IDs as written, and a pair that would nest only after a
    flow resolved into its endpoints is not reported.
    """
    found = []
    for entry in lane_verbs(framework):
        drafts = entry.exemplars
        for index, left in enumerate(drafts):
            for right in drafts[index + 1 :]:
                if left.verb == right.verb:
                    continue
                if family_of(left.verb) != family_of(right.verb):
                    continue
                if not endpoint_subset(left.element_ids, right.element_ids, {}):
                    continue
                found.append(
                    Collision(
                        framework=framework,
                        lane=entry.lane,
                        verbs=(left.verb, right.verb),
                        headings=(left.heading, right.heading),
                        shared_element_ids=tuple(
                            sorted(set(left.element_ids) & set(right.element_ids))
                        ),
                    )
                )
    return tuple(found)


def _proposal_type(framework: FrameworkName):
    """The record one package's lane agent emits a claim as.

    Resolved through ``schemas_for`` rather than named, so each package's drafts
    parse against their own shape: a draft parsed against another framework's
    record fails on every field that framework does not carry.
    """
    claims = schemas_for(framework).proposals.model_fields["claims"]
    return get_args(claims.annotation)[0]
