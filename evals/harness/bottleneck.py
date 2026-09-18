"""Ten hand-authored shapes, put through the System Model, and every loss placed (#1033).

#1033 asks whether requiring source knowledge to fit graph identity, type,
placement and interaction structure loses facts the sources state. A recall
figure cannot answer it: a route that reads badly and a route that binds badly
score alike, and :mod:`evals.harness.oracle` already showed that a *perfect*
reading of the signed corpus survives the deterministic path whole. A ceiling
is insensitive to a shape the corpus does not hold.

So this instrument supplies the shapes. Each fixture is a few sentences written
by hand, with the facts a careful reader finds in them written out beside the
words — no model, no corpus, no reference. Each one is then read three ways,
and the three answer different questions:

* **Direct construction.** Can the **System Model** and the assertion contract
  hold these facts when a person writes them out? A fixture the schema cannot
  hold is a representational limit.
* **The adapter.** Does :func:`~analysis_service.factbundle.resolve_bundle`
  keep them? A fixture the schema holds and the adapter drops is an
  implementation defect, and the two must never be reported as one finding.
* **The consumer.** Does anything downstream read the fact once it is kept? A
  row in a catalog nothing reads has survived in the accounting and nowhere
  else.

**These are diagnostics and never extraction evidence.** Every fixture's facts
are authored, so nothing here says how often a real reading meets the shape.
:func:`charge_misses` is the other half and answers that: it reads archived
emissions this repository already paid for and charges every missed reference
row to the earliest stage that did not carry it.

## What a survival means

Each fixture declares what a reader must end up with, and each want takes one
of four survivals:

* ``structural``: it reached a graph attribute a framework rule reads.
* ``catalog``: it reached the assertion catalog and no graph attribute, so a
  consumer reaches it only where a deployment sets ``ANALYSIS_ASSERTIONS``.
* ``question``: it reached the sidecar as an open question — named, answerable,
  and read by nothing.
* ``lost``: it reached none of the three.

## Framework parity

Every fixture is framework-neutral, because every one reads the System Model
and the System Model is the service's rather than a package's. Where a want
names a consumer it names the rule by its own declaration —
``stride.rules``'s store-tampering rule reads ``operations``, ASVS's classified
store test reads ``data_classification`` — so a package added tomorrow answers
the same fixtures through whichever attributes its own rules read.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from analysis_service.assertions import (
    SUBJECT_PREFIXES,
    AssertionCatalog,
    Qualifier,
    QuoteProposal,
    apply_projection,
    assertion_id,
    conflicts,
)
from analysis_service.factbundle import (
    FactProposal,
    InteractionProposal,
    MentionProposal,
    Resolution,
    SourceFactBundle,
    resolve_bundle,
)
from analysis_service.patch import Operation, PatchBatch, apply_patch
from analysis_service.system_model import (
    UNKNOWN,
    Assumption,
    DataFlow,
    DataStore,
    Element,
    Process,
    SystemModel,
    TrustBoundary,
)
from analysis_service.validation import validate
from evals.harness.alignment import Alignment, align
from evals.harness.arms import required_rows
from evals.harness.artifact import load_artifact
from evals.harness.bundle import heads_from_reports
from evals.harness.modes import AssertionResult
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.replay import (
    ADJUDICATED_WRONG,
    AliasTarget,
    ReferenceRowFate,
    SignedReference,
    replay_assertions,
    signed_reference,
)
from evals.harness.score_arms import SPEC, parse_spec

#: Where one wanted fact ended up. Ordered best to worst, so a table reads
#: down.
Survival = Literal["structural", "catalog", "question", "lost"]
SURVIVALS: tuple[Survival, ...] = ("structural", "catalog", "question", "lost")

#: What a fixture's loss is a property of. ``schema`` is a fact neither target
#: can express; ``adapter`` is one the schema holds and the resolver drops;
#: ``consumer`` is one both keep and nothing reads. Kept apart because the
#: corrections are different work and #1033 asks for separate verdicts.
Cause = Literal["none", "schema", "adapter", "consumer"]


@dataclass(frozen=True)
class Want:
    """One fact a reader of the fixture's sources must end up holding.

    ``about`` is which thing the fact is about, in the words the sources use.
    It is a label and never a match key: an **Element ID** is a function of the
    name, and two of these fixtures exist because two things the sources name
    alike cannot both carry one. So a want is matched by what it says rather
    than by whom it says it of, and each want takes a **distinct** carrier, so
    two wants are never answered by one row.

    ``attribute`` names the graph field a rule would read it through, or ``""``
    where the registry gives the predicate no such field. ``carried`` is how
    that attribute spells the value where the two differ — a stated absence is
    ``absent`` in the catalog and ``none`` in the graph — and defaults to
    ``value``. ``reader`` names the deterministic consumer, in its own
    spelling, for the report to cite.
    """

    about: str
    predicate: str
    value: str
    attribute: str = ""
    carried: str = ""
    reader: str = ""

    @property
    def spelled(self) -> str:
        """How the graph attribute writes this want's value."""
        return self.carried or self.value

    @property
    def label(self) -> str:
        return f"{self.about} / {self.predicate}"


@dataclass(frozen=True)
class Fixture:
    """One shape, its sources, the bundle a careful reader writes, and the wants.

    ``direct`` is the faithful construction in the current schema, hand-written
    where it differs from what the adapter builds. It is ``None`` where the
    adapter keeps every want, because the adapter's own output is then a
    construction and a second one would say nothing.
    """

    key: str
    asks: str
    sources: Mapping[str, str]
    bundle: SourceFactBundle
    wants: tuple[Want, ...]
    direct: SystemModel | None = None
    #: A review pass applied to the resolved bundle, where the fixture's
    #: subject is what a *correction* does to facts that were already there.
    patch: PatchBatch | None = None
    note: str = ""


@dataclass(frozen=True)
class Diagnosis:
    """One fixture, read three ways."""

    key: str
    asks: str
    survivals: Mapping[str, Survival]
    lost_rows: tuple[tuple[str, str, str], ...]
    direct_holds: bool
    direct_issues: tuple[str, ...]
    cause: Cause
    #: Disagreements :func:`~analysis_service.assertions.conflicts` derives from
    #: the rows this fixture produced. A fixture whose subject is two sources
    #: disagreeing wants one.
    conflicts: int = 0
    #: Whether a fixture's review batch was discarded whole, or ``None`` where
    #: the fixture carries no batch.
    rolled_back: bool | None = None
    note: str = ""

    @property
    def met(self) -> bool:
        """Whether every want reached a reader."""
        return all(one == "structural" for one in self.survivals.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "fixture": self.key,
            "asks": self.asks,
            "survivals": dict(self.survivals),
            "lost": [
                {"kind": kind, "handle": handle, "code": code}
                for kind, handle, code in self.lost_rows
            ],
            "direct_holds": self.direct_holds,
            "direct_issues": list(self.direct_issues),
            "cause": self.cause,
            "conflicts": self.conflicts,
            "rolled_back": self.rolled_back,
        }


