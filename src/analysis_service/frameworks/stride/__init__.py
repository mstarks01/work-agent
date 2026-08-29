"""The STRIDE framework package: this repo's original analysis, as a package.

Nine members and a text root at ``frameworks/stride/``. Everything here is
*profile* — the tailoring this service applies — and nothing declares a catalog:
STRIDE is a method rather than a published requirement set, so there is no
external artifact to carry or to check a declaration against.
"""

from __future__ import annotations

from types import MappingProxyType

from pydantic import BaseModel, ConfigDict

from analysis_service.frameworks import (
    FrameworkPackage,
    IdRule,
    KnowledgeTables,
    PreconditionResult,
)
from analysis_service.frameworks.stride.record import (
    CATEGORY_LETTERS,
    ID_FORMAT,
    STRIDE_CATEGORIES,
    STRIDE_VERSION,
    DraftThreat,
)
from analysis_service.frameworks.stride.rules import RULES
from analysis_service.system_model import SystemModel

__all__ = ["CASES", "NOTES", "STRIDE", "StrideOptions"]


class StrideOptions(BaseModel):
    """The job-level values STRIDE needs, of which there are none.

    A declared empty model rather than an absent member. Every package declares
    its options, so a caller submitting ``{"name": "stride"}`` is submitting a
    complete selection rather than relying on an omission, and ``extra="forbid"``
    means an option a caller invented for STRIDE is refused on the input ladder
    instead of being ignored.
    """

    model_config = ConfigDict(extra="forbid")


def stride_precondition(model: SystemModel) -> PreconditionResult:
    """What STRIDE asks of a **Valid System Model**: nothing beyond validity.

    **Total, and declared rather than absent.** STRIDE-per-element applies to
    any system that can be drawn as a data-flow graph, which is exactly what a
    valid model is, so every one of them satisfies this. Writing it out is what
    keeps the precondition a required member: a package exempt from declaring
    one would be a package whose scope nothing states, and a framework that
    *does* scope itself would then be the odd case rather than the ordinary one.
    """
    del model  # every valid model satisfies STRIDE
    return "satisfied"


# Document -> the candidate rules that select it. The direction is
# document-to-rules because what a maintainer edits is a document and its
# applicability, and a rule-keyed table would spread one document's entry
# across the file.
#
# Every rule ID here must exist in :data:`~analysis_service.frameworks.stride.
# rules.RULES` and every document must exist under this package's text root.
# The package gate holds both true at deployment construction, so an edit to
# either side that forgets the other refuses to start rather than silently
# dropping material out of an agent's context.
NOTES: dict[str, tuple[str, ...]] = {
    "identity-at-a-boundary": (
        "spoofing-unverified-boundary-auth",
        "spoofing-unverified-external-caller",
    ),
    "callback-and-webhook-trust": ("spoofing-unverified-external-caller",),
    "transport-protection": (
        "tampering-unprotected-transit-crossing",
        "information-disclosure-unprotected-sensitive-transit",
    ),
    "write-path-integrity": ("tampering-unverified-write-to-store",),
    "attribution-and-audit": ("repudiation-unattributable-action",),
    "protection-at-rest": ("information-disclosure-store-at-rest-unverified",),
    "cost-of-an-unauthenticated-request": (
        "denial-of-service-internet-exposed-process",
    ),
    "failure-coupling": ("denial-of-service-shared-dependency",),
    "privilege-transitions": ("elevation-of-privilege-privilege-zone-crossing",),
    "compromise-inheritance": ("elevation-of-privilege-inbound-from-exposed-process",),
}

# The same table for worked cases. A case may be selected by rules in several
# lanes on purpose: the judgement it demonstrates — an unknown control is not a
# missing one, a candidate the prose already answers — is not a property of one
# lane, and the agent that receives it is whichever lane's rule fired.
CASES: dict[str, tuple[str, ...]] = {
    "unknown-is-not-absent": (
        "spoofing-unverified-boundary-auth",
        "information-disclosure-store-at-rest-unverified",
        "tampering-unprotected-transit-crossing",
    ),
    "stated-control-outside-the-model": (
        "spoofing-unverified-boundary-auth",
        "tampering-unverified-write-to-store",
    ),
    "spoofing-or-elevation": (
        "elevation-of-privilege-privilege-zone-crossing",
        "spoofing-unverified-external-caller",
    ),
    "two-threats-one-flow": (
        "information-disclosure-unprotected-sensitive-transit",
        "tampering-unverified-write-to-store",
    ),
    "chained-benign-facts": (
        "elevation-of-privilege-inbound-from-exposed-process",
        "denial-of-service-shared-dependency",
    ),
    "shared-credential-attribution": ("repudiation-unattributable-action",),
}


STRIDE = FrameworkPackage(
    name="stride",
    version=STRIDE_VERSION,
    lanes=STRIDE_CATEGORIES,
    rules=RULES,
    record=DraftThreat,
    id_rule=IdRule(
        template=ID_FORMAT,
        # Widened from ``StrideCategory`` to ``str``, because ``IdRule`` keys by
        # lane and a lane is a plain slug on the contract: the package gate is
        # what checks this table covers every declared lane.
        prefix=MappingProxyType(
            {str(lane): letter for lane, letter in CATEGORY_LETTERS.items()}
        ),
        lane_field="category",
    ),
    options=StrideOptions,
    precondition=stride_precondition,
    knowledge=KnowledgeTables(
        notes=MappingProxyType(NOTES), cases=MappingProxyType(CASES)
    ),
)
