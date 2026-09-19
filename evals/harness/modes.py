"""The three eval modes over one corpus.

Two artifacts per case buy three modes, and the point of the split is
attribution. An end-to-end-only fixture cannot say whether a recall miss was a
category-agent failure or an element ``extract`` never produced.

* extraction — source text against the blessed model. It runs the shipped
  ``extract`` node alone, and puts its emission through the same shipped
  validity gate ``validate`` uses.
* analysis — the blessed model injected at ``prepare``, scored against the
  reference threats. The input is deterministic, so every threat number is
  attributable to the category agents and the critic.
* end-to-end — text in, report out. This is the integration smoke test.

All three drive the shipped graph through
:func:`~analysis_service.graph.build_pipeline`, and differ only in its ``entry``
and the state seeded into the session. Nothing about the topology, the prompts,
the skills, the tier config or the sampling config is eval-specific. Grading a
configuration you do not ship is the failure this whole design rejects.

Every function here needs live provider credentials, and nothing here runs in
the credential-free pull-request job. That job scores recorded output through
:mod:`evals.harness.scorer` and :mod:`evals.harness.structural`, both of which
take plain data.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, get_args

from analysis_service.analysis import (
    comparable_asset_tags,
    control_state,
)
from analysis_service.assertions import (
    ABSENT,
    REGISTRY,
    AssertionCatalog,
    AssertionRecord,
    Assessment,
    CatalogIssue,
    CatalogProposal,
    ProjectionReason,
    conflicts,
    project,
    projection_fields,
)
from analysis_service.basis import (
    IN_SCOPE,
    UnbasedControl,
    read_controls,
)
from analysis_service.basis import (
    Coverage as BasisCoverage,
)
from analysis_service.claims import (
    Claim,
    FrameworkName,
)
from analysis_service.compact import FULL_FORMAT, parse_extraction
from analysis_service.deployment import Deployment
from analysis_service.execution import GraphExecutor, GraphFailed, GraphRun
from analysis_service.frameworks.stride.record import DraftThreat
from analysis_service.graph import (
    ENTRY_ASSERT_ONLY,
    ENTRY_EXTRACT,
    ENTRY_EXTRACT_ONLY,
    ENTRY_HEAD_ONLY,
    ENTRY_PREPARE,
    STATE_ASSERTION_CATALOG,
    STATE_ASSERTION_PROPOSAL,
    STATE_BUNDLE_DISPOSITIONS,
    STATE_EXTRACTED_MODEL,
    STATE_FIRST_PASS,
    STATE_FRAMEWORK_OPTIONS,
    STATE_PATCH_OUTCOMES,
    STATE_REPAIR_BASELINE,
    STATE_SOURCE_FACTS,
    STATE_SOURCE_INVENTORY,
    STATE_SOURCE_TEXTS,
    STATE_VALID_MODEL,
    Entry,
    GraphProducedNothing,
    ModelResolver,
    Pipeline,
    Rejected,
    result_of,
)
from analysis_service.report import (
    FrameworkSelection,
    InputRef,
    Job,
    NodeRun,
    Report,
)
from analysis_service.sampling import (
    SamplingConfig,
)
from analysis_service.sources import Source
from analysis_service.system_model import (
    FLOW_DELIMITER,
    UNKNOWN,
    DataFlow,
    Element,
    ExternalEntity,
    FlowIdError,
    ModelIndex,
    SystemModel,
    TrustBoundary,
    flow_id_version,
    parse_flow_id,
)
from analysis_service.validation import ValidationIssue
from evals.harness.alignment import (
    Alignment,
    Pair,
    align,
    element_type,
    protocol_state,
    singular,
)
from evals.harness.identity import comparable_elements
from evals.harness.reference import GoldenCase

EVAL_APP_NAME = "analysis-evals"
EVAL_USER = "eval-harness"


class EvalRunError(RuntimeError):
    """A graph run produced neither the artifact the mode wanted nor a rejection."""


class CaseFailure(GraphFailed):
    """A case that did not become a report, and what its graph ran first.

    Two ways in. The graph ran to the end and the result failed to build, in
    which case every node was billed (#707). Or a node raised inside the graph,
    in which case every node that finished before it was billed and the
    executor hands those out as :class:`~analysis_service.execution.GraphFailed`
    (#711). Either way the provider billed what ran whatever came next, and a
    sweep that read a case's node runs off its finished report alone would let
    a failed case contribute nothing to the usage the artifact prices, so a run
    that cost a dollar would read as free. ``cause`` is the exception the caller
    classifies, unwrapped.

    ``extraction`` is the first pass the graph kept before it failed, where
    the graph finished and refused its model; a sweep writes it beside the
    artifact the way it writes a finished case's, because a refused model is
    the emission most worth reading back (#961). ``None`` where the fault
    came before the gate or the graph ran none.

    ``assertion`` is the same fact about a head-only run: the stages it wrote
    before it reached no catalog. A head that fails is the run most worth
    reading back, because the stages say which of them lost the rows — and a
    sweep that archived nothing for it left the question unanswerable.
    """

    def __init__(
        self,
        cause: Exception,
        node_runs: Sequence[NodeRun],
        extraction: ExtractionResult | None = None,
        assertion: AssertionResult | None = None,
    ) -> None:
        super().__init__(cause, node_runs)
        self.extraction = extraction
        self.assertion = assertion


@dataclass(frozen=True)
class ExtractionResult:
    """One extraction run: what came out, whether it was valid, and what ran.

    ``node_runs`` is carried even though this mode produces no report: the
    ``extract`` execution presented an execution identity like any other, and
    sourcing observations from the report would make exactly the tier this mode
    exercises the one tier it could never certify.
    """

    case_id: str
    extracted: SystemModel | None
    issues: tuple[ValidationIssue, ...]
    node_runs: tuple[NodeRun, ...] = ()
    #: What the ``extract`` node emitted, before IDs were derived and before
    #: the gate ran. Kept because it is the only thing a re-score cannot
    #: recompute: a normalized model has already had a slug decision made for
    #: it, and a scorer change that reads IDs differently needs what arrived
    #: (#925). Empty on a run that produced nothing.
    raw: Mapping[str, Any] = MappingProxyType({})
    #: What ``repair`` emitted where the gate sent the first pass to it, else
    #: ``None``. The extraction mode runs no repair, so there it is always
    #: ``None``. An end-to-end run keeps it beside ``raw`` because its report
    #: carries the model after the overlay and ``model_repair``, and neither
    #: reconstructs what the repair node itself returned (#961).
    repair: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AnalysisRun:
    """A graph run's report, plus the draft union each critic was handed.

    The drafts are what each framework's fan-in parked on the way into that
    framework's critic, read back through
    :meth:`~analysis_service.execution.GraphRun.drafts_of` as **the package's
    own record**, so every scorer stays typed against the shipped model and no
    harness code spells a state key.

    ``drafts`` is keyed by framework because the drafts are: two frameworks'
    subgraphs never touch, each has its own fan-in and its own critic, and a
    pooled draft list would ask one scorer to read another package's record.
    ``merged_drafts`` stays as STRIDE's, because the scorer and the critic-yield
    instrument both read a :class:`DraftThreat`'s ``category`` and ``severity``,
    which only that record carries.
    """

    report: Report
    drafts: Mapping[FrameworkName, tuple[Claim, ...]]
    #: Each lane's emission as the node dumped it, keyed by framework then by
    #: lane, before the fan-in routed anything away. A draft no longer carries
    #: ``needs_evidence`` — the fan-in strips it once it has decided whether the
    #: proposal becomes a draft — so without this the lane's own answer to *what
    #: would settle this* cannot be audited after the run (#657).
    proposals: Mapping[FrameworkName, Mapping[str, Any]] = field(default_factory=dict)
    #: The first pass of ``extract`` and what ``repair`` returned, on a graph
    #: that ran them. ``None`` on the analysis mode, which seeds the blessed
    #: model at ``prepare`` and runs neither.
    extraction: ExtractionResult | None = None

    @property
    def merged_drafts(self) -> tuple[DraftThreat, ...]:
        """STRIDE's half, at the record its own scorers are typed against."""
        return tuple(
            draft
            for draft in self.drafts.get("stride", ())
            if isinstance(draft, DraftThreat)
        )


def _tags(value: list[str]) -> str:
    """One element's asset tags as a comparable string, order removed.

    Every tag counts, on both sides. #877 retired the two that named what a
    failure would cost rather than what the element holds, so this scorer no
    longer drops a half: the vocabulary itself carries the rule now.

    :func:`~analysis_service.analysis.comparable_asset_tags` builds the value,
    here and in the candidate rule that reports an element's tags, so the
    scorer and the rule cannot part company about what they compare.
    """
    return ", ".join(comparable_asset_tags(value))


#: The attributes an extraction is measured on, each with the function that
#: reduces it to a comparable value. Declaration order, because the per-sweep
#: aggregate prints in it.
#:
#: **Two kinds of attribute, and nothing else.** A closed vocabulary — ``kind``,
#: ``exposure``, the asset tags — is set arithmetic against the blessed model,
#: exact and needs no interpretation. A free-text control is not, but
#: :func:`~analysis_service.analysis.control_state` reduces it to ``unverified`` /
#: ``absent`` / ``stated`` by its leading token, and *that* is comparable. So
#: this measures the state rather than the wording, which keeps interpretation out
#: and still catches the corpus's most repeated extraction failure: a control
#: invented where the blessed model says ``unknown``.
#:
#: ``interface_kind`` is a closed vocabulary too, and it is the one the ASVS
#: precondition reads: a process extracted as ``web`` where the blessed model
#: says ``non-web`` moves whether the framework runs at all. ``protocol`` is
#: free text, so only its *state* is compared — stated or silent, through
#: :func:`~analysis_service.analysis.states_a_protocol`, the same reader the
#: precondition uses — because a silent protocol is what holds that gate open,
#: and an extraction that invents one closes it (#659).
#:
#: What is deliberately absent is the wording of every free-text attribute —
#: ``technology``, ``data_description``, the protocol's own text. Two correct
#: readings of one sentence word them differently, so an exact test on them
#: reports disagreement that is not there. ``trust_zone`` is absent for the
#: opposite reason: :attr:`ExtractionScore.crossings_match` already reads it,
#: derived rather than compared string by string.
class _Scored(NamedTuple):
    """How one attribute is compared, and whether the comparison reads the fact.

    ``states`` is the half a reader of the aggregate has to know about.
    ``kind``, ``exposure``, ``interface_kind`` and ``operations`` hold closed
    vocabularies and ``assets`` a controlled one, so comparing them compares
    what they say. The other five hold free text that is reduced to a state
    first: ``control_state`` maps a whole mechanism to ``stated``, ``absent`` or
    ``unverified``, and a protocol to whether it says anything at all. Those
    comparisons agree on ``session cookie; no MFA`` against ``session cookie;
    MFA enforced for every shopper``, and on either against ``arbitrary
    nonsense`` (#891, #925).

    A field added to the table must say which it is, so a sixth control
    attribute cannot default into the aggregate as though it compared a fact.
    """

    reduce: Callable[[Any], str]
    states: bool


_SCORED_ATTRIBUTES: Mapping[str, _Scored] = {
    "kind": _Scored(str, states=False),
    "exposure": _Scored(str, states=False),
    "interface_kind": _Scored(str, states=False),
    "assets": _Scored(_tags, states=False),
    "operations": _Scored(str, states=False),
    "protocol": _Scored(protocol_state, states=True),
    "authentication": _Scored(control_state, states=True),
    "encryption_in_transit": _Scored(control_state, states=True),
    "encryption_at_rest": _Scored(control_state, states=True),
    "data_classification": _Scored(control_state, states=True),
}

#: The scored fields whose comparison reads a state rather than the fact. Taken
#: off the table rather than listed beside it, so the two cannot disagree about
#: which fields those are.
STATE_REDUCED: frozenset[str] = frozenset(
    name for name, scored in _SCORED_ATTRIBUTES.items() if scored.states
)

#: The state-reduced fields no basis check can answer for, derived from the two
#: registries rather than listed. A state agreement on one of these is reported
#: with nothing beside it, and that is a property of the field rather than an
#: omission: ``analysis_service.basis`` is out of scope on
#: ``data_classification`` because it holds *schema* words where the source holds
#: its own — "not exposed outside the cluster" is correctly written ``internal``
#: and shares no word with it — and ``protocol`` is not a control attribute at
#: all, so that module never walks it.
STATE_UNCHECKED: frozenset[str] = STATE_REDUCED - frozenset(IN_SCOPE)


@dataclass(frozen=True)
class AttributeCheck:
    """One attribute of one element, as each model states it.

    ``blessed`` and ``extracted`` hold the *reduced* values that
    :data:`_SCORED_ATTRIBUTES` compared, never the raw text: a report saying
    ``unverified -> stated`` names the failure, where the two sentences behind
    it would only name the wording.
    """

    element_id: str
    attribute: str
    blessed: str
    extracted: str

    @property
    def agrees(self) -> bool:
        return self.blessed == self.extracted

    @property
    def key(self) -> str:
        """What this check is about, as ``<element type>.<attribute>``.

        The type is carried because ``kind`` names two different closed
        vocabularies — an entity's ``human``/``external-system`` and a
        boundary's four — and a sweep-wide split that pooled them would report
        a drift without saying which one drifted. The type is read off the ID's
        prefix, which is where an element ID always carries it.
        """
        return f"{self.element_id.split(':', 1)[0]}.{self.attribute}"

    def to_json(self) -> dict[str, str]:
        return {
            "element": self.element_id,
            "attribute": self.attribute,
            "blessed": self.blessed,
            "extracted": self.extracted,
        }


def _is_zone(element_id: str) -> bool:
    """Is this a trust zone, the element type the claim comparison never reads?

    The one spelling of that test here, held to
    :func:`~evals.harness.identity.comparable_elements` — which drops exactly
    this population — by ``tests/test_evals_modes.py``.
    """
    return element_id.startswith(f"{TrustBoundary.id_prefix}:")


def _endpoint_key(element_id: str) -> str:
    """One element ID reduced to what identifies it structurally.

    A flow's label is the describing half, so it drops and its two endpoints
    stay. Every other type is returned unchanged: there is no structural key
    behind an entity's or a boundary's name, and an ID that is not a flow ID
    under any shipped identity version has no parts to drop.

    Through the identity's own decoder rather than a split of its own. This read
    the first two colon-separated segments while a flow ID was
    ``flow:<endpoints>:<label>``; under a version whose endpoints carry their
    types that returns ``flow:<one prefix>`` for every flow in the model, so
    every crossing keyed alike and the precision reading went to 1.0 over a
    model that separated everything from everything.
    """
    try:
        version = flow_id_version(element_id)
    except FlowIdError:
        return element_id
    parts = parse_flow_id(element_id, version)
    return f"flow:{parts.source}{FLOW_DELIMITER}{parts.destination}"


def _endpoint_keys(ids: Iterable[str]) -> frozenset[str]:
    return frozenset(_endpoint_key(element_id) for element_id in ids)


#: The element types a submitter's own text names, so a name absent from it is
#: a component nobody described. A flow's name is a label the model coins for an
#: interaction and a zone is one it invents per implied boundary, so neither is
#: a name the source was ever going to hold. The one reader of that population:
#: :func:`_name_tokens_absent` asks its question of these, and
#: :attr:`ExtractionScore.named_extra` reports the split over them.
#:
#: **Subtracted from the registry, never listed.** ``Element`` is the closed set
#: of element types and each one carries its own ``id_prefix``, so a sixth type
#: added tomorrow is in this population unless somebody rules it out — the
#: direction that fails loudly. A hand-typed triple would have left it silently
#: outside both readers.
NAMED_TYPES: tuple[str, ...] = tuple(
    sorted(
        element.id_prefix
        for element in get_args(Element)
        if element not in (DataFlow, TrustBoundary)
    )
)


#: What a score knows about one extra element. ``equivalent`` is the
#: alignment's word; ``unreviewed`` is every other extra, and it is the honest
#: word for what no figure here can decide (#961). A reader's rulings —
#: supported, unsupported, ambiguous — join this literal when a field carries
#: them.
ExtraStatus = Literal["equivalent", "unreviewed"]


@dataclass(frozen=True)
class ExtractionScore:
    """Agreement between an extraction and the blessed model.

    Purely mechanical — element IDs are typed slugs, so set arithmetic answers
    this mechanically. Element *naming* drift shows up as a miss plus a
    spurious element, which is the honest reading for a **report reader**: a
    threat filed against ``process:auth-svc`` does not resolve for someone
    holding ``process:auth-service``.

    It is the wrong reading for a question about **extraction**, and #293 is
    what showed the difference. Two models on the same corpus both missed
    ``flow:entity:card-processor>process:storefront-api>settlement-webhook`` and both
    emitted the same endpoints under another label — ``payment-webhook`` and
    ``post-webhook``. Identical architecture, one word apart, charged once as a
    miss and again as an invention. Measured over 13 cases, folding the flow
    label alone recovers 24-39% of both.

    So ``endpoint_*`` carries the second reading beside the strict one, and
    neither replaces the other. **A flow's identity is its endpoints; its label
    is descriptive** — the same principle
    :func:`~evals.harness.identity.endpoint_form` applies to a claim, for the
    same reason. Nothing else is folded: an entity, process, store or boundary
    has no structural key behind its name, so ``entity:shopper`` against
    ``entity:shoppers`` stays a miss and a spurious element. Guessing there
    would be a fuzzy match wearing a mechanical number's clothes.

    ``attributes`` carries the second half, on the elements both models hold:
    the values a Candidate rule reads. Without it an extraction that types
    every Trust Boundary ``network`` scores exactly like one that picks
    ``privilege`` and ``tenant`` correctly, and a value the live pipeline
    stopped producing leaves every number here flat
    ([#195](https://github.com/mstarks01/work-agent/issues/195)). It is
    **reported, never gated**: it carries no threshold, because a low number is
    not a defect on its own, and adding a scored metric would move every
    baseline this repo tracks.
    """

    case_id: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    crossings_match: bool
    attributes: tuple[AttributeCheck, ...]
    #: Which produced element stands for which blessed one, and on what
    #: evidence — :mod:`evals.harness.alignment` is the one reader of that
    #: question, and every figure below that asks it reads this. ``matched``,
    #: ``missing`` and ``extra`` are the strict reading by exact ID, kept beside
    #: it so no archived number changes meaning.
    alignment: Alignment = field(default_factory=Alignment.empty)
    #: The extra elements with a name token the submitted text does not
    #: contain — a word-presence diagnostic and nothing more. It says the model
    #: used a word nobody wrote; it does not say the element is invented, and
    #: an element absent from this list is not thereby real: a component
    #: assembled from the source's own vocabulary passes it (#961). Carried
    #: rather than recomputed, because it reads the case's source bytes.
    name_tokens_absent_from_source: tuple[str, ...] = ()
    #: The blessed model's pure initiators — elements that only ever start an
    #: interaction. Carried so the reading below needs no second model walk.
    blessed_initiators: tuple[str, ...] = ()
    #: The blessed **decided** crossings and the extraction's, each as
    #: endpoint-pair keys. ``extracted_crossings`` is ``None`` where derivation
    #: raised — a model with a dangling endpoint has said nothing, not "nothing
    #: crosses".
    blessed_crossings: tuple[str, ...] = ()
    extracted_crossings: tuple[str, ...] | None = None
    #: The flows whose crossing the extraction could not decide, because it
    #: placed one endpoint nowhere (ADR 0039). Beside the two figures above
    #: rather than inside them: an extraction that placed nothing has not
    #: separated a pair, and it has not put the pair together either.
    extracted_undecidable_crossings: tuple[str, ...] | None = None
    #: The extraction's stated controls that its own cited source does not echo
    #: (:mod:`analysis_service.basis`). The same failure the ``unverified ->
    #: stated`` attribute check names, read from the other side: that check asks
    #: what the blessed model says, and this one asks what the *source* says, so
    #: it fires on an invented control the blessed model happens to state too.
    #: Non-gating, like every number here.
    unbased: tuple[UnbasedControl, ...] = ()
    #: The citation half of the validity gate, as it ruled on this extraction:
    #: an excerpt that is not in the source it names, or a label naming a source
    #: the job never carried.
    #:
    #: A measurement rather than a Tier 1 failure, because production does not
    #: treat it as one either — the ``validate`` node routes such a model to
    #: ``repair``, and this mode stops before that pass. So the number says how
    #: much work the repair pass is being left, and a sweep that reported it as
    #: a malformed model would fail every run for doing its job. Non-gating,
    #: like every number here.
    uncited: tuple[ValidationIssue, ...] = ()
    #: How much of this extraction the stated-control diagnostic could read at
    #: all. ``unbased`` is a count of problems found, and on its own an empty
    #: one is three different facts: every value echoed its source, no value
    #: was readable, or there were no stated values. A run whose citations are
    #: blank or whose mechanisms carry no content token reads clean on every
    #: other figure here, and this is where it stops reading clean (#925).
    basis_coverage: BasisCoverage = field(default_factory=BasisCoverage.empty)
    #: Scored fields the blessed model carries, which is what
    #: :attr:`attributes_compared` is a fraction of. Counted at scoring time
    #: from the reference, because the score does not keep the model and a
    #: denominator recovered later would be a second reader of it.
    blessed_scored_fields: int = 0
    #: Every pair of zoned elements the alignment paired, asked whether the
    #: pair shares a zone, split by the blessed answer. Computed at scoring
    #: time for the reason the field above is: the score does not keep either
    #: model.
    zone_pairs: ZonePairs | None = None

    @property
    def recall(self) -> float:
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    @property
    def precision(self) -> float:
        total = len(self.matched) + len(self.extra)
        return len(self.matched) / total if total else 0.0

    @property
    def aliased(self) -> tuple[Pair, ...]:
        """Every element found under a name a reader ruled supported.

        The alignment's ``alias`` pairs, each carrying the excerpt the ruling
        rests on. Empty on a case nobody has ruled on.
        """
        return self.alignment.by_evidence("alias")

    def _reference_of_type(self, prefix: str) -> frozenset[str]:
        """The blessed elements of one type, from the strict reading's two halves."""
        return frozenset(
            element_id
            for element_id in (*self.matched, *self.missing)
            if element_type(element_id) == prefix
        )

    @property
    def aligned_recall(self) -> float:
        """Of the citable blessed elements, the share some produced element stands for.

        The alignment's reading beside :attr:`endpoint_recall` and
        :attr:`sourced_recall`. It credits an exact ID, a reader's alias, a
        flow under its own label between aligned endpoints, and a flow the
        discriminators single out — and nothing else, so an interaction the
        model drew between two real elements with a different operation is
        missed here and matched by the endpoint fold. Zones sit outside it as
        they sit outside every citable figure.
        """
        reference = comparable_elements((*self.matched, *self.missing))
        found = reference & self.alignment.aligned_reference
        return len(found) / len(reference) if reference else 0.0

    @property
    def aligned_precision(self) -> float:
        """Of the citable produced elements, the share that stands for a blessed one."""
        produced = comparable_elements((*self.matched, *self.extra))
        found = produced & frozenset(self.alignment.reference_of)
        return len(found) / len(produced) if produced else 0.0

    @property
    def aligned_interaction_recall(self) -> float:
        """Of the blessed interactions, the share the alignment paired.

        One to one, unlike :attr:`interaction_recall`, which counts flows per
        endpoint pair and so credits an invented interaction that replaced a
        real one between the same two elements (#961). A pair here rests on
        the label or on the discriminators, so that replacement is a miss and
        an extra, and two flows the rules cannot tell apart are neither.
        """
        flows = self._reference_of_type(DataFlow.id_prefix)
        found = flows & self.alignment.aligned_reference
        return len(found) / len(flows) if flows else 0.0

    @property
    def aligned_interaction_precision(self) -> float:
        """Of the produced interactions, the share that stands for a blessed one."""
        produced = frozenset(
            element_id
            for element_id in (*self.matched, *self.extra)
            if element_type(element_id) == DataFlow.id_prefix
        )
        found = produced & frozenset(self.alignment.reference_of)
        return len(found) / len(produced) if produced else 0.0

    @property
    def actor_recall(self) -> float:
        """Of the blessed external entities, the share the alignment paired.

        Actor retention on its own, apart from :attr:`initiator_recall`: that
        figure reads the graph's pure source nodes, and an actor that also
        receives a callback is outside it (#961). This asks the plainer
        question — is each actor the source describes present under some
        aligned identity — and a reader's alias counts.
        """
        actors = self._reference_of_type(ExternalEntity.id_prefix)
        found = actors & self.alignment.aligned_reference
        return len(found) / len(actors) if actors else 0.0

    @property
    def sourced_recall(self) -> float:
        """Recall over citable elements, crediting a name a reader ruled supported.

        **The second of two standards, and it does not replace the first.**
        :attr:`endpoint_recall` asks naming-policy conformity: did the
        extraction write the name ``extract.md`` asks for, which is the source's
        own wording. This asks semantic fidelity: does the name identify the
        described thing at all.

        The gap between them is the whole reading, the way the gap between
        strict and endpoint recall is. A wide gap is an extraction that found
        the architecture and named it its own way; a narrow one at a low number
        is an extraction that found different things.

        A pair only counts where a person ruled it — see
        :class:`~evals.harness.reference.ElementAlias`. Nothing here infers a
        rename, so a case nobody has ruled on reads exactly
        :attr:`endpoint_recall`.

        **A credit rules on the name and on nothing else.** It does not say the
        two zones hold the same members, answer to the same owner or carry the
        same privilege, and it does not say the extraction was right to draw a
        zone there at all. Those are modelling judgements, and each one is
        decided by the reference set and the claim scorer rather than here. Read
        a credit as "the source names the thing this way", which is the only
        question the excerpt can answer.
        """
        credited = comparable_elements(
            frozenset(credit.reference for credit in self.aliased)
        )
        found = len(comparable_elements(self.endpoint_matched)) + len(
            comparable_elements(
                _endpoint_keys(self.extra) & _endpoint_keys(self.missing)
            )
        )
        total = found + len(comparable_elements(self.endpoint_missing))
        return (found + len(credited)) / total if total else 0.0

    @property
    def naming_departures(self) -> int:
        """Elements found under a supported name that is not the source's wording.

        The cost of the gap above, as a count rather than a rate: each one is a
        component the extraction identified correctly and named its own way.
        ``extract.md`` rule 3 asks for the source's word, so this is the number
        an edit to that rule moves.
        """
        return len(self.aliased)

    @property
    def same_type_unmatched_candidates(self) -> tuple[str, ...]:
        """Unaligned extra elements of a type some blessed element is unaligned in.

        **A candidate list, and not a count of renames.** One unaligned
        blessed store and twenty unaligned extra stores put all twenty here,
        and twenty elements cannot all be one store under another name; the
        list says only that a rename is not ruled out by type (#961). Which
        blessed element an extra one renames is a reader's ruling, recorded as
        an alias and read by the alignment, and an extra the alignment already
        paired is outside this list.

        An extra element of a type the alignment paired completely has nothing
        to be a rename of, so it is outside this list too — but that is the
        whole of what its absence says. It is not thereby an invention and not
        thereby a component the corpus omits; :attr:`extra_status` leaves it
        ``unreviewed``.
        """
        unaligned = frozenset(self.alignment.unaligned_reference)
        displaced = {element_type(element_id) for element_id in unaligned}
        found = self.alignment.reference_of
        return tuple(
            element_id
            for element_id in self.extra
            if element_id not in found and element_type(element_id) in displaced
        )

    @property
    def extra_status(self) -> Mapping[str, ExtraStatus]:
        """What is known about each extra element, and it is usually nothing.

        ``equivalent`` where the alignment paired it with a blessed element —
        on a reader's alias, or as a flow under its own label or the
        discriminators between aligned endpoints. ``unreviewed`` for every
        other extra: it may be a supported component the corpus omits, an
        unsupported one the model invented, or a name for a blessed element
        nobody has ruled on, and no figure here can tell those apart. The
        statuses a reader can assign — supported, unsupported, ambiguous —
        wait on a ruling field somebody fills (#961 step 3).
        """
        found = self.alignment.reference_of
        return {
            element_id: "equivalent" if element_id in found else "unreviewed"
            for element_id in self.extra
        }

    @property
    def named_extra(self) -> tuple[str, ...]:
        """Extra elements of a type a submitter's own text names.

        The flow labels the model coins swamp ``extra`` — 107 of the 136 a run
        on the sweep of 2026-09-13 — and rule 2 of ``extract.md`` has it invent
        a zone per implied boundary. Neither is a name the source holds, so the
        split over :data:`NAMED_TYPES` is the reading that answers a question
        about the corpus.
        """
        return tuple(
            element_id
            for element_id in self.extra
            if element_type(element_id) in NAMED_TYPES
        )

    @property
    def initiators_missing(self) -> frozenset[str]:
        """Pure initiators the blessed model holds that no produced element stands for.

        Read off the alignment, so an initiator found under a reader's alias
        is kept: on corpus case 09 the approved name for the catalogue
        spreadsheet read here as a dropped initiator (#961).
        """
        return frozenset(self.blessed_initiators) - self.alignment.aligned_reference

    @property
    def initiator_recall(self) -> float:
        """Of the blessed model's pure initiators, the share the extraction kept.

        Read beside the overall element recall rather than instead of it: the
        two together say whether an extraction is uniformly thin or is dropping
        one *kind* of element. A case with no pure initiator reads 0.0, for the
        reason every other empty denominator here does.

        **A pure initiator is a graph source node**, an element that starts an
        interaction and never receives one, and that is narrower than an actor
        that initiates an action: case 09's customer places orders and
        receives a fax, so it is outside this denominator. :attr:`actor_recall`
        is the reading over every external entity.
        """
        if not self.blessed_initiators:
            return 0.0
        kept = len(self.blessed_initiators) - len(self.initiators_missing)
        return kept / len(self.blessed_initiators)

    @property
    def crossings_found(self) -> frozenset[str]:
        """Blessed crossings the extraction also separated, by endpoint pair."""
        return frozenset(self.blessed_crossings) & frozenset(
            self.extracted_crossings or ()
        )

    @property
    def crossings_recall(self) -> float:
        """Of the blessed crossings, the share the extraction also derived.

        Names dropped, so this is the reading ``crossings_match`` cannot give.
        It is still bounded by what the extraction found at all: a crossing
        whose flow was never emitted cannot be separated, however it is
        compared. On the 2026-08-23 sweeps that ceiling was 40% and 55%.
        """
        if not self.blessed_crossings:
            return 0.0
        return len(self.crossings_found) / len(self.blessed_crossings)

    @property
    def crossings_precision(self) -> float:
        """Of the crossings the extraction derived, the share the reference holds.

        **Recall alone never charges an invented crossing**, and inventing them
        is free: put every element in a zone of its own and every flow in the
        model crosses a boundary, so the blessed crossings are all still there
        and recall reads 1.0 over a partition that is wrong everywhere (#925).
        A model that separated nothing is the opposite failure and recall
        already sees it, which is why both are reported rather than folded.

        ``0.0`` where the extraction derived none, on the same reading
        :attr:`crossings_recall` gives an empty reference: nothing derived is
        not a perfect score.
        """
        derived = self.extracted_crossings
        if not derived:
            return 0.0
        return len(self.crossings_found) / len(frozenset(derived))

    @property
    def crossings_derivable(self) -> bool:
        """Whether the extraction was well-formed enough to derive crossings."""
        return self.extracted_crossings is not None

    @property
    def interaction_recall(self) -> float:
        """Endpoint recall with multiplicity, so a parallel flow is not free.

        :func:`_endpoint_keys` returns a *set*, and the schema and the corpus
        both carry more than one interaction between the same pair — case 13's
        console reaches its API with ordinary dispatch requests and with a
        WebSocket held open for live job status. Delete one and the pair is
        still in the folded set, so ``endpoint_recall`` reads 1.0 over a model
        that lost a whole handshake, session and transport surface (#925).

        So the graph is read as a directed multigraph: for each endpoint pair,
        how many of the reference's interactions the extraction kept, capped at
        the reference's count so duplicates cannot pay for a miss elsewhere.
        A pair the extraction named differently still counts, exactly as
        ``endpoint_recall`` intends — this charges the *number* of interactions
        and never their labels.
        """
        blessed = Counter(
            _endpoint_key(element_id)
            for element_id in (*self.matched, *self.missing)
            if element_type(element_id) == DataFlow.id_prefix
        )
        if not blessed:
            return 0.0
        extracted = Counter(
            _endpoint_key(element_id)
            for element_id in (*self.matched, *self.extra)
            if element_type(element_id) == DataFlow.id_prefix
        )
        kept = sum(min(count, extracted[pair]) for pair, count in blessed.items())
        return kept / sum(blessed.values())

    @property
    def endpoint_matched(self) -> frozenset[str]:
        """The matched set with every flow reduced to its endpoint pair."""
        return _endpoint_keys(self.matched)

    @property
    def endpoint_missing(self) -> frozenset[str]:
        """Missed under endpoint folding: not matched, and not emitted elsewhere.

        Subtracting the emitted keys is what makes this a second *reading*
        rather than a second count. A flow the model produced under another
        label is in ``extra`` strictly and in ``endpoint_matched`` here, so it
        must leave the missing set too or one flow is charged on both sides.
        """
        emitted = self.endpoint_matched | _endpoint_keys(self.extra)
        return _endpoint_keys(self.missing) - emitted

    @property
    def endpoint_extra(self) -> frozenset[str]:
        """Spurious under endpoint folding."""
        blessed = self.endpoint_matched | _endpoint_keys(self.missing)
        return _endpoint_keys(self.extra) - blessed

    @property
    def endpoint_recall(self) -> float:
        """Recall over the elements a claim can cite, so zones are not counted.

        :func:`~evals.harness.identity.comparable_elements` drops every
        ``boundary:`` ID before any claim comparison, so a zone the extraction
        named differently cannot cost a single match downstream. Counting one
        as a miss depressed this figure by eleven points — 0.642 against 0.755
        on the sweep of 2026-09-13 — over 24 elements a run that no reader
        consumes. Whether the zone structure itself is right is a real
        question, and :attr:`zone_recall` beside this one is where it is asked.
        """
        found = len(comparable_elements(self.endpoint_matched)) + len(
            comparable_elements(
                _endpoint_keys(self.extra) & _endpoint_keys(self.missing)
            )
        )
        total = found + len(comparable_elements(self.endpoint_missing))
        return found / total if total else 0.0

    def _zones(self) -> tuple[frozenset[str], frozenset[str]]:
        """The case's trust zones, and the ones the extraction did not write.

        The one reader of that split, so :attr:`zone_recall` and
        :attr:`sourced_zone_recall` cannot disagree about the population they
        score over.
        """
        blessed = frozenset(
            key
            for key in _endpoint_keys(self.missing) | _endpoint_keys(self.matched)
            if _is_zone(key)
        )
        gone = frozenset(key for key in self.endpoint_missing if _is_zone(key))
        return blessed, gone

    @property
    def zone_recall(self) -> float:
        """The half :attr:`endpoint_recall` no longer counts: the trust zones.

        Kept apart rather than dropped. A zone name reaches no claim, but the
        zones are what :meth:`~analysis_service.system_model.SystemModel.boundary_crossings`
        is derived from, so a model that invents its own set is a finding even
        though the identity rule never reads one.
        """
        blessed, gone = self._zones()
        return (len(blessed) - len(gone)) / len(blessed) if blessed else 0.0

    @property
    def sourced_zone_recall(self) -> float:
        """:attr:`zone_recall`, crediting a zone a reader ruled supported.

        The zone half of the pair :attr:`sourced_recall` and
        :attr:`endpoint_recall` make, and it sits beside :attr:`zone_recall`
        rather than replacing it, for the same reason: one asks whether the
        extraction kept the source's wording, the other whether the name
        identifies the zone the source describes.

        A ``boundary:`` ID reaches :attr:`sourced_recall` through nothing —
        :func:`~evals.harness.identity.comparable_elements` drops every zone
        before a credit counts there — so this is the only figure a reviewed
        alias on a zone can move. A case nobody ruled on reads exactly
        :attr:`zone_recall`.

        A credit rules on the name and on nothing else. It does not say two
        zones hold the same members, answer to the same owner or carry the same
        privilege, and it does not say the extraction was right to draw a zone
        there at all.
        """
        blessed, gone = self._zones()
        if not blessed:
            return 0.0
        credited = gone & frozenset(
            credit.reference for credit in self.aliased if _is_zone(credit.reference)
        )
        return (len(blessed) - len(gone) + len(credited)) / len(blessed)

    @property
    def zone_partition_agreement(self) -> float:
        """Do the same elements sit together, whatever the zones are called?

        **A zone recall is a recall over zone *names*.** Every figure beside it
        reads a name too, so a model can keep every name and put the members
        anywhere: collapse case 01's five elements into one existing zone and
        ``zone_recall`` holds at 1.0, and split each into a zone of its own
        while retaining the originals empty and every zone, endpoint and
        crossing figure holds at 1.0 (#925).

        So this reads the *partition* and never a name. Over the zoned elements
        the alignment paired, every pair is asked one question — do these two
        share a zone? — and the score is the share of pairs the two models
        answer the same way. A renaming moves it not at all, which is the
        point: it is the one zone figure a naming difference cannot reach.

        **Not a whole-system guarantee, and it credits two kinds of pair
        alike.** A pair correctly kept apart counts as much as a pair correctly
        kept together, and a pair with an unaligned element is asked nothing,
        so a high value can sit over omitted nodes and a missed co-membership
        (#961). :attr:`same_zone_recall`, :attr:`different_zone_agreement` and
        :attr:`zone_pair_coverage` are the three readings beside it.

        ``0.0`` where fewer than two zoned elements are paired, because there
        is no pair to ask and an empty agreement is not a perfect one.
        """
        pairs = self.zone_pairs
        if pairs is None or not pairs.compared:
            return 0.0
        return (pairs.same_agreed + pairs.apart_agreed) / pairs.compared

    @property
    def same_zone_recall(self) -> float:
        """Of the aligned pairs the blessed model puts together, the share the extraction does too.

        The co-memberships, on their own. Case 01's elements split into a zone
        each leaves every apart pair agreeing and every together pair not, and
        the pooled figure reads the average; this reads the loss.
        """
        pairs = self.zone_pairs
        if pairs is None or not pairs.same_total:
            return 0.0
        return pairs.same_agreed / pairs.same_total

    @property
    def different_zone_agreement(self) -> float:
        """Of the aligned pairs the blessed model keeps apart, the share the extraction does too.

        The separations, on their own: one zone for everything leaves every
        together pair agreeing and this at zero.
        """
        pairs = self.zone_pairs
        if pairs is None or not pairs.apart_total:
            return 0.0
        return pairs.apart_agreed / pairs.apart_total

    @property
    def zone_pair_coverage(self) -> float:
        """What share of the blessed model's zoned pairs the partition figures were asked over.

        A pair with an unaligned element is outside every figure above, so a
        model that dropped half the zoned elements can read a perfect
        partition over the half it kept; this is the denominator beside that
        reading, the way :attr:`comparison_coverage` sits beside the attribute
        agreement.
        """
        pairs = self.zone_pairs
        if pairs is None or not pairs.reference_total:
            return 0.0
        return pairs.compared / pairs.reference_total

    @property
    def differing(self) -> tuple[AttributeCheck, ...]:
        """The checks the two models answered differently, in model order."""
        return tuple(check for check in self.attributes if not check.agrees)

    @property
    def scored_field_agreement(self) -> float:
        """Agreement over every scored field of the elements both models hold.

        **Not an accuracy.** Half these fields are compared after a reduction to
        a state, so this rises when an extraction picks the right *kind* of
        answer and says nothing about whether the answer is right.
        :attr:`control_state_agreement` is that half on its own, and
        :attr:`comparison_coverage` says how much of the reference it was taken
        over at all.
        """
        agreed = len(self.attributes) - len(self.differing)
        return agreed / len(self.attributes) if self.attributes else 0.0

    @property
    def state_agrees_unbased(self) -> int:
        """State agreements whose extracted value its own cited source never echoes.

        **The join the two figures lacked.** A state-reduced comparison folds a
        whole mechanism to one of three words, so ``arbitrary nonsense`` reads
        ``stated`` and agrees with a real control; the ``unbased`` diagnostic
        sees exactly that and nothing tied the two together (#891). Replacing
        every stated scored value in corpus case 01 with that literal reads
        agreement 1.000 and five here.

        Counted over the fields a basis check can answer, so
        :data:`STATE_UNCHECKED` is outside it and a reader needs both numbers.
        It reads the flags this run's own extraction produced, which is the
        model the agreement was taken over.

        A diagnostic, gating nothing, like the flags it reads.
        """
        flagged = {(flag.element_id, flag.attribute) for flag in self.unbased}
        return sum(
            1
            for check in self.attributes
            if check.attribute in STATE_REDUCED
            and check.agrees
            and (check.element_id, check.attribute) in flagged
        )

    @property
    def control_state_agreement(self) -> float:
        """The :data:`STATE_REDUCED` fields alone, named for what they compare.

        ``protocol``, ``authentication``, ``encryption_in_transit``,
        ``encryption_at_rest`` and ``data_classification`` reach this after a
        whole mechanism has been folded to one of three words. So a 1.0 here
        means the extraction agreed about which *state* each control is in —
        stated, absent or unverified — and carries no claim about the mechanism,
        version, principal, scope or strength it named (#891).
        """
        checks = [c for c in self.attributes if c.attribute in STATE_REDUCED]
        if not checks:
            return 0.0
        return sum(check.agrees for check in checks) / len(checks)

    @property
    def attributes_comparable(self) -> int:
        """Scored fields the reference carries, whether or not one was compared.

        The denominator :attr:`attributes_compared` is a subset of. An element
        the extraction named differently leaves the comparison entirely, taking
        its attributes with it, so agreement can be *improved* by dropping the
        elements whose facts are hardest to get right — five renamed flows take
        25 of case 01's 47 fields out of the numerator and the denominator
        together (#925).
        """
        return self.blessed_scored_fields

    @property
    def attributes_compared(self) -> int:
        """Scored fields the two models were actually compared on.

        The numerator :attr:`comparison_coverage` and the serialised count both
        read, so the figure and the number beside it cannot be taken over two
        different populations.
        """
        return len(self.attributes)

    @property
    def comparison_coverage(self) -> float:
        """What fraction of the reference's scored fields was compared at all.

        Read beside every agreement above it. A high agreement over a low
        coverage is a statement about the elements that happened to align, and
        alignment here is exact-ID: a renamed element is not compared, not
        compared leniently.
        """
        total = self.attributes_comparable
        return self.attributes_compared / total if total else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "endpoint_recall": round(self.endpoint_recall, 3),
            # The same fold with multiplicity, so a second interaction between
            # one pair of endpoints is not free to drop (#925).
            "interaction_recall": round(self.interaction_recall, 3),
            "zone_recall": round(self.zone_recall, 3),
            "sourced_zone_recall": round(self.sourced_zone_recall, 3),
            # The zone figure a naming difference cannot reach: do the same
            # elements sit together, whatever the zones are called (#925).
            "zone_partition_agreement": round(self.zone_partition_agreement, 3),
            # The partition figure's three readings: the co-memberships alone,
            # the separations alone, and how much of the reference either was
            # asked over (#961).
            "same_zone_recall": round(self.same_zone_recall, 3),
            "different_zone_agreement": round(self.different_zone_agreement, 3),
            "zone_pair_coverage": round(self.zone_pair_coverage, 3),
            "endpoint_missing": sorted(self.endpoint_missing),
            "endpoint_extra": sorted(self.endpoint_extra),
            "name_tokens_absent_from_source": list(self.name_tokens_absent_from_source),
            "sourced_recall": round(self.sourced_recall, 3),
            "aliased": [credit.to_json() for credit in self.aliased],
            # The alignment's own reading, beside the strict and folded ones:
            # one produced element per blessed one, on recorded evidence, and
            # what it left unpaired (#961).
            "alignment": self.alignment.to_json(),
            "aligned_recall": round(self.aligned_recall, 3),
            "aligned_precision": round(self.aligned_precision, 3),
            "aligned_interaction_recall": round(self.aligned_interaction_recall, 3),
            "aligned_interaction_precision": round(
                self.aligned_interaction_precision, 3
            ),
            "actor_recall": round(self.actor_recall, 3),
            "same_type_unmatched_candidates": list(self.same_type_unmatched_candidates),
            "extra_status": dict(self.extra_status),
            "initiator_recall": round(self.initiator_recall, 3),
            "initiators_missing": sorted(self.initiators_missing),
            "crossings_match": self.crossings_match,
            "crossings_recall": round(self.crossings_recall, 3),
            # Beside recall, which never charges an invented crossing.
            "crossings_precision": round(self.crossings_precision, 3),
            "crossings_derivable": self.crossings_derivable,
            # Beside recall and precision rather than inside either. A flow
            # here is one the extraction found and placed one endpoint of
            # nowhere, so neither figure has an answer to give about it, and a
            # sweep reading them alone would see the silence as a low score
            # without seeing what caused it (ADR 0039).
            "crossings_undecidable": sorted(self.extracted_undecidable_crossings or ()),
            "crossings_missing": sorted(
                frozenset(self.blessed_crossings) - self.crossings_found
            ),
            "missing": list(self.missing),
            "extra": list(self.extra),
            # Named for what it compares. Half these fields reach the check as
            # a state rather than as the fact they hold, so this is agreement
            # over scored fields and never an extraction accuracy (#891).
            "scored_field_agreement": round(self.scored_field_agreement, 3),
            "state_agrees_unbased": self.state_agrees_unbased,
            "control_state_agreement": round(self.control_state_agreement, 3),
            "attributes_compared": self.attributes_compared,
            # The denominator beside the count, so a figure taken over half the
            # reference cannot read like one taken over all of it (#925).
            "attributes_comparable": self.attributes_comparable,
            "comparison_coverage": round(self.comparison_coverage, 3),
            # The disagreements alone. An agreeing check is a number, and the
            # count above carries it; writing all of them out would bury the
            # few lines a reader opens this file for.
            "attributes_differing": [check.to_json() for check in self.differing],
            "unbased_controls": [flag.model_dump(mode="json") for flag in self.unbased],
            # The denominator beside the flags: how many stated controls this
            # model carried, and how many of them the scan could read at all.
            "basis_coverage": self.basis_coverage.to_json(),
            "uncited": [issue.model_dump(mode="json") for issue in self.uncited],
        }


def aggregate_attributes(scores: Sequence[ExtractionScore]) -> dict[str, Any]:
    """The whole sweep's attribute agreement, and the same split per attribute.

    Both, because they answer different questions. The total says whether
    anything drifted; the split says *which value stopped arriving*, which is
    the question [#184](https://github.com/mstarks01/work-agent/issues/184)
    needed a hand-run count of candidates by lane to answer.
    """
    checks = [check for score in scores for check in score.attributes]
    compared = Counter(check.key for check in checks)
    agreed = Counter(check.key for check in checks if check.agrees)
    order = list(_SCORED_ATTRIBUTES)
    keys = sorted(compared, key=lambda key: (order.index(key.split(".", 1)[1]), key))
    return {
        "compared": len(checks),
        "agreed": sum(agreed.values()),
        "agreement": round(sum(agreed.values()) / len(checks), 3) if checks else 0.0,
        "by_attribute": {
            key: {
                "compared": compared[key],
                "agreed": agreed[key],
                "agreement": round(agreed[key] / compared[key], 3),
            }
            for key in keys
        },
    }


#: The frameworks a sweep runs for a case that declares none, and the fallback
#: for a caller that names no case. A sweep grades a framework's own claim set
#: (#167), so which frameworks ran is a property of the *case* rather than of the
#: harness — see :func:`case_frameworks`, which is what the sweep actually reads.
EVAL_FRAMEWORKS: tuple[FrameworkName, ...] = ("stride",)


def case_framework_options(case: GoldenCase) -> dict[str, dict[str, Any]]:
    """The job-level options this case declares, in the shape the graph seeds.

    **The harness is a driver, and a driver seeds these.** ``prepare_analysis``
    validates every selected framework's options against that package's own
    model and raises ``MissingFrameworkOptions`` when one is absent, because no
    package field carries a default. ``AdkPipelineRunner`` builds this map from
    the job's ``frameworks`` list; this builds the same map from the case's,
    which is where a corpus case has always declared them.

    Missing until #290, and it made ``analysis`` and ``end-to-end`` unrunnable
    for any package with a required option. It went unnoticed because STRIDE
    declares none, so the omission was invisible for as long as STRIDE was the
    only package — and ``tests/test_graph.py`` seeds ``ASVS_OPTIONS`` by hand,
    so no offline test drove the path that omits them.
    """
    return {
        declaration.name: dict(declaration.options)
        for declaration in case.meta.frameworks
    }


def select_frameworks(
    case: GoldenCase, only: Sequence[FrameworkName] = ()
) -> tuple[FrameworkName, ...]:
    """This case's frameworks, narrowed to ``only`` when a sweep asks for it.

    **A pure selection: it names no option and changes no reference set.** A
    case still declares what it is graded for and still carries the options for
    it; this only decides which of those declarations one sweep builds a graph
    for. So a narrowed sweep and a full one measure the same cases the same way
    — the narrowed one just measures fewer frameworks per case.

    Empty ``only`` means every framework the case declares.

    **Why it exists is capacity, not preference.** One job fans out one
    ``strong``-tier request per lane of every framework it names, all at the
    barrier — :func:`~analysis_service.frameworks.widest_fan_out`, 23 today. On a
    200,000 token-per-minute quota that burst is over budget on a single job,
    and no per-caller ceiling helps: ``max_active_jobs`` bounds jobs, and this
    is one job. Narrowing the selection is the only lever inside the harness.

    A case that declares none of ``only`` yields an empty tuple and its caller
    skips it, which is a case the sweep did not measure rather than one that
    measured nothing.
    """
    declared = case_frameworks(case)
    if not only:
        return declared
    return tuple(name for name in declared if name in set(only))


def case_frameworks(case: GoldenCase) -> tuple[FrameworkName, ...]:
    """The frameworks to build this case's graph for: the ones it declares.

    Read off ``case.json`` rather than fixed for the sweep. A case declares a
    framework when that framework's **Precondition** allows one and it carries a
    reference set, so running the declaration is what makes "every record the
    corpus holds is graded" true — and what stops a sweep paying for ASVS's 17
    ``strong``-tier lanes on a case that has nothing to score them against.

    A case declaring nothing falls back to :data:`EVAL_FRAMEWORKS`, which keeps
    a hand-built fixture case runnable without a frameworks array.
    """
    declared = tuple(declaration.name for declaration in case.meta.frameworks)
    return declared or EVAL_FRAMEWORKS


def case_selections(case: GoldenCase, pipeline: Pipeline) -> list[FrameworkSelection]:
    """The job selection, taken from the built graph and dressed in the case's options.

    **The names come from the pipeline, never from the case.** The envelope
    checks that a report's blocks answer the job's own selection, so a driver
    that named the case's declaration while the graph ran something else would
    fail that check rather than record what happened — which is exactly what a
    test binding a STRIDE-only pipeline to a case declaring two frameworks does.

    The *options* come from the case, because they are load-bearing and the
    graph does not carry them: an **ASVS Level** decides which requirements a run
    rules on, so a job that omitted it is rejected on the input ladder and one
    that guessed it grades against the wrong slice of the catalog.
    """
    options = {
        declaration.name: dict(declaration.options)
        for declaration in case.meta.frameworks
    }
    return [
        FrameworkSelection(name=name, options=options.get(name, {}))
        for name in pipeline.frameworks
    ]


def build_eval_pipeline(
    entry: Entry,
    *,
    deployment: Deployment | None = None,
    resolve_model: ModelResolver | None = None,
    sampling: SamplingConfig | None = None,
    frameworks: Sequence[FrameworkName] = EVAL_FRAMEWORKS,
) -> Pipeline:
    """The shipped graph, entered where the mode needs it.

    Built from a :class:`~analysis_service.deployment.Deployment`, which is the
    same thing the service is built from — including the retry and per-request
    timeout, so a scheduled sweep does not die on one 429 after hours of work.
    Locating config through the deployment rather than the repo root is what
    makes "eval and production read from the same place" true rather than
    aspirational: a deployment that redirects a path has its sweeps grading the
    configuration it actually runs.

    ``resolve_model`` short-circuits the tier adapters deliberately: building
    them runs the credential check, which an offline test binding scripted
    models has no credentials to pass and no provider to call. ``sampling``
    overrides the deployment's for a sweep varying the per-tier params.
    """
    deployment = deployment or Deployment.from_env()
    if sampling is not None:
        deployment = replace(deployment, sampling=sampling)
    return deployment.pipeline(frameworks, entry=entry, resolve_model=resolve_model)


async def run_graph(
    pipeline: Pipeline,
    sources: Sequence[Source],
    extra_state: Mapping[str, Any] | None = None,
) -> GraphRun:
    """Drive one graph to completion and hand back the Graph Run.

    The shipped :class:`~analysis_service.execution.GraphExecutor` drives it, so
    a sweep stamps each node execution exactly as the service does — the served
    build it presented, and the execution-identity fingerprint that build is
    one of seven parts of.
    Stamping this here rather than in the harness is what makes the eval CLI a
    real second caller of :func:`~analysis_service.certification.certify` rather
    than one certifying an empty observation set.
    """
    executor = GraphExecutor(pipeline, app_name=EVAL_APP_NAME)
    try:
        return await executor.run(sources, user_id=EVAL_USER, extra_state=extra_state)
    except GraphFailed as failed:
        # One failure type for the sweep, whichever side of the graph's end the
        # fault sat on: what ran joins the artifact's usage before the cause is
        # classified.
        raise CaseFailure(failed.cause, failed.node_runs) from failed.cause


async def run_extraction(case: GoldenCase, pipeline: Pipeline) -> ExtractionResult:
    """Mode 1: the source text through ``extract``, and nothing else."""
    graph_run = await run_graph(pipeline, case.sources)
    state = graph_run.final_state
    if STATE_EXTRACTED_MODEL not in state:
        raise EvalRunError(f"{case.id}: extract produced no model")
    # The transport is read off the built graph rather than guessed from the
    # payload, and the expansion runs through the same
    # :func:`~analysis_service.compact.parse_extraction` the ``validate`` node
    # calls — so a compact run is graded on the model production would have
    # built from the same emission, not on a second reading of the wire form.
    # First-pass semantics are unchanged: this mode still runs no repair.
    #
    # normalize_ids mirrors the ``validate`` node: blessed models already carry
    # derived IDs, so scoring a candidate's raw IDs by set membership would
    # count an abbreviated slug as one missing element and one extra, on a
    # reading of the source that was correct.
    #
    # ``sources`` mirrors it too, and for a sharper reason: the citation half
    # of the gate does not run without them, so omitting them grades a model
    # against a weaker gate than the one production applies, and an invented
    # excerpt that passes here fails inside a job. The mapping is read off the
    # state the executor seeded rather than rebuilt from ``case.sources``, so
    # the gate sees the labels the run actually carried.
    return _extraction_result(
        case, graph_run, pipeline, state[STATE_EXTRACTED_MODEL], graph_run.node_runs
    )


def _extraction_result(
    case: GoldenCase,
    graph_run: GraphRun,
    pipeline: Pipeline,
    raw: Mapping[str, Any],
    node_runs: Sequence[NodeRun],
    repair: Mapping[str, Any] | None = None,
) -> ExtractionResult:
    """One emission of ``extract`` through the gate production applies to it."""
    state = graph_run.final_state
    model, issues = parse_extraction(
        raw,
        pipeline.extraction_format or FULL_FORMAT,
        sources=state.get(STATE_SOURCE_TEXTS, {}),
    )
    return ExtractionResult(
        case_id=case.id,
        extracted=model,
        issues=tuple(issues),
        node_runs=tuple(node_runs),
        raw=raw,
        repair=repair,
    )


def _first_pass(
    case: GoldenCase, graph_run: GraphRun, pipeline: Pipeline
) -> ExtractionResult | None:
    """What ``extract`` emitted and what ``repair`` returned, off a finished graph.

    The first gate parks the emission under :data:`STATE_FIRST_PASS` on every
    route out of it, and ``repair`` writes its own over
    :data:`STATE_EXTRACTED_MODEL`; a repair ran exactly where the gate parked
    a baseline for it. ``None`` on a graph that ran no gate, which is the
    analysis entry seeding the blessed model at ``prepare``.
    """
    state = graph_run.final_state
    if STATE_FIRST_PASS not in state:
        return None
    repair = state[STATE_EXTRACTED_MODEL] if STATE_REPAIR_BASELINE in state else None
    return _extraction_result(
        case, graph_run, pipeline, state[STATE_FIRST_PASS], (), repair
    )


#: The graph state a head-only run keeps beside its catalog, by the key the node
#: that wrote it uses. A **table**, so a stage added to a head is archived by
#: adding a row here rather than by editing a writer that lists what it knows
#: about — and a key nothing wrote is absent rather than empty.
#:
#: Each one answers a question the catalog alone cannot. The raw bundle and
#: inventory are what the model emitted, which resolving has already dropped
#: rows from. The dispositions say what every input row came to. The extracted
#: model is the graph the rows bound against, and the valid model is the graph
#: after any repair. The patch outcomes say what a review pass changed, and
#: whether its batch was discarded.
ARCHIVED_STATE: tuple[str, ...] = (
    STATE_SOURCE_FACTS,
    STATE_SOURCE_INVENTORY,
    STATE_BUNDLE_DISPOSITIONS,
    STATE_EXTRACTED_MODEL,
    STATE_VALID_MODEL,
    STATE_PATCH_OUTCOMES,
)


@dataclass(frozen=True)
class AssertionResult:
    """One assertion run: what was proposed, what resolved, and what ran.

    ``proposal`` is what the node emitted, kept for the reason
    :attr:`ExtractionResult.raw` is kept: it is the only thing a re-score
    cannot recompute, because resolving has already dropped rows and located
    spans. ``issues`` is why each dropped row dropped.

    ``stages`` is what every earlier node of the run wrote, by
    :data:`ARCHIVED_STATE`. A head that reads the sources facts-first composes
    its proposal out of a bundle code resolved, so the proposal alone cannot say
    what the model read or where a row was lost between the two.
    """

    case_id: str
    proposal: Mapping[str, Any]
    catalog: AssertionCatalog
    issues: tuple[CatalogIssue, ...]
    node_runs: tuple[NodeRun, ...] = ()
    stages: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssertionScore:
    """What one case's assertion run produced, counted rather than graded.

    **No agreement figure here, because this mode counts rather than grades.**
    Every number is a reading of what the run did: how many rows survived, which
    predicates they covered, and how much of the model they reached.
    :func:`~evals.harness.replay.replay_assertions` is the one reader that
    compares a run to a signed reference, and it runs over the cases whose facts
    a reviewer signed.

    ``absences`` is the one number that answers the audit directly. It counts
    rows whose value is ``absent`` — a control the sources say is **not there**
    — which is the fact the graph's own attributes read as a stated control in
    ten of the corpus's 21 stated mechanism values.

    Four outcomes are kept apart (#961). ``rejected`` is rows the resolver
    dropped, counted by row; ``refused`` is the reasons, by code, and one row
    can draw several. ``span_backed`` is kept rows whose quotes located, which
    is not a judgement that the words support the value; ``assessed`` is the
    rows somebody judged, by assessment, and an extractor leaves every row
    ``unchecked``, so a run reads all zero there until a reviewer sits.
    """

    case_id: str
    proposed: int
    kept: int
    rejected: int
    refused: Mapping[str, int]
    subjects: int
    bound_subjects: int
    predicates: tuple[str, ...]
    span_backed: int
    assessed: Mapping[str, int]
    spans: int
    absences: int
    unknowns: int
    inferred: int
    conflicts: int
    subjects_reached: float
    #: The control facts the blessed model states that some predicate in
    #: :data:`~analysis_service.assertions.REGISTRY` can project into, and how
    #: many of them the catalog's own rows reach. **This is the denominator the
    #: projection had none of**: ``projected`` counts what the catalog emitted,
    #: so a catalog reaching one attribute and getting it right read 1/1.
    #:
    #: Only pairs whose blessed value reads as ``stated`` are counted here,
    #: because reaching an attribute the blessed model leaves unverified asks
    #: nothing of the model. Registry version 2 can reach five attributes; on
    #: corpus case 01 that is 20 pairs, 13 of them stated.
    reachable: int
    reached: int
    #: The same pair of counts over the facts the blessed model states as
    #: **absent** — a control the sources say is not there, which
    #: :func:`~analysis_service.analysis.control_state` reads as ``absent``.
    #: A second stratum rather than a share of the first, because omitting an
    #: explicit absence is the loss this layer exists to prevent and a
    #: denominator of stated facts alone could not see it: seven such pairs
    #: across the corpus were outside every count (#961).
    reachable_absent: int
    reached_absent: int
    #: The graph attributes the catalog reaches, and what projecting them costs.
    #: ``degraded`` counts the ones that read ``unknown`` because a scope, a
    #: second value or a second predicate would not fit one string, which is the
    #: loss the compatibility projection is *supposed* to report rather than
    #: hide.
    projected: int
    projection_degraded: int
    #: Agreement, each field compared the way :data:`PROJECTION_COMPARED`
    #: says — a closed vocabulary by its value, a zone by its identity, a
    #: mechanism by its state — and **split by what was agreed about**.
    #: ``agrees_stated`` is an agreement about a fact the blessed model states.
    #: ``agrees_absent`` is an agreement that a control is absent, which the
    #: blessed model states too. ``agrees_unstated`` is an agreement that the
    #: blessed model states nothing — both sides reading ``unknown``.
    #:
    #: Three fields rather than one, because pooling them is the #891 mistake:
    #: agreeing that an unstated control is unknown is free, and on the first
    #: assertion benchmark two of luna's eight agreements were of that kind.
    #: Their sum is the single figure they replace, so no reader loses one.
    agrees_stated: int
    agrees_absent: int
    agrees_unstated: int

    def to_json(self) -> dict[str, Any]:
        """The per-case payload a sweep carries in the artifact's mode output."""
        return {
            "case": self.case_id,
            "proposed": self.proposed,
            "kept": self.kept,
            "rejected": self.rejected,
            "refused": dict(sorted(self.refused.items())),
            "subjects": self.subjects,
            "bound_subjects": self.bound_subjects,
            "predicates": list(self.predicates),
            "span_backed": self.span_backed,
            "assessed": dict(self.assessed),
            "spans": self.spans,
            "absences": self.absences,
            "unknowns": self.unknowns,
            "inferred": self.inferred,
            "conflicts": self.conflicts,
            "subjects_reached": round(self.subjects_reached, 3),
            "reachable": self.reachable,
            "reached": self.reached,
            "reachable_absent": self.reachable_absent,
            "reached_absent": self.reached_absent,
            "projected": self.projected,
            "projection_degraded": self.projection_degraded,
            "agrees_stated": self.agrees_stated,
            "agrees_absent": self.agrees_absent,
            "agrees_unstated": self.agrees_unstated,
        }


async def run_assertions(case: GoldenCase, pipeline: Pipeline) -> AssertionResult:
    """Mode 4: the sources and the blessed model through ``assert``, resolved.

    The blessed model is seeded for the reason :func:`run_analysis` seeds it. A
    row this node could not bind to an element would otherwise be
    unattributable between two readings of one text, and the question this mode
    asks is what the sources state — not whether two calls named one component
    alike.
    """
    graph_run = await run_graph(
        pipeline, case.sources, {STATE_VALID_MODEL: case.model.model_dump(mode="json")}
    )
    state = graph_run.final_state
    if STATE_ASSERTION_PROPOSAL not in state:
        raise EvalRunError(f"{case.id}: assert produced no assertions")
    # Resolved and gated through the one reader production's ``prepare`` node
    # uses, so a sweep grades the catalog a job would have read.
    record = AssertionRecord.of(
        CatalogProposal.model_validate(state[STATE_ASSERTION_PROPOSAL]),
        case.model,
        state.get(STATE_SOURCE_TEXTS, {}),
    )
    return AssertionResult(
        case_id=case.id,
        proposal=state[STATE_ASSERTION_PROPOSAL],
        catalog=record.catalog,
        issues=tuple(record.issues),
        node_runs=tuple(graph_run.node_runs),
    )


async def run_heads(case: GoldenCase, pipeline: Pipeline) -> AssertionResult:
    """Mode 5: run one arm's head over the sources and stop at the catalog.

    **What #1003's primary endpoint is scored on, and nothing else.** The arm
    extracts its own model — that is the half the arms differ in — and the
    graph stops at the terminal ``catalog`` node, so no lane agent and no
    critic is billed for findings the endpoint never reads.

    Nothing is seeded. The assertion mode seeds a blessed model because its
    question is what the sources state about one; this mode's question is what
    a whole head recovers, so seeding would answer half of it in advance.

    The catalog is read back rather than resolved here: ``park_catalog`` has
    already put it through :func:`~analysis_service.graph._resolve_assertions`,
    the seam every catalog reaches a reader through, and resolving it a second
    time here would be a second answer to what one proposal came to.
    """
    graph_run = await run_graph(
        pipeline, case.sources, {STATE_FRAMEWORK_OPTIONS: case_framework_options(case)}
    )
    state = graph_run.final_state
    stages = {key: state[key] for key in ARCHIVED_STATE if key in state}
    if STATE_ASSERTION_CATALOG not in state:
        outcome = result_of(state)
        detail = (
            _rejection(outcome)
            if isinstance(outcome, Rejected)
            else "the head produced no catalog"
        )
        # Carried out with the failure, for the reason a refused model is: the
        # stages say where the rows went, and a run that archived nothing for a
        # failed case left that unanswerable.
        raise CaseFailure(
            EvalRunError(f"{case.id}: {detail}"),
            graph_run.node_runs,
            assertion=AssertionResult(
                case_id=case.id,
                proposal=state.get(STATE_ASSERTION_PROPOSAL, {}),
                catalog=AssertionCatalog(),
                issues=(),
                node_runs=tuple(graph_run.node_runs),
                stages=stages,
            ),
        )
    record = AssertionRecord.model_validate(state[STATE_ASSERTION_CATALOG])
    return AssertionResult(
        case_id=case.id,
        proposal=state.get(STATE_ASSERTION_PROPOSAL, {}),
        catalog=record.catalog,
        issues=tuple(record.issues),
        node_runs=tuple(graph_run.node_runs),
        stages=stages,
    )


def score_assertions(case: GoldenCase, result: AssertionResult) -> AssertionScore:
    """Count what one assertion run produced. Nothing here grades it."""
    catalog = result.catalog
    entries = catalog.entries
    bound = {subject.id for subject in catalog.subjects if ":" in subject.id}
    element_ids = {element.id for element in case.model.elements()}
    reached = {entry.subject for entry in entries} & element_ids
    return AssertionScore(
        case_id=result.case_id,
        proposed=len(result.proposal.get("assertions", ())),
        kept=len(entries),
        rejected=len({issue.row for issue in result.issues if issue.row is not None}),
        refused=Counter(issue.code for issue in result.issues),
        subjects=len(catalog.subjects),
        bound_subjects=len(bound & element_ids),
        predicates=tuple(sorted({entry.predicate for entry in entries})),
        span_backed=sum(1 for entry in entries if entry.support),
        assessed={
            assessment: sum(1 for entry in entries if entry.assessment == assessment)
            for assessment in get_args(Assessment)
        },
        spans=sum(len(entry.support) for entry in entries),
        absences=sum(1 for entry in entries if entry.value == ABSENT),
        unknowns=sum(1 for entry in entries if entry.value == UNKNOWN),
        inferred=sum(1 for entry in entries if entry.basis == "inferred"),
        conflicts=len(conflicts(catalog)),
        subjects_reached=len(reached) / len(element_ids) if element_ids else 0.0,
        **_projection_counts(case, catalog),
    )


#: Why a projected value degraded rather than carrying the catalog's answer.
#: Read off the projection's own reasons rather than listed again, so a reason
#: added to :data:`~analysis_service.assertions.ProjectionReason` is counted the
#: day it lands.
_DEGRADED: frozenset[str] = frozenset(get_args(ProjectionReason)) - {
    "stated",
    "absent",
    "unknown",
}


#: How a projected value is compared with the blessed attribute, keyed by the
#: graph field it projects into. :data:`_SCORED_ATTRIBUTES` is the one reader of
#: how a scored field reduces — a closed vocabulary such as ``exposure`` by its
#: value, a mechanism by its state — so those entries are taken off it rather
#: than spelled again. ``trust_zone`` is the one projected field the extraction
#: scorer does not score, because it reads zones through the crossings; it
#: holds an **Element ID** and compares by identity.
#:
#: Reducing every field to a control state made ``internal`` agree with
#: ``internet-facing``: both are stated (#961). A field a predicate projects
#: into that has no entry here raises at the first projection, which
#: ``tests/test_evals_modes.py`` holds against
#: :func:`~analysis_service.assertions.projection_fields`.
PROJECTION_COMPARED: Mapping[str, Callable[[Any], str]] = {
    **{name: scored.reduce for name, scored in _SCORED_ATTRIBUTES.items()},
    "trust_zone": str,
}

#: The blessed states a catalog can be asked to reproduce. ``unverified`` is
#: outside: reaching an attribute the blessed model leaves unverified asks
#: nothing of a model, and both sides reading ``unknown`` agree for free.
Stratum = Literal["stated", "absent"]


def reachable_controls(model: SystemModel) -> Mapping[tuple[str, str], Stratum]:
    """Every ``(element, attribute)`` pair a catalog can be asked to reproduce, by stratum.

    The projection's denominator, derived from the blessed model rather than
    authored: an attribute is reachable when some predicate in
    :data:`~analysis_service.assertions.REGISTRY` projects into it, the element
    carries it, and the blessed value reads as ``stated`` or ``absent``.

    **Two strata, and never a pooled one.** A stated fact and a stated absence
    are both facts the sources establish, and omitting either is a loss. They
    are counted apart because the layer exists for the second: the graph's own
    attributes read "session cookie; no MFA" as a stated control, and a
    denominator of stated facts alone left every explicit absence — case 01's
    unauthenticated gRPC, case 09's unencrypted HTTP — outside every count
    (#961). ``unverified`` pairs are outside both, for the reason
    :data:`Stratum` gives.

    No human review is needed for this to be ground truth, because the blessed
    control attributes are the same reviewed facts the extraction scorer already
    compares. It is not a reference *catalog*: it says nothing about the rows a
    predicate projecting into no graph field should hold, which is nine of the
    sixteen in registry version 2.

    The attribute set comes from
    :func:`~analysis_service.assertions.projection_fields`, the one reader of
    which field each predicate is authoritative for, rather than folded out of
    :data:`~analysis_service.assertions.REGISTRY` a second time here.
    """
    attributes = sorted(set(projection_fields().values()))
    strata: dict[tuple[str, str], Stratum] = {}
    for element in model.elements():
        for attribute in attributes:
            if not hasattr(element, attribute):
                continue
            state = control_state(str(getattr(element, attribute)))
            if state == "unverified":
                continue
            strata[element.id, attribute] = state
    return strata


def _projection_counts(case: GoldenCase, catalog: AssertionCatalog) -> dict[str, int]:
    """How much of the blessed graph the catalog's own rows reproduce.

    **Each field compared the way :data:`PROJECTION_COMPARED` says.** A
    mechanism is compared as a state, because comparing the strings would
    report a disagreement wherever two correct readings of one sentence are
    worded differently; a closed vocabulary and a zone are compared as
    written, because reducing ``internal`` to a state made it agree with
    ``internet-facing`` (#961).

    Agreement is split by what was agreed about. A projection landing on an
    attribute the blessed model states, one landing on an absence it states,
    and one landing where it states nothing are three counts, because the
    third costs a model nothing to get right and the second is the fact the
    layer exists for.
    """
    index = ModelIndex.of(case.model)
    reachable = reachable_controls(case.model)
    reached: dict[Stratum, set[tuple[str, str]]] = {"stated": set(), "absent": set()}
    agrees: Counter[str] = Counter()
    projections = project(catalog)
    for projection in projections:
        element = index.get(projection.element_id)
        if element is None:
            continue
        key = (projection.element_id, projection.attribute)
        stratum = reachable.get(key)
        if stratum is not None:
            reached[stratum].add(key)
        compared = PROJECTION_COMPARED[projection.attribute]
        blessed = getattr(element, projection.attribute, UNKNOWN)
        if compared(projection.value) == compared(str(blessed)):
            agrees[stratum or "unstated"] += 1
    return {
        "reachable": sum(1 for stratum in reachable.values() if stratum == "stated"),
        "reached": len(reached["stated"]),
        "reachable_absent": sum(
            1 for stratum in reachable.values() if stratum == "absent"
        ),
        "reached_absent": len(reached["absent"]),
        "projected": len(projections),
        "projection_degraded": sum(
            1 for projection in projections if projection.reason in _DEGRADED
        ),
        "agrees_stated": agrees["stated"],
        "agrees_absent": agrees["absent"],
        "agrees_unstated": agrees["unstated"],
    }


def render_assertions(scores: Sequence[AssertionScore]) -> None:
    """What an assertion sweep produced, per case and then over the corpus.

    Every number is **non-gating**, for the reason
    :func:`render_extraction`'s are: there is no reference catalog, so a low
    count is a question to take to the source text rather than a defect.
    """
    for score in scores:
        print(
            f"{score.case_id:<26} kept {score.kept:>3}/{score.proposed:<3}"
            f" spans {score.spans:>3} absences {score.absences:>2}"
            f" unknowns {score.unknowns:>2} conflicts {score.conflicts:>2}"
            f" elements reached {score.subjects_reached:.0%}"
            f" reached {score.reached}/{score.reachable}"
            f" (+{score.reached_absent}/{score.reachable_absent} absent)"
            f" agrees {score.agrees_stated} (+{score.agrees_absent} absent,"
            f" +{score.agrees_unstated} unstated)"
            f" degraded {score.projection_degraded} of {score.projected}"
        )
    if not scores:
        return
    refused: Counter[str] = Counter()
    for score in scores:
        refused.update(score.refused)
    covered = sorted({name for score in scores for name in score.predicates})
    print(
        f"\n{sum(s.kept for s in scores)} rows kept of"
        f" {sum(s.proposed for s in scores)} proposed,"
        f" {sum(s.rejected for s in scores)} rejected,"
        f" {len(covered)} of {len(REGISTRY)} predicates covered"
    )
    for code, count in sorted(refused.items()):
        print(f"  refused {code:<24} {count}")
    print(
        "  predicates with no row: " + ", ".join(sorted(set(REGISTRY) - set(covered)))
    )


def score_extraction(case: GoldenCase, result: ExtractionResult) -> ExtractionScore:
    """Compare an extraction to the blessed model, mechanically."""
    blessed_ids = {element.id for element in case.model.elements()}
    extracted_ids = (
        {element.id for element in result.extracted.elements()}
        if result.extracted
        else set()
    )
    crossings_match = _crossings_match(case.model, result.extracted)
    unbased, read = _basis(case, result.extracted)
    alignment = align(case, result.extracted)
    return ExtractionScore(
        case_id=case.id,
        matched=tuple(sorted(blessed_ids & extracted_ids)),
        missing=tuple(sorted(blessed_ids - extracted_ids)),
        extra=tuple(sorted(extracted_ids - blessed_ids)),
        crossings_match=crossings_match,
        alignment=alignment,
        attributes=_check_attributes(case.model, result.extracted, alignment),
        blessed_scored_fields=_scored_fields(case.model),
        zone_pairs=_zone_pairs(case.model, result.extracted, alignment),
        blessed_initiators=tuple(sorted(pure_initiators(case.model))),
        blessed_crossings=crossing_keys(case.model) or (),
        extracted_crossings=crossing_keys(result.extracted),
        extracted_undecidable_crossings=undecidable_crossing_keys(result.extracted),
        uncited=tuple(issue for issue in result.issues if issue.is_citation),
        unbased=unbased,
        basis_coverage=read,
        name_tokens_absent_from_source=_name_tokens_absent(
            case, sorted(extracted_ids - blessed_ids), result.extracted
        ),
    )


def _name_tokens_absent(
    case: GoldenCase, extra: Sequence[str], extracted: SystemModel | None
) -> tuple[str, ...]:
    """The extra elements with a name token the submitted text never uses.

    **A word-presence diagnostic, named for what it reads.** A name passes
    when every one of its words of three letters or more appears somewhere in
    the case's own source text. That over-credits a model that assembled a
    name from scattered words, and it does not ask whether the words refer to
    this component, so it neither measures invention nor rules it out (#961).
    What it does is name the elements a reader should open first.

    Singular and plural are one word here. A source writing "game servers",
    "Analysts" and "dashboards" had a model's ``game server``, ``analyst`` and
    ``dashboard`` read as three invented components on the first run that used
    this — 3.5 a run, against a true count near zero. Which of the two forms an
    extraction should write is the naming rule's question, and it is measured
    as recall against the corpus; charging it a second time here as invention
    would count one disagreement twice.

    **Only a component the text could name is asked.** An entity, a process and
    a data store are things a submitter describes, so a name absent from their
    words is a component nobody mentioned. A flow's name is a label the model
    coins for an interaction — ``dashboards-query-telemetry-lake`` — and rule 2
    of ``extract.md`` has it invent a zone for each one the text implies, so
    neither is a name the source was ever going to contain. Asking anyway read
    41 coined flow labels and 3 invented zones as invention on the first run
    that used this.

    What it is not is a rename test. Deciding that an extra element is a
    blessed one under another name is a reader's ruling, which
    :mod:`evals.harness.alignment` reads from the case's aliases; nothing here
    guesses at one.
    """
    if extracted is None:
        return ()
    text = re.sub(r"[^a-z0-9 ]", " ", " ".join(s.text for s in case.sources).lower())
    words = {singular(word) for word in text.split()}
    by_id = {element.id: element for element in extracted.elements()}
    out = []
    for element_id in extra:
        element = by_id.get(element_id)
        if element is None or element_type(element_id) not in NAMED_TYPES:
            continue
        tokens = [
            w for w in re.split(r"[^a-z0-9]+", element.name.lower()) if len(w) > 2
        ]
        if tokens and not {singular(token) for token in tokens} <= words:
            out.append(element_id)
    return tuple(out)


def _basis(
    case: GoldenCase, extracted: SystemModel | None
) -> tuple[tuple[UnbasedControl, ...], BasisCoverage]:
    """The extraction's unechoed stated controls, and what was read to find them.

    Read against the **extracted** model, not the blessed one: the question is
    what this run asserted about the text it was given. The blessed model's own
    rate is the false-rejection figure published in
    :mod:`analysis_service.basis`, and ``tests/test_basis.py`` re-derives it.

    Both halves come from one call, which is one walk and one scan budget: a
    flag count paired with a denominator taken over a second walk is the defect
    the single reader in that module exists to prevent.
    """
    if extracted is None:
        return (), BasisCoverage.empty()
    sources = {source.label: source.text for source in case.sources}
    flags, read = read_controls(extracted, sources)
    return tuple(flags), read


@dataclass(frozen=True)
class ZonePairs:
    """Every pair of zoned elements, asked whether the pair shares a zone.

    Split by the blessed answer, because the two answers are two different
    failures: ``same_*`` counts the pairs the blessed model puts together and
    how many the extraction does too, ``apart_*`` the pairs it keeps apart.
    ``reference_total`` is every pair of blessed zoned elements, paired or not,
    which is what the compared count is a coverage of.
    """

    same_agreed: int
    same_total: int
    apart_agreed: int
    apart_total: int
    reference_total: int

    @property
    def compared(self) -> int:
        return self.same_total + self.apart_total

    def to_json(self) -> dict[str, int]:
        return {
            "same_agreed": self.same_agreed,
            "same_total": self.same_total,
            "apart_agreed": self.apart_agreed,
            "apart_total": self.apart_total,
            "reference_total": self.reference_total,
        }


def _zone_pairs(
    blessed: SystemModel, extracted: SystemModel | None, alignment: Alignment
) -> ZonePairs | None:
    """Every pair of aligned zoned elements, and how many the models agree about.

    Agreement on one pair is "both models put these two together" or "both put
    them apart" — the question a zone *name* cannot answer and a partition can.
    Over the elements the alignment paired, because an unpaired element is
    already counted as a miss and reading it here would charge one omission
    twice; an element found under a reader's alias is paired and so is read.
    The pairs a dropped element takes with it are still in ``reference_total``,
    so the coverage beside the figure says how many were never asked.

    **An unplaced element is not asked about either.** Since
    [ADR 0039](../../docs/adr/0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md)
    ``trust_zone`` holds the unknown sentinel for a component the sources place
    nowhere, and two of them compare equal — so a model that placed nothing
    would read as putting every one of those pairs *together*, which is a claim
    it never made. Either side unplaced drops the element, on the same rule the
    unpaired ones follow: ``reference_total`` still counts its pairs, so the
    coverage says how much the figure did not ask.

    ``None`` where no extraction was handed in: nothing was measured, which is
    a different fact from every pair agreeing.
    """
    if extracted is None:
        return None
    theirs = {
        element.id: element.trust_zone
        for element in extracted.zoned_elements()
        if element.trust_zone != UNKNOWN
    }
    produced_of = alignment.produced_of
    zoned = blessed.zoned_elements()
    shared = [
        (produced_of[element.id], element.trust_zone)
        for element in zoned
        if produced_of.get(element.id) in theirs and element.trust_zone != UNKNOWN
    ]
    agreed = Counter[bool]()
    total = Counter[bool]()
    for index, (one_id, one_zone) in enumerate(shared):
        for other_id, other_zone in shared[index + 1 :]:
            together = one_zone == other_zone
            total[together] += 1
            if together == (theirs[one_id] == theirs[other_id]):
                agreed[together] += 1
    return ZonePairs(
        same_agreed=agreed[True],
        same_total=total[True],
        apart_agreed=agreed[False],
        apart_total=total[False],
        reference_total=len(zoned) * (len(zoned) - 1) // 2,
    )


def _scored_fields(model: SystemModel) -> int:
    """How many scored fields this model's elements carry between them.

    The same walk :func:`_check_attributes` makes, over one model rather than
    the pair, so ``compared`` and ``comparable`` count the same population and
    their ratio is a coverage rather than two unrelated numbers.
    """
    return sum(
        1
        for element in model.elements()
        for attribute in _SCORED_ATTRIBUTES
        if attribute in type(element).model_fields
    )


def _check_attributes(
    blessed: SystemModel, extracted: SystemModel | None, alignment: Alignment
) -> tuple[AttributeCheck, ...]:
    """Compare every scored attribute of the elements the alignment paired.

    Aligned elements only. An attribute of an unpaired element is already
    counted, as the miss, and reading it a second time here would charge one
    dropped element twice. Reading the pairs rather than the exact IDs is what
    keeps an element found under a reader's alias inside
    :attr:`ExtractionScore.comparison_coverage` (#961); the check is keyed by
    the blessed ID either way.

    Every rule in :mod:`evals.harness.alignment` keeps an element's type, so
    the attributes one side declares are the attributes the other declares,
    and the walk needs no per-type branch.
    """
    if extracted is None:
        return ()
    counterparts = {element.id: element for element in extracted.elements()}
    produced_of = alignment.produced_of
    checks = []
    for element in blessed.elements():
        counterpart = counterparts.get(produced_of.get(element.id, ""))
        if counterpart is None:
            continue
        for attribute, scored in _SCORED_ATTRIBUTES.items():
            if attribute not in type(element).model_fields:
                continue
            checks.append(
                AttributeCheck(
                    element_id=element.id,
                    attribute=attribute,
                    blessed=scored.reduce(getattr(element, attribute)),
                    extracted=scored.reduce(getattr(counterpart, attribute)),
                )
            )
    return tuple(checks)


def _crossings_match(blessed: SystemModel, extracted: SystemModel | None) -> bool:
    """Whether the extraction derives the blessed crossings.

    Derivation fails closed on a model whose flow endpoints are not zoned
    elements, which is precisely the kind of extraction this mode exists to
    catch — so a model that cannot derive crossings scores as disagreeing
    rather than crashing the sweep.

    **Compares names, and that is the reading it is for.** A
    :class:`~analysis_service.report.BoundaryCrossing` carries ``flow_id`` and two
    zones, and both zones hold a boundary *ID*. A reader of the report sees
    those strings, so a crossing naming a zone they do not hold is wrong for
    them. :func:`crossing_keys` is the other reading — see #297.
    """
    if extracted is None:
        return False
    try:
        return extracted.boundary_crossings() == blessed.boundary_crossings()
    except ValueError:
        return False


def pure_initiators(model: SystemModel) -> frozenset[str]:
    """Elements that only ever start an interaction, never receive one.

    **The failure mode this isolates.** Across five gpt-4o runs on an unchanged
    config, these were recalled at 0.421 against 0.600 for every other non-flow
    element — lower in all five, a gap of 0.179. 16 of the 19 distinct dangling
    endpoints in those runs were flow *sources*, and the repeated offenders are
    exactly these: `process:store-server`, `entity:developer`,
    `entity:duty-engineer`.

    In case 07 the extraction writes ``flow source 'process:store-server'`` —
    the exact ID the blessed model uses — and never declares the element. So
    this is neither naming drift nor a conceptual error, but an
    inventory-completeness failure at the elements a source text describes by
    what they *do* rather than by where they sit: "a developer writes a change",
    "every store server asks the deploy controller once a minute".

    Measured against total Tier 1 failures the effect is invisible — that count
    has an sd of 3.27 on an unchanged config. Measured here it is roughly five
    standard deviations of headroom, which is why this reading exists.
    """
    sources = {flow.source for flow in model.data_flows}
    destinations = {flow.destination for flow in model.data_flows}
    return frozenset(sources - destinations)


def crossing_keys(model: SystemModel | None) -> tuple[str, ...] | None:
    """Each decided crossing as its flow's endpoint pair, or ``None``.

    **A crossing means "this interaction has its two endpoints in different
    zones", and that sentence contains no zone name.** Two extractions can
    partition the same elements identically and name the partitions
    differently; compared as lists of
    :class:`~analysis_service.report.BoundaryCrossing`, that reads as total
    disagreement, and on the 2026-08-23 sweeps it did — ``crossings DIFFER`` on
    13 of 13 cases for two models five times apart in price.

    So the zones drop out entirely and membership survives as *set membership*:
    a flow is in this set exactly when the model **separated** its endpoints.
    The flow itself is keyed by :func:`_endpoint_key`, for the reason #293
    gives.

    **Separated, which is why an undecidable crossing is not here.** Since
    [ADR 0039](../../docs/adr/0039-a-crossing-a-model-cannot-decide-is-still-a-lead.md)
    a flow one of whose endpoints the model never placed derives a crossing
    carrying ``decided=False``. Counting it would credit an extraction that
    placed nothing with separating the pair, and the recall and precision
    figures below both read this set. :func:`undecidable_crossing_keys` is
    where those flows are reported, as their own figure, so the reading is
    beside the two rather than folded into them.

    ``None`` rather than an empty tuple where derivation raises, because a model
    with a dangling flow endpoint has not said that nothing crosses — it has
    said nothing at all, and scoring that as perfect agreement with an empty
    blessed set would reward the worst extraction in the sweep.
    """
    return _crossing_keys(model, decided=True)


def undecidable_crossing_keys(model: SystemModel | None) -> tuple[str, ...] | None:
    """Each undecidable crossing as its flow's endpoint pair, or ``None``.

    The complement of :func:`crossing_keys` over the same derivation, on the
    same key, so the two can be read side by side and neither can hold a flow
    the other does. A large count here is an extraction that found the
    interactions and placed the components nowhere, which reads on the two
    crossing figures as silence rather than as a wrong answer.
    """
    return _crossing_keys(model, decided=False)


def _crossing_keys(
    model: SystemModel | None, *, decided: bool
) -> tuple[str, ...] | None:
    """One half of the derivation, keyed by endpoint pair. The one reader."""
    if model is None:
        return None
    try:
        crossings = model.boundary_crossings()
    except ValueError:
        return None
    return tuple(
        sorted(
            {_endpoint_key(one.flow_id) for one in crossings if one.decided is decided}
        )
    )


async def run_analysis(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 2: the blessed model injected at ``prepare``.

    The seeded ``valid_model`` is the blessed one, so the category agents see exactly
    what the corpus blessed and nothing depends on that run's extraction.
    """
    graph_run = await run_graph(
        pipeline,
        case.sources,
        {
            STATE_VALID_MODEL: case.model.model_dump(mode="json"),
            STATE_FRAMEWORK_OPTIONS: case_framework_options(case),
        },
    )
    return _run_from_graph(case, graph_run, pipeline)


async def run_end_to_end(case: GoldenCase, pipeline: Pipeline) -> AnalysisRun:
    """Mode 3: text in, report out — the integration smoke test."""
    graph_run = await run_graph(
        pipeline, case.sources, {STATE_FRAMEWORK_OPTIONS: case_framework_options(case)}
    )
    return _run_from_graph(case, graph_run, pipeline)


def _run_from_graph(
    case: GoldenCase, graph_run: GraphRun, pipeline: Pipeline
) -> AnalysisRun:
    """:func:`_report_of`, with what ran attached to whatever it raises.

    The graph is finished by the time this is called, so every node it ran
    was billed; a failure past this point is a fact about the result and not
    about the spend, and the sweep needs both. The first pass rides out the
    same way, because the graph refusing its model is the one failure that
    is a measurement, and the emission it refused is what the measurement
    is of.
    """
    extraction = _first_pass(case, graph_run, pipeline)
    try:
        return _report_of(case, graph_run, pipeline, extraction)
    except Exception as exc:
        raise CaseFailure(exc, graph_run.node_runs, extraction) from exc


def _report_of(
    case: GoldenCase,
    graph_run: GraphRun,
    pipeline: Pipeline,
    extraction: ExtractionResult | None,
) -> AnalysisRun:
    """Complete the graph's :class:`~analysis_service.graph.Analysis` into a report, as production does.

    The report is built by :meth:`~analysis_service.graph.Analysis.into_report`,
    the same method :class:`~analysis_service.pipeline.AdkPipelineRunner` calls,
    so a sweep's reports carry every block a job's do. The Tier 1 gates check a
    whole ``Report`` — including the self-containment invariants — and a
    stripped-down payload would test a shape production never emits.

    What the eval supplies is what only this driver knows: a case-derived job
    identity and input reference. The node runs and the per-tier sampling clear
    block ride along with the graph run and the pipeline, without which a
    sweep's reports would carry no fingerprints and its certification verdict
    would be computed over nothing.
    """
    now = datetime.now(UTC)
    try:
        # The terminal shape first, through the one reader of it. A refused
        # model reached no fan-in, so reading the drafts first would name the
        # rejection as a framework the graph never reached.
        outcome = result_of(graph_run.final_state)
        if isinstance(outcome, Rejected):
            raise EvalRunError(f"{case.id}: {_rejection(outcome)}")
        # Grading is per framework (#167), so a scorer reads its own package's
        # drafts against its own reference set and two packages' records never
        # meet. Read before the report, so a framework the graph never reached
        # is named as such rather than as a report defect.
        drafts = {
            name: tuple(graph_run.drafts_of(name)) for name in pipeline.frameworks
        }
        proposals = {name: graph_run.proposals_of(name) for name in pipeline.frameworks}
        result = graph_run.report(
            job=Job(
                id=f"eval-{case.id}",
                created_at=now,
                completed_at=now,
                # Read off the built graph rather than restated, so the blocks
                # answer the job's own selection exactly as the envelope
                # requires; the case supplies only the options the graph does
                # not carry.
                frameworks=case_selections(case, pipeline),
            ),
            input_ref=InputRef.of(system_name=case.meta.title, sources=case.sources),
            pipeline=pipeline,
        )
    except GraphProducedNothing as exc:
        raise EvalRunError(f"{case.id}: {exc}") from exc
    if isinstance(result, Rejected):
        raise EvalRunError(f"{case.id}: {_rejection(result)}")
    return AnalysisRun(
        report=result, drafts=drafts, proposals=proposals, extraction=extraction
    )


def _rejection(rejected: Rejected) -> str:
    detail = "; ".join(f"{issue.code}: {issue.message}" for issue in rejected.issues)
    return f"the graph rejected the model: {detail}"


MODE_ENTRIES: dict[str, Entry] = {
    "extraction": ENTRY_EXTRACT_ONLY,
    "assertions": ENTRY_ASSERT_ONLY,
    "analysis": ENTRY_PREPARE,
    "end-to-end": ENTRY_EXTRACT,
    # #1003's arm mode: one arm's head, stopped at the catalog its endpoint is
    # scored on. It is the end-to-end graph with the lanes left off rather than
    # a route of its own, so an arm measured here ran the nodes an arm measured
    # end to end would have run.
    "heads": ENTRY_HEAD_ONLY,
}

#: The modes whose graph runs ``extract``, and so the ones whose sweep keeps
#: an emission beside its artifact. Read off the entries rather than listed,
#: so a mode added tomorrow answers by the entry it builds.
EXTRACTING_MODES: frozenset[str] = frozenset(
    mode
    for mode, entry in MODE_ENTRIES.items()
    if entry in {ENTRY_EXTRACT, ENTRY_EXTRACT_ONLY, ENTRY_HEAD_ONLY}
)

#: The modes whose graph ends in a :class:`Report`. ``extraction`` stops at the
#: validity gate and returns an :class:`ExtractionResult`, so a sweep of it has
#: no report to persist and says so rather than writing an empty file
#: ([#180](https://github.com/mstarks01/work-agent/issues/180)).
REPORTING_MODES: frozenset[str] = frozenset({"analysis", "end-to-end"})


def render_extraction(scores: Sequence[ExtractionScore]) -> None:
    """What an extraction sweep found, per case and then per attribute.

    The per-attribute split is the line this instrument exists for. An element
    recall of 1.00 says the extraction named the right things; it says nothing
    about whether it typed them, and a rule reads the type. So a sweep whose
    ``boundary.kind`` row reads 40% has found a real regression behind two
    perfect element numbers.

    Every number here is **non-gating**. A low agreement is a question to take
    to the source text, not a defect on its own
    ([#179](https://github.com/mstarks01/work-agent/issues/179)).
    """
    for score in scores:
        agreed = len(score.attributes) - len(score.differing)
        print(
            f"{score.case_id:<26} extraction recall {score.recall:.2f}"
            f"  endpoint {score.endpoint_recall:.2f}"
            f"  precision {score.precision:.2f}"
            f"  crossings {'match' if score.crossings_match else 'DIFFER'}"
            f"/{score.crossings_recall:.2f}"
            f"  fields {agreed}/{score.attributes_compared}"
            + (
                f" of {score.attributes_comparable}"
                if score.attributes_comparable
                else ""
            )
        )
    if not scores:
        return
    # Both readings, because the gap between them is the reading. A wide gap is
    # naming drift over the right architecture; a narrow one at a low number is
    # an extraction that found different things.
    strict = sum(score.recall for score in scores) / len(scores)
    endpoint = sum(score.endpoint_recall for score in scores) / len(scores)
    print(
        f"recall: {strict:.2f} strict, {endpoint:.2f} folding the flow label and"
        f" dropping the zones a claim cannot cite"
        f" — the gap is naming, not extraction (instrument, non-gating)"
    )
    if any(score.aliased for score in scores):
        sourced = sum(score.sourced_recall for score in scores) / len(scores)
        departures = sum(score.naming_departures for score in scores)
        print(
            f"  {sourced:.2f} crediting a name a reader ruled supported"
            f" — {departures} element(s) found and named another way, which is"
            f" naming policy rather than a component the extraction missed"
        )
    interactions = sum(score.interaction_recall for score in scores) / len(scores)
    print(
        f"  {interactions:.2f} counting interactions rather than pairs — a second"
        f" flow between one pair of endpoints is its own surface (#925)"
    )
    zones = sum(score.zone_recall for score in scores) / len(scores)
    print(
        f"zones: {zones:.2f} recall — no claim cites one, so this scores the"
        f" structure the crossings derive from rather than the identity rule"
    )
    partition = sum(score.zone_partition_agreement for score in scores) / len(scores)
    together = sum(score.same_zone_recall for score in scores) / len(scores)
    apart = sum(score.different_zone_agreement for score in scores) / len(scores)
    asked = sum(score.zone_pair_coverage for score in scores) / len(scores)
    print(
        f"  {partition:.2f} of element pairs are placed alike in both models —"
        f" the one zone figure a naming difference cannot reach (#925);"
        f" {together:.2f} of the pairs the reference puts together,"
        f" {apart:.2f} of the pairs it keeps apart, asked over {asked:.2f} of"
        f" its pairs (#961)"
    )
    zone_credits = sum(
        1 for score in scores for credit in score.aliased if _is_zone(credit.reference)
    )
    if zone_credits:
        sourced_zones = sum(score.sourced_zone_recall for score in scores) / len(scores)
        print(
            f"  {sourced_zones:.2f} crediting a zone a reader ruled supported"
            f" — {zone_credits} credit(s), which no other figure reads"
        )
    aligned = sum(s.aligned_recall for s in scores) / len(scores)
    aligned_interactions = sum(s.aligned_interaction_recall for s in scores) / len(
        scores
    )
    ambiguous = sum(len(s.alignment.ambiguous) for s in scores)
    print(
        f"aligned: {aligned:.2f} recall over one produced element per blessed"
        f" one, {aligned_interactions:.2f} over interactions — an exact ID, a"
        f" reader's alias, a flow's own label or its discriminators, and"
        f" nothing guessed; {ambiguous} ambiguity(ies) left unpaired (#961)"
    )
    actors = sum(s.actor_recall for s in scores) / len(scores)
    initiator = sum(s.initiator_recall for s in scores) / len(scores)
    dropped = sum(len(s.initiators_missing) for s in scores)
    print(
        f"actors: {actors:.2f} recall over external entities;"
        f" initiators {initiator:.2f}, {dropped} dropped — an element the text"
        f" describes by what it does rather than where it sits"
    )
    absent = sum(len(s.name_tokens_absent_from_source) for s in scores)
    extra = sum(len(s.extra) for s in scores)
    equivalent = sum(
        sum(1 for status in s.extra_status.values() if status == "equivalent")
        for s in scores
    )
    candidates = sum(len(s.same_type_unmatched_candidates) for s in scores)
    print(
        f"extra: {extra} element(s) the blessed model does not hold by ID —"
        f" {equivalent} aligned to a blessed element, {extra - equivalent}"
        f" unreviewed; precision counts every one as the model's error"
    )
    print(
        f"  {absent} carry a name token the source never uses, a word-presence"
        f" diagnostic and not an invention count; {candidates} are of a type"
        f" some blessed element is unaligned in, a candidate list and not a"
        f" rename count (#961)"
    )
    undrivable = [s.case_id for s in scores if not s.crossings_derivable]
    crossings = sum(s.crossings_recall for s in scores) / len(scores)
    print(
        f"crossings: {sum(s.crossings_match for s in scores)}/{len(scores)} match"
        f" by name, {crossings:.2f} recall and"
        f" {sum(s.crossings_precision for s in scores) / len(scores):.2f} precision"
        f" by endpoint pair"
        + (f"; underivable on {len(undrivable)}" if undrivable else "")
    )
    unbased = sum(len(score.unbased) for score in scores)
    read = sum(score.basis_coverage.measured for score in scores)
    stated = sum(score.basis_coverage.stated for score in scores)
    print(
        f"unbased controls: {unbased} stated with no word in the cited source,"
        f" out of {read} read of {stated} stated"
        f" — a diagnostic, gating nothing (analysis_service.basis)"
    )
    # Beside the agreement rather than under the flags, because the number it
    # qualifies is the agreement: a state comparison folds the mechanism away,
    # so an agreement on a value the source never echoes is an agreement about
    # nothing (#891).
    agreed_unbased = sum(score.state_agrees_unbased for score in scores)
    print(
        f"  of which {agreed_unbased} sit under a control state this run"
        f" *agreed* about, so that agreement reads a value its own source does"
        f" not support; {', '.join(sorted(STATE_UNCHECKED))} carry no basis"
        f" check at all"
    )
    totals = aggregate_attributes(scores)
    comparable = sum(score.attributes_comparable for score in scores)
    # The coverage clause only where there is a denominator to divide by: a
    # score built without one would otherwise read "over 22/0".
    over = f" over {totals['compared']}/{comparable} of the reference's fields"
    print(
        f"scored fields: {totals['agreed']}/{totals['compared']} agree"
        f" ({totals['agreement']:.0%}){over if comparable else ''}"
        f" (instrument, non-gating)"
    )
    states = [
        check
        for score in scores
        for check in score.attributes
        if check.attribute in STATE_REDUCED
    ]
    if states:
        agreed_states = sum(check.agrees for check in states)
        print(
            f"  of those, {agreed_states}/{len(states)} are a control *state*"
            f" agreeing — stated, absent or unverified, never the mechanism (#891)"
        )
    for name, split in totals["by_attribute"].items():
        print(
            f"  {name:28} {split['agreed']:5,}/{split['compared']:<7,}"
            f" {split['agreement']:.0%}"
        )


def artifact_extraction(scores: Sequence[ExtractionScore]) -> dict[str, Any]:
    """This instrument's artifact key.

    The per-case attribute numbers ride in ``mode_output`` beside the element
    ones; this is the sweep-wide fold, which is where a value the pipeline
    stopped producing shows up as a column rather than as one line per case
    (#195). ``None`` outside the extraction mode, so an unmeasured attribute set
    never reads as a fully agreeing one.
    """
    return {"attribute_aggregate": aggregate_attributes(scores) if scores else None}