# --- Authoring helpers -------------------------------------------------------


def quotes(label: str, *texts: str) -> list[QuoteProposal]:
    """The proposed quotes one bundle row carries, all from one source."""
    return [QuoteProposal(source_label=label, quote=text) for text in texts]


def mention(handle: str, text: str, roles: Sequence[str], cite: str, label: str):
    """One named thing, with the role or roles the sources give it."""
    return MentionProposal.model_validate(
        {
            "handle": handle,
            "text": text,
            "roles": list(roles),
            "quotes": quotes(label, cite),
        }
    )


def interaction(
    handle: str,
    initiator: str,
    receiver: str,
    action: str,
    cite: str,
    label: str,
    protocol: str = UNKNOWN,
    operations: str = "unknown",
):
    """One interaction, in the direction the sources say it is initiated."""
    return InteractionProposal.model_validate(
        {
            "handle": handle,
            "initiator": initiator,
            "receiver": receiver,
            "action": action,
            "protocol": protocol,
            "operations": operations,
            "quotes": quotes(label, cite),
        }
    )


def fact(
    handle: str,
    kind: str,
    subject: str,
    predicate: str,
    value: str,
    cite: str,
    label: str,
    *,
    reason: str | None = None,
    scope: Sequence[Qualifier] = (),
):
    """One fact the sources state about one subject."""
    return FactProposal.model_validate(
        {
            "handle": handle,
            "subject_kind": kind,
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "reason": reason,
            "scope": list(scope),
            "basis": "stated",
            "quotes": quotes(label, cite),
        }
    )


# --- The ten shapes ----------------------------------------------------------
#
# One source label per fixture, because a fixture's subject is the schema and
# never the multi-source join — except the two whose subject *is* two sources
# disagreeing, which carry their own second label.

NOTE = "Architecture note"
INTERVIEW = "Interview"


def _unknown_placement() -> Fixture:
    """A component the sources name, in a network they never state."""
    text = (
        "The office network and the plant network are kept apart.\n"
        "A licence server checks keys over TLS for the press controller, and"
        " nobody wrote down which network the licence server sits on.\n"
        "The press controller sits on the plant network."
    )
    return Fixture(
        key="unknown-placement",
        asks="existence and supported security facts survive an unstated placement",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "off", "office network", ["network-zone"], "office network", NOTE
                ),
                mention(
                    "plant",
                    "plant network",
                    ["network-zone"],
                    "plant network are kept apart",
                    NOTE,
                ),
                mention(
                    "lic",
                    "licence server",
                    ["process"],
                    "A licence server checks keys",
                    NOTE,
                ),
                mention(
                    "press",
                    "press controller",
                    ["process"],
                    "The press controller sits on the plant network",
                    NOTE,
                ),
            ],
            interactions=[
                interaction(
                    "chk",
                    "press",
                    "lic",
                    "check key",
                    "checks keys over TLS for the press controller",
                    NOTE,
                    protocol="TLS",
                    operations="read",
                ),
            ],
            facts=[
                fact(
                    "p1",
                    "mention",
                    "press",
                    "network-membership",
                    "plant",
                    "The press controller sits on the plant network",
                    NOTE,
                ),
                fact(
                    "t1",
                    "interaction",
                    "chk",
                    "transport-encryption",
                    "TLS",
                    "checks keys over TLS",
                    NOTE,
                ),
                fact(
                    "u1",
                    "mention",
                    "lic",
                    "network-membership",
                    UNKNOWN,
                    "nobody wrote down which network the licence server sits on",
                    NOTE,
                    reason="silent",
                ),
            ],
        ),
        wants=(
            Want(
                "the check-key interaction",
                "transport-encryption",
                "TLS",
                "encryption_in_transit",
                reader="stride _unprotected_transit_crossing; asvs transport test",
            ),
        ),
        direct=SystemModel(
            processes=[
                Process(
                    id="process:licence-server",
                    name="licence server",
                    technology=UNKNOWN,
                    trust_zone="boundary:office-network",
                    exposure="unknown",
                    interface_kind="unknown",
                ),
                Process(
                    id="process:press-controller",
                    name="press controller",
                    technology=UNKNOWN,
                    trust_zone="boundary:plant-network",
                    exposure="unknown",
                    interface_kind="unknown",
                ),
            ],
            data_flows=[
                DataFlow(
                    id="flow:process:press-controller>process:licence-server>check-key",
                    name="check key",
                    source="process:press-controller",
                    destination="process:licence-server",
                    protocol="TLS",
                    authentication=UNKNOWN,
                    data_description=UNKNOWN,
                    encryption_in_transit="TLS",
                    operations="read",
                ),
            ],
            trust_boundaries=[
                TrustBoundary(
                    id="boundary:office-network", name="office network", kind="network"
                ),
                TrustBoundary(
                    id="boundary:plant-network", name="plant network", kind="network"
                ),
            ],
            assumptions=[
                Assumption(
                    assumption="the licence server sits in the office network",
                    element_id="process:licence-server",
                    attribute="trust_zone",
                    basis="no source places this component",
                ),
            ],
        ),
        note="the direct construction holds the fact by inventing a zone and marking it",
    )


