"""STRIDE's deterministic candidate rules.

A **Candidate** is not a finding. It is a mechanically evaluated condition over
the validated System Model — "this flow crosses a trust boundary and its
``authentication`` is unverified" — handed to the lane agent as something to look
at. Whether it is a threat, and what the attacker achieves, is the agent's
judgement and stays there.

These rules are STRIDE's, which is why they live in STRIDE's package. A rule
decides which lane sees a lead, and a lane is a framework's own unit. The
machinery that fires a rule and groups the results is neutral, and stays in
:mod:`analysis_service.candidates`. The retrieval tables that select a
**Reference Note** or a **Worked Case** key on the IDs below, and they are this
package's for the same reason.

There is a line this module does not cross: a candidate is never evidence. It
carries no severity, no attacker story and no claim that anything is wrong, and
it cannot become a
:class:`~analysis_service.frameworks.stride.record.Threat`. Nothing downstream
of the prompt reads a candidate at all. What grounds a finding is still the
submitter's words, an ``unknown`` attribute, or a derived crossing. A rule's
whole contribution is attention.

The representation is deliberately small. It is a tuple of
:class:`~analysis_service.candidates.Rule` values, each holding a rule ID, the
lane it belongs to, the question it puts to that agent, and a plain function
from model to matches. There is no rule DSL, no condition tree and no engine,
because the thing a maintainer needs to do most often is read one rule and
decide whether it is right, and a table of plain functions is the
representation that makes that cheapest. Adding a rule means writing a function
and appending to :data:`RULES`. A rule that reads an assertion predicate is also
named against it in :data:`PREDICATE_READERS`.

Rules fire on structure, never on prose. The attribute predicates come from
:mod:`analysis_service.analysis`, which reads a control attribute's leading
token and nothing else, so no rule here is a natural-language classifier wearing
a rule's clothes. A rule that needed to understand what ``"company SSO"`` implies
is a rule that belongs in the lane skill text instead.

Two rules may fire on one attribute from different lanes, and that is correct.
An unverified ``authentication`` on a boundary-crossing flow is a spoofing
question and an attribution question, and the two agents answer differently.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from types import MappingProxyType

from analysis_service.analysis import (
    comparable_asset_tags,
    control_state,
    crossing_facts,
    crossings_by_flow,
    inbound_flows,
    internet_exposed_elements,
    is_unverified,
    reachable_from,
    sensitive_assets,
    zone_kinds,
)
from analysis_service.assertions import (
    ABSENT,
    Assertion,
    AssertionCatalog,
    answer,
)
from analysis_service.candidates import Fact, Match, Rule, clip_fact
from analysis_service.frameworks import NoRule, PredicateReader
from analysis_service.system_model import DataFlow, SystemModel

__all__ = [
    "ENTERED_ZONE_KINDS",
    "LEFT_ZONE_KINDS",
    "PREDICATE_READERS",
    "RULES",
    "SHARED_DEPENDENCY_MIN",
]

# A shared dependency is an element two or more distinct elements flow into.
# Two rather than three: on the small models this service sees, the second
# dependent is already the point — one queue behind two producers is a
# chokepoint, and the agent decides whether it matters.
SHARED_DEPENDENCY_MIN = 2

# The zone kinds a crossing *enters* to be a privilege transition rather than a
# network hop. ``network`` and ``other`` are excluded: every boundary crossing
# is already surfaced to every agent, and a rule that fires on all of them adds
# no attention anywhere.
ENTERED_ZONE_KINDS = frozenset({"privilege", "tenant"})

# The zone kind a crossing *leaves* to be one as well, and the asymmetry is the
# point. Entering a ``privilege`` zone is the transition and leaving it is not:
# the authority is on the inside. A ``tenant`` zone separates parties rather
# than authority levels, so both directions cross something — inward hands
# authority to a party we do not control, and outward is a party we do not
# control reaching a zone we do. That second direction is the multi-tenancy
# escape, and reading only the destination makes it invisible.
LEFT_ZONE_KINDS = frozenset({"tenant"})

_clip = clip_fact


# --- Spoofing ---------------------------------------------------------------


def _unverified_boundary_auth(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    crossings = crossings_by_flow(model)
    for flow in model.data_flows:
        if flow.id not in crossings or not is_unverified(flow.authentication):
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                **crossing_facts(crossings[flow.id], flow),
            },
        )


def _unverified_external_caller(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    external = {
        entity.id
        for entity in model.external_entities
        if entity.kind == "external-system"
    }
    for flow in model.data_flows:
        if flow.source not in external or not is_unverified(flow.authentication):
            continue
        yield (
            (flow.id, flow.source),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "source_kind": "external-system",
            },
        )


def _second_factor_stated_absent(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """A flow the sources say needs no second factor.

    **The first rule here that reads the catalog, and it has to.**
    ``mfa-requirement`` projects into no graph attribute, so the fact reaches a
    reader as a row or not at all: a flow whose ``authentication`` reads
    ``"session cookie"`` is a stated control on every graph reading of it, and
    that the submitter also said there is no second factor is nowhere in the
    model. That is the substitution #926 was opened for — an explicit lack of
    MFA becoming a control — and it is a spoofing lead rather than a report of
    a contradiction, because the two facts agree: there is one factor.

    It asks :func:`~analysis_service.assertions.answer` rather than walking the
    rows, so which rows count is ``settled``'s rule and not a second one here.
    An absence is a positive claim about the world, so ``basis`` rides in the
    facts: what the source stated and what this service inferred are different
    leads, and the agent sees which it has.

    **A row about a principal reaches the flows that principal originates.**
    ``mfa-requirement`` takes a principal as well as an interaction, and over
    15 archived proposal/graph pairs 15 of 18 rows sat on the principal
    ``shopper accounts`` against 3 on a flow. A candidate names elements, so
    such a row reached nothing until the catalog could say which element the
    principal *is*: a settled ``represented-by``, which the sources must state
    rather than this service infer. The lead then lands on every flow out of
    that element, because that is where a single factor is presented.
    """
    for flow in model.data_flows:
        stated_on = answer(catalog, flow.id, "mfa-requirement").holding(ABSENT)
        held = stated_on or _principal_absence(catalog, flow.source)
        if not held:
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "second_factor": ABSENT,
                "second_factor_basis": held[0].basis,
                "second_factor_subject": "interaction" if stated_on else "principal",
            },
        )


def _principals_of(catalog: AssertionCatalog, element_id: str) -> tuple[str, ...]:
    """The principals the sources say this element stands for.

    The identification is read from the catalog and never guessed here: a
    principal reaches an element only through a settled ``represented-by``,
    whose own gate rule refuses a basis this service inferred. So a wrong lead
    needs a source that states the wrong thing, rather than a rule that decided
    two names looked alike. Every rule that places a fact about a principal
    asks this, so the identification has one reader.
    """
    return tuple(
        subject.id
        for subject in catalog.subjects
        if subject.type == "principal"
        and element_id
        in {
            entry.value
            for entry in answer(catalog, subject.id, "represented-by").settled
        }
    )


def _principal_absence(
    catalog: AssertionCatalog, element_id: str
) -> tuple[Assertion, ...]:
    """Rows saying a principal this element stands for needs no second factor."""
    return tuple(
        row
        for principal in _principals_of(catalog, element_id)
        for row in answer(catalog, principal, "mfa-requirement").holding(ABSENT)
    )


def _presented_on(catalog: AssertionCatalog, flow: DataFlow) -> frozenset[str]:
    """The credentials the sources say this flow presents.

    Read from ``credential-presented`` on the flow itself, and on each
    principal its source element stands for, because a principal presents its
    credential on every flow it originates. The second path is the one the
    sources usually take: they say who holds a key more often than which call
    carries it.
    """
    subjects = (flow.id, *_principals_of(catalog, flow.source))
    return frozenset(
        entry.value
        for subject in subjects
        for entry in answer(catalog, subject, "credential-presented").settled
    )


def _credential_rows(
    model: SystemModel,
    catalog: AssertionCatalog,
    weak: tuple[tuple[str, str], ...],
) -> Iterator[tuple[DataFlow, tuple[Assertion, ...]]]:
    """Each flow that presents a credential, with the rows that hold ``weak``.

    ``weak`` pairs a credential predicate with the value that makes it a lead.
    A flow that presents no such credential is not yielded.
    """
    for flow in model.data_flows:
        rows = tuple(
            row
            for credential in sorted(_presented_on(catalog, flow))
            for predicate, value in weak
            for row in answer(catalog, credential, predicate).holding(value)
        )
        if rows:
            yield flow, rows


def _stated(rows: tuple[Assertion, ...]) -> dict[str, Fact]:
    """What a lead from catalog rows tells the agent: each fact and its basis."""
    return {
        "stated": _clip(
            "; ".join(f"{row.subject} {row.predicate} {row.value}" for row in rows)
        ),
        "basis": ", ".join(sorted({row.basis for row in rows})),
    }


def _stated_on_flow(predicate: str) -> Callable[..., Iterator[Match]]:
    """A rule that fires where the sources say a flow lacks ``predicate``'s check.

    For the verification predicates, whose subject is the interaction itself,
    so the lead lands on the flow and both its endpoints with no
    identification to read. Only a stated absence fires: silence about a check
    is an ``unknown`` row, which is a question and not a lead.
    """

    def find(model: SystemModel, catalog: AssertionCatalog) -> Iterator[Match]:
        for flow in model.data_flows:
            rows = answer(catalog, flow.id, predicate).holding(ABSENT)
            if rows:
                yield (flow.id, flow.source, flow.destination), _stated(rows)

    return find


#: The credential facts that keep a credential valid after it leaks.
_STAYS_VALID = (
    ("credential-rotation", "not-rotated"),
    ("credential-expiry", "does-not-expire"),
    ("credential-revocation", ABSENT),
)


def _standing_credential(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """A flow that presents a credential the sources say stays valid."""
    for flow, rows in _credential_rows(model, catalog, _STAYS_VALID):
        yield (flow.id, flow.source, flow.destination), _stated(rows)


# --- Tampering --------------------------------------------------------------


def _unprotected_transit_crossing(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    crossings = crossings_by_flow(model)
    for flow in model.data_flows:
        if flow.id not in crossings or not is_unverified(flow.encryption_in_transit):
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "encryption_in_transit": _clip(flow.encryption_in_transit),
                "encryption_state": control_state(flow.encryption_in_transit),
                "protocol": _clip(flow.protocol),
                **crossing_facts(crossings[flow.id], flow),
            },
        )


def _unverified_write_to_store(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """A store an unverified caller may write, by the flow's own ``operations``.

    A :class:`~analysis_service.system_model.DataFlow`'s direction is who
    initiates, so by direction alone a service that only *reads* a database
    looks exactly like one that writes it — and the lead would tell an agent a
    read-only path can be tampered with. ``operations`` is the field that
    separates them, and a flow stating ``read`` is skipped.

    ``unknown`` still fires, and that is the point of raising a lead rather than
    a finding: nobody said what the connection carries, so a write cannot be
    ruled out. The facts carry the value, so the agent argues from what the
    model says rather than from the rule's name.

    **This deliberately loses three corpus triggers, one of them a must-find,
    and the losses name the next rule rather than argue against this one.** All
    three are the same shape: a store the model shows only being *read* — case
    02's device registry, case 03's Airflow metadata database, case 04's feature
    store — where the reference is about an attacker writing it over a path
    nobody drew. Reading a read as a write would credit the lead by accident,
    for a fact this rule cannot see.

    What those references actually rest on is that the store is unauthenticated
    *at all*, so anyone who reaches the network writes it. That is authority
    rather than operations, the model carries no field for it, and inventing one
    here would be the same mistake in the other direction. It is the next
    structured fact, in the order #671 finding 11 asks for: facts first, then
    the rules that read them.
    """
    stores = {store.id: store for store in model.data_stores}
    for flow in model.data_flows:
        store = stores.get(flow.destination)
        if store is None or not is_unverified(flow.authentication):
            continue
        if flow.operations == "read":
            continue
        yield (
            (flow.id, store.id),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "operations": flow.operations,
                "data_classification": _clip(store.data_classification),
            },
        )


# --- Repudiation ------------------------------------------------------------


def _unattributable_action(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """Unverified callers writing into an element that holds a graded asset."""
    by_id = {element.id: element for element in model.elements()}
    for flow in model.data_flows:
        destination = by_id.get(flow.destination)
        if destination is None or not is_unverified(flow.authentication):
            continue
        assets = sensitive_assets(destination)
        if not assets:
            continue
        yield (
            (flow.id, destination.id),
            {
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                "destination_assets": ", ".join(assets),
            },
        )


def _shared_credential(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """A flow that presents a credential the sources say more than one holds."""
    for flow, rows in _credential_rows(
        model, catalog, (("credential-sharing", "shared"),)
    ):
        yield (flow.id, flow.source, flow.destination), _stated(rows)


def _record_names_intermediary(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """A component whose records the sources say name no principal who acted.

    ``intermediary`` means a record names only the component that passed the
    request on, and ``absent`` that no record is kept. The lead covers the
    component and every flow into it, because the principal the record loses
    is at the far end of one of those flows.
    """
    for element in model.elements():
        rows = tuple(
            row
            for value in ("intermediary", ABSENT)
            for row in answer(catalog, element.id, "record-attribution").holding(value)
        )
        if rows:
            writers = tuple(flow.id for flow in inbound_flows(model, element.id))
            yield (element.id, *writers), _stated(rows)


# --- Information disclosure -------------------------------------------------


def _unprotected_sensitive_transit(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """An unprotected flow whose own tags, or whose endpoints', name an asset.

    **The flow's own ``assets`` are read first, because they are the closest
    statement of what the channel carries.** Reading only the endpoints made the
    rule miss a graded flow between two ungraded elements: case 02's telemetry
    publish tags ``pii`` while the node and the gateway tag nothing, and its
    ``must-find`` disclosure reference drew no lead at all. An endpoint's tags
    stay in the union — a store holding ``financial`` says what a flow reaching
    it moves, and a submitter who tags the store and not the flow has still said
    it.
    """
    by_id = {element.id: element for element in model.elements()}
    for flow in model.data_flows:
        if not is_unverified(flow.encryption_in_transit):
            continue
        carriers = [flow, by_id.get(flow.source), by_id.get(flow.destination)]
        assets = sorted(
            {
                asset
                for carrier in carriers
                if carrier is not None
                for asset in sensitive_assets(carrier)
            }
        )
        if not assets:
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "encryption_in_transit": _clip(flow.encryption_in_transit),
                "encryption_state": control_state(flow.encryption_in_transit),
                # Named for the union it now holds. It was `endpoint_assets`
                # while the endpoints were the only side read, and a fact key
                # that says where a value came from has to keep saying it.
                "assets_in_transit": ", ".join(assets),
            },
        )


def _store_at_rest_unverified(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    for store in model.data_stores:
        if not is_unverified(store.encryption_at_rest):
            continue
        yield (
            (store.id,),
            {
                "encryption_at_rest": _clip(store.encryption_at_rest),
                "encryption_state": control_state(store.encryption_at_rest),
                "data_classification": _clip(store.data_classification),
                "assets": ", ".join(store.assets),
            },
        )


# --- Denial of service ------------------------------------------------------


def _internet_exposed_process(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    for process in internet_exposed_elements(model):
        yield (
            (process.id,),
            {
                "exposure": "internet-facing",
                "inbound_flows": len(inbound_flows(model, process.id)),
                "reachable_elements": len(reachable_from(model, process.id)),
            },
        )


def _shared_dependency(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """Elements several distinct callers flow into: one stall stalls them all.

    Convergence is arithmetic over the graph, and it is the whole trigger. The
    ``availability-critical`` tag names the same idea and a model guesses it:
    over the thirteen corpus models this rule fires on 20 elements, a
    consequence tag sits on 12, and the two agree about 3 (#877). So the facts
    carry the count, and ``assets`` reports the element's tags through
    :func:`~analysis_service.analysis.comparable_asset_tags`, which the
    extraction scorer reads too. Every shipped tag names what the element
    holds, because #877 retired the two that did not; what stops here is a
    guess being handed to the agent as a lead.
    """
    for element in model.elements():
        flows = inbound_flows(model, element.id)
        callers = {flow.source for flow in flows}
        if len(callers) < SHARED_DEPENDENCY_MIN:
            continue
        yield (
            (element.id, *sorted(callers)),
            {
                "inbound_flows": len(flows),
                "distinct_callers": len(callers),
                "assets": ", ".join(comparable_asset_tags(element.assets)),
            },
        )


# --- Elevation of privilege -------------------------------------------------


def _privilege_zone_crossing(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """Crossings that change authority, and which way the flow runs.

    One candidate per crossing at most. A flow leaving one tenant zone for
    another qualifies twice over, and the destination is the end that decides
    it — the facts carry both zones' kinds either way, so the agent loses
    nothing that a second candidate would have told it.
    """
    kinds = zone_kinds(model)
    # A crossing's flow ID names a Data Flow in the model it was derived from,
    # so this lookup resolves. Indexed by type rather than reached for with a
    # defaulted ``getattr``: the default would answer a missing flow with the
    # empty string, whose ``control_state`` is ``stated`` — a rule reporting a
    # control the model never named, which is the one thing this module may not
    # do. A flow that somehow does not resolve yields no candidate instead.
    flows = {flow.id: flow for flow in model.data_flows}
    for crossing in model.boundary_crossings():
        # The one crossing-keyed rule that skips an undecidable crossing, and
        # its premise is why: this rule fires on what the two zones *are*, and
        # an unknown zone has no kind to read. An undecidable crossing confers
        # eligibility for analysis and never a privilege transition, so firing
        # here would assert the authority change the crossing cannot establish
        # (ADR 0039 rule 4). Stated rather than left to the empty kind that
        # falls through both sets below.
        if not crossing.decided:
            continue
        source_kind = kinds.get(crossing.source_zone, "")
        destination_kind = kinds.get(crossing.destination_zone, "")
        if destination_kind in ENTERED_ZONE_KINDS:
            zone, direction = crossing.destination_zone, "into"
        elif source_kind in LEFT_ZONE_KINDS:
            zone, direction = crossing.source_zone, "out-of"
        else:
            continue
        flow = flows.get(crossing.flow_id)
        if flow is None:
            continue
        authentication = flow.authentication
        yield (
            (crossing.flow_id, zone),
            {
                "direction": direction,
                "zone": zone,
                "zone_kind": kinds[zone],
                "source_zone_kind": source_kind,
                "destination_zone_kind": destination_kind,
                "authentication": _clip(authentication),
                "authentication_state": control_state(authentication),
                **crossing_facts(crossing, flow),
            },
        )


def _inbound_from_exposed_process(
    model: SystemModel, catalog: AssertionCatalog
) -> Iterator[Match]:
    """What an internet-facing process can command in another zone.

    The structural half of a foothold: whoever holds the exposed process holds
    every authority it exercises across a boundary. The rule states the reach;
    whether the authority is excessive is the agent's call.
    """
    exposed = {process.id for process in internet_exposed_elements(model)}
    crossings = crossings_by_flow(model)
    for flow in model.data_flows:
        if flow.source not in exposed or flow.id not in crossings:
            continue
        yield (
            (flow.id, flow.source, flow.destination),
            {
                "source_exposure": "internet-facing",
                "authentication": _clip(flow.authentication),
                "authentication_state": control_state(flow.authentication),
                **crossing_facts(crossings[flow.id], flow),
            },
        )


RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="spoofing-unverified-boundary-auth",
        lane="spoofing",
        question=(
            "This flow crosses a trust boundary and states no verified caller"
            " identity. Who in the source zone can originate it, and as whom"
            " would the destination treat them?"
        ),
        find=_unverified_boundary_auth,
    ),
    Rule(
        rule_id="spoofing-unverified-external-caller",
        lane="spoofing",
        question=(
            "An external system originates this flow and its authentication is"
            " unverified. Can anyone who learns the endpoint impersonate that"
            " party, and what does the receiver do with the events?"
        ),
        find=_unverified_external_caller,
    ),
    Rule(
        rule_id="spoofing-second-factor-stated-absent",
        lane="spoofing",
        question=(
            "The sources say this interaction needs no second factor, and the"
            " graph shows the one it does use. What can an attacker who holds"
            " that single factor do, and what would a second one have stopped?"
        ),
        find=_second_factor_stated_absent,
    ),
    Rule(
        rule_id="spoofing-origin-stated-unverified",
        lane="spoofing",
        question=(
            "The sources say the receiver of this flow does not check who"
            " supplied what it takes. Who else can supply it, and as whose"
            " does the receiver then treat what arrives?"
        ),
        find=_stated_on_flow("origin-verification"),
    ),
    Rule(
        rule_id="spoofing-standing-credential",
        lane="spoofing",
        question=(
            "The sources say a credential this flow presents stays valid: it"
            " is not rotated, does not expire, or cannot be withdrawn. Who"
            " could come to hold it, and for how long could they act as its"
            " owner?"
        ),
        find=_standing_credential,
    ),
    Rule(
        rule_id="tampering-unprotected-transit-crossing",
        lane="tampering",
        question=(
            "This flow crosses a trust boundary with no verified transport"
            " protection. What could an on-path attacker alter in it, and what"
            " acts on the altered data?"
        ),
        find=_unprotected_transit_crossing,
    ),
    Rule(
        rule_id="tampering-unverified-write-to-store",
        lane="tampering",
        question=(
            "This flow may write into a data store without a verified caller"
            " identity — read `operations` for what the model says it carries,"
            " and treat `unknown` as a question rather than as a write. What"
            " can be written, and what downstream reader trusts what it finds"
            " there?"
        ),
        find=_unverified_write_to_store,
    ),
    Rule(
        rule_id="tampering-signature-stated-unverified",
        lane="tampering",
        question=(
            "The sources say the receiver of this flow does not check who"
            " signed what it takes. Who can substitute or alter it before it"
            " arrives, and what runs or trusts it afterwards?"
        ),
        find=_stated_on_flow("signature-verification"),
    ),
    Rule(
        rule_id="tampering-content-stated-unvalidated",
        lane="tampering",
        question=(
            "The sources say the receiver of this flow does not check the"
            " contents of what it takes, beyond their format. Who can put"
            " well-formed but false values into it, and what acts on them?"
        ),
        find=_stated_on_flow("content-validation"),
    ),
    Rule(
        rule_id="repudiation-unattributable-action",
        lane="repudiation",
        question=(
            "An unverified caller acts on an element holding a graded asset."
            " What record names the actor, and would it survive a dispute?"
        ),
        find=_unattributable_action,
    ),
    Rule(
        rule_id="repudiation-shared-credential",
        lane="repudiation",
        question=(
            "The sources say more than one principal holds a credential this"
            " flow presents. Could the record name which holder acted, and"
            " who could deny an action taken with it?"
        ),
        find=_shared_credential,
    ),
    Rule(
        rule_id="repudiation-record-names-intermediary",
        lane="repudiation",
        question=(
            "The sources say this component's records name only the component"
            " that passed a request on, or keep none. Which principal could"
            " deny an action here, and what else would contradict them?"
        ),
        find=_record_names_intermediary,
    ),
    Rule(
        rule_id="information-disclosure-unprotected-sensitive-transit",
        lane="information-disclosure",
        question=(
            "This flow has no verified transport protection and an endpoint"
            " holds a graded asset. Who can observe the channel, and what"
            " exactly would they read?"
        ),
        find=_unprotected_sensitive_transit,
    ),
    Rule(
        rule_id="information-disclosure-destination-stated-unverified",
        lane="information-disclosure",
        question=(
            "The sources say the sender of this flow does not check that it"
            " reaches the right place. Who could receive it instead, and what"
            " would they read?"
        ),
        find=_stated_on_flow("destination-verification"),
    ),
    Rule(
        rule_id="information-disclosure-store-at-rest-unverified",
        lane="information-disclosure",
        question=(
            "This store states no verified protection at rest. Who reaches the"
            " storage layer beneath the application, and what is in it?"
        ),
        find=_store_at_rest_unverified,
    ),
    Rule(
        rule_id="denial-of-service-internet-exposed-process",
        lane="denial-of-service",
        question=(
            "This process is reachable from the internet. What does an"
            " unauthenticated request cost it, and what else stalls when it"
            " saturates?"
        ),
        find=_internet_exposed_process,
    ),
    Rule(
        rule_id="denial-of-service-shared-dependency",
        lane="denial-of-service",
        question=(
            "Several callers depend on this element. If it slows or stops,"
            " which of them fail, and does anything shed load or degrade?"
        ),
        find=_shared_dependency,
    ),
    Rule(
        rule_id="elevation-of-privilege-privilege-zone-crossing",
        lane="elevation-of-privilege",
        question=(
            "This flow crosses a privilege or tenant boundary; `direction`"
            " says whether it runs into the named zone or out of it. Going"
            " in, what enforces the transition and what would the caller"
            " command if nothing did? Coming out of a tenant zone, what does"
            " a party we do not control reach on our side?"
        ),
        find=_privilege_zone_crossing,
    ),
    Rule(
        rule_id="elevation-of-privilege-inbound-from-exposed-process",
        lane="elevation-of-privilege",
        question=(
            "An internet-facing process exercises authority across a boundary."
            " What does whoever compromises it inherit, and is that authority"
            " wider than the process's own job?"
        ),
        find=_inbound_from_exposed_process,
    ),
)


#: Every assertion predicate with no graph field, and the rule here that reads
#: it or why none does. A fact of these kinds reaches a lane as an evidence row
#: whatever this table says; an entry decides only whether it is also a lead.
PREDICATE_READERS: Mapping[str, PredicateReader] = MappingProxyType(
    {
        "mfa-requirement": "spoofing-second-factor-stated-absent",
        "origin-verification": "spoofing-origin-stated-unverified",
        "signature-verification": "tampering-signature-stated-unverified",
        "destination-verification": (
            "information-disclosure-destination-stated-unverified"
        ),
        "credential-sharing": "repudiation-shared-credential",
        "record-attribution": "repudiation-record-names-intermediary",
        "content-validation": "tampering-content-stated-unvalidated",
        "credential-rotation": "spoofing-standing-credential",
        "credential-expiry": "spoofing-standing-credential",
        "credential-revocation": "spoofing-standing-credential",
        "credential-lifetime": NoRule(
            "the value is free text, and a rule reads structure, never prose;"
            " whether a window is long is the lane's judgement"
        ),
        "credential-custody": NoRule(
            "the value is free text naming where a credential is kept, and a"
            " rule reads structure, never prose"
        ),
        "authorization-grant": NoRule(
            "the resource a grant covers is often something the graph has no"
            " element for, so a rule cannot place it; the lane reads the row"
            " (QA-2026-09-22-02)"
        ),
        "administrative-authority": NoRule(
            "it states which component a principal administers, which is a"
            " role and not a weakness, so there is nothing for a lead to ask"
        ),
        "tenant-ownership": NoRule(
            "it states which party controls a component, which is an owner"
            " and not a weakness; a crossing into a tenant zone already leads"
            " the elevation lane"
        ),
        "represented-by": NoRule(
            "it identifies the element a principal is, and the rules read it"
            " to place every other fact about that principal"
        ),
    }
)