def _conflicting_placement() -> Fixture:
    """Two sources, two zones, one component."""
    runbook = (
        "The card ledger sits in the payments network.\n"
        "The payments network and the reporting network are separate estates."
    )
    interview = (
        "The card ledger has always sat in the reporting network, and it is"
        " encrypted at rest with a customer-managed key."
    )
    return Fixture(
        key="conflicting-placement",
        asks="no invented certainty, and the supported non-placement fact survives",
        sources={NOTE: runbook, INTERVIEW: interview},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "pay",
                    "payments network",
                    ["network-zone"],
                    "The card ledger sits in the payments network",
                    NOTE,
                ),
                mention(
                    "rep",
                    "reporting network",
                    ["network-zone"],
                    "the reporting network are separate estates",
                    NOTE,
                ),
                mention(
                    "led", "card ledger", ["store"], "The card ledger sits in", NOTE
                ),
            ],
            facts=[
                fact(
                    "z1",
                    "mention",
                    "led",
                    "network-membership",
                    "pay",
                    "The card ledger sits in the payments network",
                    NOTE,
                ),
                fact(
                    "z2",
                    "mention",
                    "led",
                    "network-membership",
                    "rep",
                    "has always sat in the reporting network",
                    INTERVIEW,
                ),
                fact(
                    "e1",
                    "mention",
                    "led",
                    "storage-encryption",
                    "customer-managed key",
                    "encrypted at rest with a customer-managed key",
                    INTERVIEW,
                ),
            ],
        ),
        wants=(
            Want(
                "the card ledger",
                "storage-encryption",
                "customer-managed key",
                "encryption_at_rest",
                reader="stride _store_at_rest_unverified",
            ),
        ),
        direct=SystemModel(
            data_stores=[
                DataStore(
                    id="store:card-ledger",
                    name="card ledger",
                    technology=UNKNOWN,
                    trust_zone="boundary:payments-network",
                    data_classification=UNKNOWN,
                    encryption_at_rest="customer-managed key",
                    notes="two sources place this in different zones",
                ),
            ],
            trust_boundaries=[
                TrustBoundary(
                    id="boundary:payments-network",
                    name="payments network",
                    kind="network",
                ),
                TrustBoundary(
                    id="boundary:reporting-network",
                    name="reporting network",
                    kind="network",
                ),
            ],
            assumptions=[
                Assumption(
                    assumption="the card ledger sits in the payments network",
                    element_id="store:card-ledger",
                    attribute="trust_zone",
                    basis="two sources place it in different zones",
                ),
            ],
        ),
        note="the direct construction keeps the encryption fact by picking a zone,"
        " and the graph carries no record that the two sources disagreed",
    )


def _store_and_process() -> Fixture:
    """One named thing that both holds data and runs code."""
    text = (
        "The quarterly workbook holds every customer's address, and it runs a"
        " macro that emails the sheet out each Friday.\n"
        "It lives on the finance laptop."
    )
    return Fixture(
        key="store-and-process",
        asks="storage and execution behaviour stay expressible and distinguishable",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "lap",
                    "finance laptop",
                    ["network-zone"],
                    "It lives on the finance laptop",
                    NOTE,
                ),
                mention(
                    "wb",
                    "quarterly workbook",
                    ["store", "process"],
                    "The quarterly workbook holds every customer's address",
                    NOTE,
                ),
            ],
            facts=[
                fact(
                    "d1",
                    "mention",
                    "wb",
                    "data-classification",
                    "customer addresses",
                    "holds every customer's address",
                    NOTE,
                ),
            ],
        ),
        wants=(
            Want(
                "the workbook as a store",
                "data-classification",
                "customer addresses",
                "data_classification",
                reader="asvs CLASSIFIED_STORE_TEST",
            ),
        ),
        direct=SystemModel(
            processes=[
                Process(
                    id="process:quarterly-workbook-macro",
                    name="quarterly workbook macro",
                    technology="spreadsheet macro",
                    trust_zone="boundary:finance-laptop",
                    exposure="unknown",
                    interface_kind="non-web",
                ),
            ],
            data_stores=[
                DataStore(
                    id="store:quarterly-workbook",
                    name="quarterly workbook",
                    technology="spreadsheet",
                    trust_zone="boundary:finance-laptop",
                    data_classification="customer addresses",
                    encryption_at_rest=UNKNOWN,
                ),
            ],
            trust_boundaries=[
                TrustBoundary(
                    id="boundary:finance-laptop", name="finance laptop", kind="network"
                ),
            ],
        ),
        note="the schema holds one named thing as two elements; a bundle mention"
        " carries one role and the resolver will not choose between two",
    )


def _distinct_subjects() -> Fixture:
    """A workload account, the person who owns it, and the tool they run."""
    text = (
        "The nightly export runs under a service account called batch-runner.\n"
        "Priya owns that account and signs in as herself to approve each run.\n"
        "The warehouse sits in the analytics network."
    )
    return Fixture(
        key="distinct-subjects",
        asks="distinct subjects are not merged for a convenient binding",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "an",
                    "analytics network",
                    ["network-zone"],
                    "The warehouse sits in the analytics network",
                    NOTE,
                ),
                mention("wh", "warehouse", ["store"], "The warehouse sits in", NOTE),
            ],
            facts=[
                fact(
                    "g1",
                    "principal",
                    "batch-runner",
                    "authorization-grant",
                    "write",
                    "The nightly export runs under a service account called batch-runner",
                    NOTE,
                    scope=[
                        Qualifier(kind="resource", value="warehouse"),
                        Qualifier(kind="operation", value="write"),
                    ],
                ),
                fact(
                    "m1",
                    "principal",
                    "priya",
                    "mfa-requirement",
                    "required",
                    "signs in as herself to approve each run",
                    NOTE,
                ),
                fact(
                    "s1",
                    "credential",
                    "batch-runner key",
                    "credential-sharing",
                    "shared",
                    "runs under a service account called batch-runner",
                    NOTE,
                ),
            ],
        ),
        wants=(
            Want(
                "the batch-runner account",
                "authorization-grant",
                "write",
                reader="evidence.evidence_catalog, under ANALYSIS_ASSERTIONS",
            ),
            Want(
                "the person who owns it",
                "mfa-requirement",
                "required",
                reader="evidence.evidence_catalog, under ANALYSIS_ASSERTIONS",
            ),
        ),
        note="the assertion layer's own subject types keep the account and the"
        " person apart; the System Model has no node for either",
    )


def _same_name() -> Fixture:
    """Two distinct things the sources call by one name."""
    text = (
        "The Seattle office runs a scheduler, and it is reachable from the"
        " internet.\n"
        "The Dublin office runs a scheduler too, and that one is not reachable"
        " from outside.\n"
        "Both offices sit in the corporate network."
    )
    return Fixture(
        key="same-name",
        asks="identity does not collapse two distinct referents",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "corp",
                    "corporate network",
                    ["network-zone"],
                    "Both offices sit in the corporate network",
                    NOTE,
                ),
                mention(
                    "s1",
                    "scheduler",
                    ["process"],
                    "The Seattle office runs a scheduler",
                    NOTE,
                ),
                mention(
                    "s2",
                    "scheduler",
                    ["process"],
                    "The Dublin office runs a scheduler too",
                    NOTE,
                ),
            ],
            facts=[
                fact(
                    "x1",
                    "mention",
                    "s1",
                    "internet-exposure",
                    "internet-facing",
                    "it is reachable from the internet",
                    NOTE,
                ),
                fact(
                    "x2",
                    "mention",
                    "s2",
                    "internet-exposure",
                    "internal",
                    "that one is not reachable from outside",
                    NOTE,
                ),
            ],
        ),
        wants=(
            Want(
                "the Seattle scheduler",
                "internet-exposure",
                "internet-facing",
                "exposure",
                reader="stride _internet_exposed_process",
            ),
            Want(
                "the Dublin scheduler",
                "internet-exposure",
                "internal",
                "exposure",
                reader="stride _internet_exposed_process",
            ),
        ),
        direct=SystemModel(
            processes=[
                Process(
                    id="process:seattle-scheduler",
                    name="scheduler",
                    technology=UNKNOWN,
                    trust_zone="boundary:corporate-network",
                    exposure="internet-facing",
                    interface_kind="unknown",
                    description="the Seattle office's scheduler",
                ),
                Process(
                    id="process:dublin-scheduler",
                    name="scheduler",
                    technology=UNKNOWN,
                    trust_zone="boundary:corporate-network",
                    exposure="internal",
                    interface_kind="unknown",
                    description="the Dublin office's scheduler",
                ),
            ],
            trust_boundaries=[
                TrustBoundary(
                    id="boundary:corporate-network",
                    name="corporate network",
                    kind="network",
                ),
            ],
        ),
        note="the schema holds two elements under one name; an element ID the"
        " adapter derives from the name alone holds one",
    )


def _parallel_interfaces() -> Fixture:
    """Two interactions between one pair of endpoints."""
    text = (
        "The portal calls the ledger to read balances over HTTPS.\n"
        "The portal also calls the ledger to post entries, and that call is"
        " unauthenticated.\n"
        "Both sit in the core network."
    )
    return Fixture(
        key="parallel-interfaces",
        asks="two interfaces between one pair take distinct bindings",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "core",
                    "core network",
                    ["network-zone"],
                    "Both sit in the core network",
                    NOTE,
                ),
                mention(
                    "p",
                    "portal",
                    ["process"],
                    "The portal calls the ledger to read",
                    NOTE,
                ),
                mention("l", "ledger", ["store"], "the ledger to post entries", NOTE),
            ],
            interactions=[
                interaction(
                    "i1",
                    "p",
                    "l",
                    "read balances",
                    "read balances over HTTPS",
                    NOTE,
                    protocol="HTTPS",
                    operations="read",
                ),
                interaction(
                    "i2",
                    "p",
                    "l",
                    "post entries",
                    "post entries, and that call is unauthenticated",
                    NOTE,
                    operations="write",
                ),
            ],
            facts=[
                fact(
                    "t1",
                    "interaction",
                    "i1",
                    "transport-encryption",
                    "HTTPS",
                    "read balances over HTTPS",
                    NOTE,
                ),
                fact(
                    "a1",
                    "interaction",
                    "i2",
                    "authentication-mechanism",
                    "absent",
                    "that call is unauthenticated",
                    NOTE,
                ),
            ],
        ),
        wants=(
            Want(
                "the read-balances interface",
                "transport-encryption",
                "HTTPS",
                "encryption_in_transit",
                reader="stride _unprotected_transit_crossing",
            ),
            Want(
                "the post-entries interface",
                "authentication-mechanism",
                "absent",
                "authentication",
                carried="none",
                reader="stride _unverified_write_to_store",
            ),
        ),
        note="the flow identity carries the label, so the two stay separate;"
        " #1015 is the scoring half of the same fact",
    )


def _scope_and_polarity() -> Fixture:
    """A scoped grant, a stated absence, and a silence that is not an absence."""
    text = (
        "Support staff may read any order, and they may not refund one.\n"
        "There is no second factor for support staff.\n"
        "The order desk sits in the corporate network."
    )
    return Fixture(
        key="scope-and-polarity",
        asks="scope and polarity survive, and silence is not read as absence",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "corp",
                    "corporate network",
                    ["network-zone"],
                    "The order desk sits in the corporate network",
                    NOTE,
                ),
                mention(
                    "desk", "order desk", ["process"], "The order desk sits in", NOTE
                ),
            ],
            facts=[
                fact(
                    "g1",
                    "principal",
                    "support staff",
                    "authorization-grant",
                    "read",
                    "Support staff may read any order",
                    NOTE,
                    scope=[
                        Qualifier(kind="resource", value="order"),
                        Qualifier(kind="operation", value="read"),
                    ],
                ),
                fact(
                    "g2",
                    "principal",
                    "support staff",
                    "authorization-grant",
                    "absent",
                    "they may not refund one",
                    NOTE,
                    scope=[
                        Qualifier(kind="resource", value="order"),
                        Qualifier(kind="operation", value="refund"),
                    ],
                ),
                fact(
                    "m1",
                    "principal",
                    "support staff",
                    "mfa-requirement",
                    "absent",
                    "There is no second factor for support staff",
                    NOTE,
                ),
                fact(
                    "c1",
                    "mention",
                    "desk",
                    "storage-encryption",
                    UNKNOWN,
                    "The order desk sits in the corporate network",
                    NOTE,
                    reason="silent",
                ),
            ],
        ),
        wants=(
            Want(
                "support staff, refunding an order",
                "authorization-grant",
                "absent",
                reader="evidence.evidence_catalog, under ANALYSIS_ASSERTIONS",
            ),
            Want(
                "support staff, signing in",
                "mfa-requirement",
                "absent",
                reader="evidence.evidence_catalog, under ANALYSIS_ASSERTIONS",
            ),
        ),
        note="a scoped grant and a stated absence are rows the graph has no"
        " field for, so neither reaches a framework rule",
    )


def _hedge_and_conflict() -> Fixture:
    """A speaker who hedged, and two sources that disagree."""
    runbook = (
        "The collector ships rows to the archive over plain HTTP.\n"
        "The core network holds both of them."
    )
    interview = "That link is encrypted end to end, as far as anyone remembers."
    return Fixture(
        key="hedge-and-conflict",
        asks="uncertainty and conflict are retained rather than settled without evidence",
        sources={NOTE: runbook, INTERVIEW: interview},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "core",
                    "core network",
                    ["network-zone"],
                    "The core network holds both of them",
                    NOTE,
                ),
                mention(
                    "c", "collector", ["process"], "The collector ships rows", NOTE
                ),
                mention(
                    "a", "archive", ["store"], "to the archive over plain HTTP", NOTE
                ),
            ],
            interactions=[
                interaction(
                    "i",
                    "c",
                    "a",
                    "ship rows",
                    "ships rows to the archive over plain HTTP",
                    NOTE,
                    operations="write",
                ),
            ],
            facts=[
                fact(
                    "x1",
                    "interaction",
                    "i",
                    "transport-encryption",
                    "absent",
                    "over plain HTTP",
                    NOTE,
                ),
                fact(
                    "x2",
                    "interaction",
                    "i",
                    "transport-encryption",
                    "encrypted end to end",
                    "That link is encrypted end to end",
                    INTERVIEW,
                ),
            ],
        ),
        wants=(
            Want(
                "the ship-rows interaction",
                "transport-encryption",
                "absent",
                "encryption_in_transit",
                carried="none",
                reader="stride _unprotected_transit_crossing",
            ),
        ),
        note="both readings are kept and conflicts() derives the disagreement;"
        " the projection then writes unknown rather than picking",
    )


def _operations_and_classification() -> Fixture:
    """A read-only path, and a store whose classification the sources state."""
    text = (
        "The report builder reads the customer table and never writes to it.\n"
        "The customer table holds confidential personal data.\n"
        "Both sit in the analytics network."
    )
    return Fixture(
        key="operations-and-classification",
        asks="structured meaning survives into its actual consumer",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "an",
                    "analytics network",
                    ["network-zone"],
                    "Both sit in the analytics network",
                    NOTE,
                ),
                mention(
                    "rb",
                    "report builder",
                    ["process"],
                    "The report builder reads",
                    NOTE,
                ),
                mention(
                    "ct",
                    "customer table",
                    ["store"],
                    "The customer table holds confidential personal data",
                    NOTE,
                ),
            ],
            interactions=[
                interaction(
                    "i",
                    "rb",
                    "ct",
                    "read customers",
                    "reads the customer table and never writes to it",
                    NOTE,
                    operations="read",
                ),
            ],
            facts=[
                fact(
                    "d1",
                    "mention",
                    "ct",
                    "data-classification",
                    "confidential personal data",
                    "holds confidential personal data",
                    NOTE,
                ),
            ],
        ),
        wants=(
            Want(
                "the customer table",
                "data-classification",
                "confidential personal data",
                "data_classification",
                reader="asvs CLASSIFIED_STORE_TEST",
            ),
        ),
        note="operations rides on the interaction row rather than on a fact row,"
        " and stride's store-tampering rule reads it off the flow",
    )


def _failed_correction() -> Fixture:
    """A review that retracts a supported row and offers a replacement nothing can take."""
    text = (
        "The gateway forwards requests to the billing service over mutual TLS.\n"
        "Both run in the core network."
    )
    return Fixture(
        key="failed-correction",
        asks="the original supported facts survive a correction that fails",
        sources={NOTE: text},
        bundle=SourceFactBundle(
            mentions=[
                mention(
                    "core",
                    "core network",
                    ["network-zone"],
                    "Both run in the core network",
                    NOTE,
                ),
                mention(
                    "gw", "gateway", ["process"], "The gateway forwards requests", NOTE
                ),
                mention(
                    "bill",
                    "billing service",
                    ["process"],
                    "to the billing service over mutual TLS",
                    NOTE,
                ),
            ],
            interactions=[
                interaction(
                    "f",
                    "gw",
                    "bill",
                    "forward request",
                    "forwards requests to the billing service over mutual TLS",
                    NOTE,
                    protocol="TLS",
                    operations="write",
                ),
            ],
            facts=[
                fact(
                    "t1",
                    "interaction",
                    "f",
                    "transport-encryption",
                    "mutual TLS",
                    "over mutual TLS",
                    NOTE,
                ),
            ],
        ),
        wants=(
            Want(
                "the forward-request interaction",
                "transport-encryption",
                "mutual TLS",
                "encryption_in_transit",
                reader="stride _unprotected_transit_crossing",
            ),
        ),
        patch=PatchBatch(
            operations=[
                Operation(
                    handle="drop",
                    kind="retract-assertion",
                    reason="the review read the transport as one-way TLS",
                    retract="assertion:transport-encryption~flow:process:gateway"
                    ">process:billing-service>forward-request~any~v.0000000000ff",
                ),
                Operation(
                    handle="put",
                    kind="add-assertion",
                    reason="the replacement the review offers",
                    assertion=fact(
                        "put",
                        "interaction",
                        "nowhere",
                        "transport-encryption",
                        "one-way TLS",
                        "over mutual TLS",
                        NOTE,
                    ),
                ),
            ]
        ),
        note="the replacement names an interaction handle no bundle row builds,"
        " so the batch is refused whole and the retraction never applies",
    )


#: The ten shapes #1033's fixture table names, each built by the function above
#: it. A table rather than a list of calls, so a fixture added tomorrow is one
#: row and ``tests/test_evals_bottleneck.py`` finds it without being told.
FIXTURES: tuple[Fixture, ...] = (
    _unknown_placement(),
    _conflicting_placement(),
    _store_and_process(),
    _distinct_subjects(),
    _same_name(),
    _parallel_interfaces(),
    _scope_and_polarity(),
    _hedge_and_conflict(),
    _operations_and_classification(),
    _failed_correction(),
)


# --- Reading each fixture three ways -----------------------------------------


def _structural(model: SystemModel, wants: Sequence[Want]) -> list[bool]:
    """Which wants a graph attribute a framework rule reads carries.

    One ``(element, attribute)`` answers one want, so two wants about two
    things the sources name alike cannot both be answered by the one element
    an ID collision leaves standing.
    """
    taken: set[tuple[str, str]] = set()
    held = []
    for want in wants:
        carrier = ""
        if want.attribute:
            carrier = next(
                (
                    element.id
                    for element in model.elements()
                    if (element.id, want.attribute) not in taken
                    and str(getattr(element, want.attribute, "")) == want.spelled
                ),
                "",
            )
        if carrier:
            taken.add((carrier, want.attribute))
        held.append(bool(carrier))
    return held


def _in_catalog(catalog: AssertionCatalog, wants: Sequence[Want]) -> list[bool]:
    """Which wants a catalog row carries, one row answering one want."""
    taken: set[int] = set()
    held = []
    for want in wants:
        found = next(
            (
                index
                for index, entry in enumerate(catalog.entries)
                if index not in taken
                and entry.predicate == want.predicate
                and entry.value == want.value
            ),
            None,
        )
        if found is not None:
            taken.add(found)
        held.append(found is not None)
    return held


def _open_question(resolution: Resolution) -> bool:
    """Whether the sidecar carries an open question at all.

    Read off the disposition rows rather than off a second record, which is
    what :attr:`~analysis_service.factbundle.Resolution.gaps` is for. A row
    whose subject the graph could not place is such a question, and a reader
    counting what the route lost finds it there and nowhere else.
    """
    return any(row.disposition == "unresolved" for row in resolution.gaps)


def survivals(
    resolution: Resolution, projected: SystemModel, wants: Sequence[Want]
) -> tuple[Survival, ...]:
    """Where each wanted fact ended up, in the order a reader should prefer."""
    structural = _structural(projected, wants)
    catalogued = _in_catalog(resolution.record.catalog, wants)
    question = _open_question(resolution)
    found: list[Survival] = []
    for reached, kept in zip(structural, catalogued, strict=True):
        if reached:
            found.append("structural")
        elif kept:
            found.append("catalog")
        else:
            found.append("question" if question else "lost")
    return tuple(found)


def _direct_reading(fixture: Fixture) -> tuple[bool, tuple[str, ...]]:
    """Whether the hand-written construction stands, and what the gate says of it.

    A fixture with no construction of its own is answered by the adapter's, so
    it holds by construction: writing a second one would only restate what the
    resolver already built.
    """
    if fixture.direct is None:
        return True, ()
    issues = validate(fixture.direct)
    wanted = [want for want in fixture.wants if want.attribute]
    holds = not issues and all(_structural(fixture.direct, wanted))
    return holds, tuple(f"{issue.code}: {issue.message}" for issue in issues)


def _cause(reached: Sequence[Survival], direct_holds: bool) -> Cause:
    """What a fixture's loss is a property of, by the three readings.

    The order is the order of the corrections: a fact the schema cannot hold is
    not an adapter defect, and a fact both keep and nothing reads is neither.
    """
    if all(one == "structural" for one in reached):
        return "none"
    if not direct_holds:
        return "schema"
    if any(one in ("question", "lost") for one in reached):
        return "adapter"
    return "consumer"


def diagnose(fixture: Fixture) -> Diagnosis:
    """One fixture, through the real resolver, the real gate and the projection."""
    resolution = resolve_bundle(fixture.bundle, fixture.sources)
    record = resolution.record
    model = resolution.model
    rolled_back = None
    if fixture.patch is not None:
        result = apply_patch(fixture.patch, model, record, fixture.sources)
        model, record, rolled_back = result.model, result.record, result.rolled_back
        resolution = Resolution(
            model, resolution.proposal, record, resolution.dispositions
        )
    projected, _ = apply_projection(model, record.catalog)
    reached = survivals(resolution, projected, fixture.wants)
    direct_holds, direct_issues = _direct_reading(fixture)
    return Diagnosis(
        key=fixture.key,
        asks=fixture.asks,
        survivals={
            want.label: one for want, one in zip(fixture.wants, reached, strict=True)
        },
        lost_rows=tuple((row.kind, row.handle, row.code) for row in resolution.gaps),
        direct_holds=direct_holds,
        direct_issues=direct_issues,
        cause=_cause(reached, direct_holds),
        conflicts=len(conflicts(record.catalog)),
        rolled_back=rolled_back,
        note=fixture.note,
    )


# --- What the archived emissions actually lost -------------------------------
#
# The fixtures above say which shapes the code can lose. They cannot say how
# often a real reading meets one, because their facts are authored. This half
# reads the emissions #1003's arm sweep already paid for and charges every
# reference row the run missed to the earliest stage that did not carry it.

#: Where one missed reference row stopped, earliest first.
#:
#: * ``unread``: no proposed row names the predicate at all. Nothing
#:   downstream ever had the fact.
#: * ``unmodelled``: the predicate was proposed, and the run's own graph holds
#:   no element paired with the reference's subject. The fact had nowhere to
#:   land, which is the cost the System Model's identity and placement rules
#:   impose.
#: * ``renamed``: the predicate was proposed about a produced element the
#:   alignment pairs with the reference's subject. The reading found the right
#:   thing and spelled its name otherwise, and an **Element ID** is a function
#:   of the name.
#: * ``misattached``: the predicate was proposed, the graph held the subject,
#:   and the row names an element that is not it. The reading put the fact in
#:   the wrong place.
#: * ``refused``: a proposed row on the right subject that the resolver or the
#:   gate dropped.
#: * ``scored``: a row that reached the catalog and that the matcher
#:   adjudicated as something other than found.
#:
#: **The split that decides #1033's question is ``unmodelled`` and ``refused``
#: against the rest.** Those two are losses the representation and the code
#: took after the model had the fact. ``unread`` and ``misattached`` are the
#: reading, which no schema change recovers.
MissStage = Literal[
    "unread", "unmodelled", "renamed", "misattached", "refused", "scored"
]
MISS_STAGES: tuple[MissStage, ...] = get_args(MissStage)

#: The fates that say the reference row reached a produced row the matcher
#: adjudicated. Read off :data:`~evals.harness.replay.ADJUDICATED_WRONG` plus
#: the fate that answers a row in other words, so a fate added to the matcher
#: is classified there rather than listed again here.
ADJUDICATED_FATES: frozenset[str] = ADJUDICATED_WRONG | {"worded"}


@dataclass(frozen=True)
class MissCharge:
    """One required reference row a run missed, and where it stopped."""

    case_id: str
    arm: str
    repeat: int
    row: str
    predicate: str
    fate: str
    stage: MissStage
    why: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "arm": self.arm,
            "repeat": self.repeat,
            "row": self.row,
            "predicate": self.predicate,
            "fate": self.fate,
            "stage": self.stage,
            "why": self.why,
        }


def _proposed(result: AssertionResult) -> tuple[Mapping[str, Any], ...]:
    """The rows the node emitted, whatever route wrote them.

    A graph-first head emits assertion rows straight into its proposal; a
    facts-first head emits a bundle and code composes a proposal from it. The
    two are read as one list of ``(subject, predicate)`` claims, because the
    question here is what the *model* said, and both routes say it in those
    two fields.
    """
    return tuple(result.proposal.get("assertions", ()))


def _refusal(result: AssertionResult, index: int) -> str:
    """The first reason the gate or the resolver gave for dropping one row."""
    return next(
        (issue.code for issue in result.issues if issue.row == index),
        "",
    )


def charge_row(
    reference: SignedReference,
    result: AssertionResult,
    fate: ReferenceRowFate,
    alignment: Alignment,
) -> tuple[MissStage, str]:
    """Which stage did not carry one missed reference row.

    The questions run in the order the stages do, and the first ``yes``
    answers. A row's subject is compared under the reference's signed aliases,
    so a subject a reviewer already ruled to be the same one counts as the same
    one here too; ``alignment`` then answers the question the aliases do not,
    which is whether the run's own graph held the thing at all.
    """
    if fate.fate in ADJUDICATED_FATES:
        return "scored", fate.fate
    wanted = next(
        entry for entry in reference.entries if assertion_id(entry) == fate.reference
    )
    named = [
        (index, row)
        for index, row in enumerate(_proposed(result))
        if row.get("predicate") == wanted.predicate
    ]
    if not named:
        return "unread", "no proposed row names the predicate"
    here = [
        index
        for index, row in named
        if _aliased_subject(str(row.get("subject", "")), reference) == wanted.subject
    ]
    if here:
        refused = next((_refusal(result, index) for index in here), "")
        if refused:
            return "refused", refused
        return "misattached", "the row resolved onto another subject"
    paired = alignment.reference_of
    if any(
        paired.get(_aliased_subject(str(row.get("subject", "")), reference))
        == wanted.subject
        for _, row in named
    ):
        return "renamed", "the subject is the right element under another name"
    if _modelled(wanted.subject, alignment):
        return "misattached", "the predicate was proposed about another subject"
    return "unmodelled", "the run's graph holds no element for the subject"


def _modelled(subject: str, alignment: Alignment) -> bool:
    """Whether the run's own graph held an element standing for this subject.

    A subject of the assertion layer's own — a principal, a credential, an
    artifact — is never an element, so no graph decides it and the answer is
    yes: a row about one had somewhere to go whatever the extraction built.
    """
    if not any(subject.startswith(prefix) for prefix in _GRAPH_PREFIXES):
        return True
    return subject in alignment.aligned_reference


def _aliased_subject(subject: str, reference: SignedReference) -> str:
    """One produced subject under the reference's signed aliases.

    The subject half of :func:`~evals.harness.replay.under_aliases`, which
    reads a whole :class:`~analysis_service.assertions.Assertion`. A proposal
    row is not one yet — its spans carry no offsets and its subject may name a
    principal by the words the model used — so the same table is read for the
    one field this charge compares.
    """
    ruled = reference.subject_aliases.get(subject)
    return subject if ruled is None else ruled.to


def charge_misses(
    specs: Sequence[tuple[str, int, Path]],
    cases: Sequence[GoldenCase],
    corpus_dir: Path,
) -> tuple[tuple[MissCharge, ...], dict[str, str]]:
    """Every required row these archived sweeps missed, charged to a stage.

    Nothing is graded here: :func:`~evals.harness.replay.replay_assertions`
    decides which reference rows a run answered, under the aliases a reviewer
    signed, and this reads its fates. A case nobody signed is skipped and
    named, because a run charged against a draft is charged against nothing.
    """
    charged: list[MissCharge] = []
    skipped: dict[str, str] = {}
    for arm, repeat, path in specs:
        loaded = load_artifact(path)
        held = [case for case in cases if case.id in loaded.cases]
        produced = heads_from_reports(path, held)
        for case in held:
            reference = signed_reference(corpus_dir, case)
            if reference is None:
                skipped[case.id] = "no signed reference, so nothing grades it"
                continue
            result = produced.get(case.id)
            if result is None:
                skipped[f"{arm}/{case.id}"] = "the sweep archived no catalog"
                continue
            required = set(required_rows(reference))
            graded = replay_assertions(case, reference, result)
            alignment = align(case, _graph_of(result))
            for fate in graded.rows:
                if fate.reference not in required or fate.fate == "found":
                    continue
                stage, why = charge_row(reference, result, fate, alignment)
                charged.append(
                    MissCharge(
                        case_id=case.id,
                        arm=arm,
                        repeat=repeat,
                        row=fate.reference,
                        predicate=_predicate_of(reference, fate.reference),
                        fate=fate.fate,
                        stage=stage,
                        why=why,
                    )
                )
    return tuple(charged), skipped


#: The **Element ID** prefixes a graph subject carries. Read off the schema's
#: own table, so a sixth element class is covered the day it lands.
_GRAPH_PREFIXES: tuple[str, ...] = tuple(
    f"{prefix}:"
    for prefixes in SUBJECT_PREFIXES.values()
    for prefix in prefixes
    if prefix in {element.id_prefix for element in get_args(Element)}
)


def _graph_of(result: AssertionResult) -> SystemModel | None:
    """The graph the run itself built, off the stages it archived.

    The **valid** model rather than the extracted one: it is the graph the
    assertion rows were resolved against and the one every consumer of that job
    read. A sweep that archived no stages answers ``None``, and every row of it
    is then charged to the reading rather than to a graph nobody kept.
    """
    held = result.stages.get("valid_model")
    return None if held is None else SystemModel.model_validate(held)


def _predicate_of(reference: SignedReference, row: str) -> str:
    """One reference row's predicate, by the identity the fate names."""
    return next(
        entry.predicate for entry in reference.entries if assertion_id(entry) == row
    )


def pooled(charges: Sequence[MissCharge]) -> Mapping[str, int]:
    """How many missed rows each stage holds, over whatever was charged."""
    counted = Counter(charge.stage for charge in charges)
    return {stage: counted[stage] for stage in MISS_STAGES}


# --- Rendering ---------------------------------------------------------------


def render_fixtures(found: Sequence[Diagnosis]) -> str:
    """The fixture table, in the shape the research record carries it."""
    lines = [
        "| fixture | asks | survival | schema holds | cause |",
        "| --- | --- | --- | --- | --- |",
    ]
    for one in found:
        reached = ", ".join(sorted(set(one.survivals.values())))
        lines.append(
            f"| `{one.key}` | {one.asks} | {reached} |"
            f" {'yes' if one.direct_holds else 'no'} | {one.cause} |"
        )
    return "\n".join(lines)


def render_readers(fixtures: Sequence[Fixture] = FIXTURES) -> str:
    """Which deterministic consumer reads each wanted fact.

    The parity half of the table: a want with a graph attribute names the rule
    that reads it, and one without names the seam that offers it. Printed
    rather than left in the source, because "does anything read this" is the
    third of the three readings and a table nobody prints answers it nowhere.
    """
    lines = ["| fixture | fact | attribute | reader |", "| --- | --- | --- | --- |"]
    for fixture in fixtures:
        for want in fixture.wants:
            lines.append(
                f"| `{fixture.key}` | {want.label} |"
                f" {want.attribute or '—'} | {want.reader} |"
            )
    return "\n".join(lines)


def render_misses(charges: Sequence[MissCharge], arms: Sequence[str]) -> str:
    """Every arm's missed rows by the stage that did not carry them."""
    lines = [
        "| arm | " + " | ".join(MISS_STAGES) + " | total |",
        "| --- |" + " --- |" * (len(MISS_STAGES) + 1),
    ]
    for arm in arms:
        ours = [charge for charge in charges if charge.arm == arm]
        counted = pooled(ours)
        lines.append(
            f"| {arm} | "
            + " | ".join(str(counted[stage]) for stage in MISS_STAGES)
            + f" | {len(ours)} |"
        )
    counted = pooled(charges)
    lines.append(
        "| all | "
        + " | ".join(str(counted[stage]) for stage in MISS_STAGES)
        + f" | {len(charges)} |"
    )
    return "\n".join(lines)


def arguments(parser: argparse.ArgumentParser) -> None:
    """The sweeps to charge, the corpus that grades them, and where output goes."""
    parser.add_argument(
        "artifact",
        nargs="*",
        metavar=SPEC,
        help="each arm's archived head-only sweep, with its .reports/ beside it;"
        " with none, only the offline fixtures run",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("evals") / "corpus",
        help="corpus root: the blessed models and the signed reference facts",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the fixtures and the charges to this JSON file",
    )


def command_bottleneck(args: argparse.Namespace) -> int:
    """Run the fixtures, and charge any archived sweep named beside them."""
    found = [diagnose(fixture) for fixture in FIXTURES]
    print(render_fixtures(found))
    print()
    print(render_readers())
    for one in found:
        if one.cause != "none":
            print(f"\n== {one.key}: {one.cause}")
            print(f"   {one.note}")
            for kind, handle, code in one.lost_rows:
                print(f"   lost {kind} {handle}: {code}")
            for issue in one.direct_issues:
                print(f"   the gate refuses the hand-written model: {issue}")

    charges: tuple[MissCharge, ...] = ()
    relaxations: tuple[Relaxation, ...] = ()
    arms: list[str] = []
    if args.artifact:
        specs = [parse_spec(spec) for spec in args.artifact]
        arms = sorted({arm for arm, _, _ in specs})
        cases = load_corpus(args.corpus)
        charges, skipped = charge_misses(specs, cases, args.corpus)
        print("\n" + render_misses(charges, arms))
        relaxations = relax(specs, cases, args.corpus)
        print("\nOne relaxed constraint: a name the alignment pairs is one subject.")
        print(render_relaxations(relaxations))
        for case_id, why in sorted(skipped.items()):
            print(f"  skipped {case_id}: {why}")
    if args.out is not None:
        args.out.write_text(
            json.dumps(
                {
                    "fixtures": [one.to_json() for one in found],
                    "charges": [charge.to_json() for charge in charges],
                    "pooled": pooled(charges),
                    "relaxed": [one.to_json() for one in relaxations],
                },
                indent=2,
            ),
            "utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


# --- One narrowly relaxed constraint, over the same emissions ----------------


@dataclass(frozen=True)
class Relaxation:
    """One arm's rows before and after the constraint is relaxed.

    ``found`` and ``wrong`` are counted over the required rows alone, so a
    recovery and a new error are read on one denominator. ``wrong`` is every
    required row a produced row answered and disagreed with — the errors the
    relaxation introduces show up here, because a pairing that binds a row to
    the wrong element turns an omission into a disagreement rather than into an
    answer.
    """

    arm: str
    required: int
    found: int
    relaxed_found: int
    wrong: int
    relaxed_wrong: int

    @property
    def recovered(self) -> int:
        return self.relaxed_found - self.found

    @property
    def introduced(self) -> int:
        return self.relaxed_wrong - self.wrong

    def to_json(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "required": self.required,
            "found": self.found,
            "relaxed_found": self.relaxed_found,
            "wrong": self.wrong,
            "relaxed_wrong": self.relaxed_wrong,
            "recovered": self.recovered,
            "introduced": self.introduced,
        }


def relaxed_reference(
    reference: SignedReference, alignment: Alignment
) -> SignedReference:
    """The signed reference with every alignment pair added as a subject alias.

    **The one relaxed constraint #1033 asks for, and it is the narrowest one
    available.** A ``renamed`` miss is a row about the element the alignment
    pairs with the reference's subject, refused because an **Element ID** is a
    function of the name. Adding the pairs as aliases asks what the run would
    have scored if a name spelled otherwise were the same subject, and nothing
    else moves: the same emissions, the same catalog, the same matcher, the
    same required rows.

    A signed alias still wins where a reviewer wrote one, because
    ``setdefault`` leaves it alone. So this only ever *adds* a pairing a person
    did not rule on, which is exactly what it is measuring.
    """
    aliases = dict(reference.subject_aliases)
    for pair in alignment.pairs:
        aliases.setdefault(pair.produced, AliasTarget(to=pair.reference))
    return SignedReference(
        catalog=reference.catalog,
        subject_aliases=aliases,
        qualifier_aliases=reference.qualifier_aliases,
    )


def relax(
    specs: Sequence[tuple[str, int, Path]],
    cases: Sequence[GoldenCase],
    corpus_dir: Path,
) -> tuple[Relaxation, ...]:
    """Each arm's required rows, scored once as it stands and once relaxed."""
    found: list[Relaxation] = []
    for arm, _, path in specs:
        loaded = load_artifact(path)
        held = [case for case in cases if case.id in loaded.cases]
        produced = heads_from_reports(path, held)
        totals: Counter[str] = Counter()
        for case in held:
            reference = signed_reference(corpus_dir, case)
            result = produced.get(case.id)
            if reference is None or result is None:
                continue
            required = set(required_rows(reference))
            alignment = align(case, _graph_of(result))
            for key, graded in (
                ("", replay_assertions(case, reference, result)),
                (
                    "relaxed_",
                    replay_assertions(
                        case, relaxed_reference(reference, alignment), result
                    ),
                ),
            ):
                for fate in graded.rows:
                    if fate.reference not in required:
                        continue
                    if not key:
                        totals["required"] += 1
                    if fate.fate == "found":
                        totals[f"{key}found"] += 1
                    elif fate.fate in ADJUDICATED_FATES:
                        totals[f"{key}wrong"] += 1
        found.append(
            Relaxation(
                arm=arm,
                required=totals["required"],
                found=totals["found"],
                relaxed_found=totals["relaxed_found"],
                wrong=totals["wrong"],
                relaxed_wrong=totals["relaxed_wrong"],
            )
        )
    return tuple(found)


def render_relaxations(found: Sequence[Relaxation]) -> str:
    """What the relaxed constraint recovers, and what it introduces."""
    lines = [
        "| arm | required | found | relaxed | recovered | wrong | relaxed wrong |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for one in found:
        lines.append(
            f"| {one.arm} | {one.required} | {one.found} | {one.relaxed_found} |"
            f" +{one.recovered} | {one.wrong} | {one.relaxed_wrong} |"
        )
    return "\n".join(lines)
