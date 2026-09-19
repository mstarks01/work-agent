"""Mechanical validity gate for the System Model.

The well-formedness rules: unique typed IDs deterministic from type+name,
referential integrity of flow endpoints and ``trust_zone``, at least one trust
zone, legal enum values (including the config-extendable asset-tag vocabulary),
and assumptions that name a real element, a real attribute, and an attribute
holding an inference. The admission cap on model size
(:data:`MAX_ELEMENTS`) is enforced here because this is the one gate every
extraction passes through before any category-agent spend.

ID derivation lives in code: callers passing ``normalize_ids`` to
:func:`parse_and_validate` get their IDs canonicalized before the gate runs, so
``id-mismatch`` is unreachable from the extraction pipeline. The rule stays
enforced here for models that arrive hand-authored.

Errors are structured (:class:`ValidationIssue`) so the repair pass can feed
them back to the extraction agent verbatim. Category agents only ever see models for
which :func:`validate` returns an empty list. On any failure the gate reports
and denies — never silently auto-repairs.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from analysis_service.analysis import CONTROL_ATTRIBUTES, control_state, leading_word
from analysis_service.grounding import normalize, verify_normalized
from analysis_service.references import canonical
from analysis_service.system_model import (
    CORE_ASSET_TAGS,
    ELEMENT_GROUPS,
    UNKNOWN,
    Assumption,
    Element,
    SystemModel,
    assumable_attributes,
    derive_element_id,
    duplicate_ids,
    normalize_element_ids,
)

#: Every way an extraction is refused. All but one are this gate's own rules;
#: ``duplicate-ref`` belongs to the compact transport
#: (:mod:`analysis_service.compact`) and is reported in this shape because the
#: repair pass reads one list of issues, not one per stage it came from.
IssueCode = Literal[
    "schema",
    "duplicate-id",
    "duplicate-ref",
    "id-mismatch",
    "invalid-reference",
    "no-trust-zones",
    "illegal-asset-tag",
    "too-many-elements",
    "unverifiable-excerpt",
    "missing-citation",
    "assumption-on-unknown",
    "blank-control",
    "ambiguous-control",
]

# Admission cap on model size. Deliberately loose: it is a
# blast-radius guard, not a quality threshold. Every artifact downstream
# scales with element count — every lane agent reads the whole model, and the
# critic reads every draft they produce in one pass — so an unbounded model
# means unbounded spend and a critic dedupeing hundreds of drafts with no
# error anywhere. Rejecting here costs one ``len()`` and happens before any
# category-agent call. Element count rather than a token estimate because it is the
# number a user can act on: "split the system" is advice they can follow.
#
# Where quality actually decays with model size is unmeasured — the golden
# corpus is 8-20 elements by design — so this number is a guard awaiting
# evidence, not a calibrated limit.
MAX_ELEMENTS = 150


class ValidationIssue(BaseModel):
    """One structured validity-gate failure, addressed to the repair pass."""

    model_config = ConfigDict(extra="forbid")

    code: IssueCode
    message: str
    element_id: str | None = None
    field: str | None = None

    @property
    def is_citation(self) -> bool:
        """Did this issue come from the one gate rule that reads the sources?

        :func:`_citation_issues` is the only rule taking data from outside the
        model, and it is the only rule that can name either of these fields. A
        caller that supplies no sources can never see one.

        The property exists so a caller asking *which half of the gate failed*
        reads the answer here rather than re-deriving it from a code. The eval
        harness asks: a model citing a quote that is not in its source is an
        extraction the repair pass exists to fix, and grading that is a
        different job from reporting a malformed model.
        """
        return self.field in CITATION_FIELDS


#: The fields only :func:`_citation_issues` writes. Named beside the rule so the
#: rule and :attr:`ValidationIssue.is_citation` cannot drift apart;
#: ``tests/test_validation.py`` drives the gate both ways and holds them to it.
CITATION_FIELDS: frozenset[str] = frozenset({"source_label", "source_excerpt"})


def allowed_asset_tags(extra_asset_tags: Collection[str] = ()) -> frozenset[str]:
    """The controlled asset vocabulary: core tags plus config extensions."""
    return CORE_ASSET_TAGS | frozenset(extra_asset_tags)


def validate(
    model: SystemModel,
    extra_asset_tags: Collection[str] = (),
    max_elements: int = MAX_ELEMENTS,
    sources: Mapping[str, str] = MappingProxyType({}),
) -> list[ValidationIssue]:
    """Run every gate rule; an empty result means the model is ready for analysis.

    The size cap is checked first and returns alone: a model too large to
    analyze cannot be made acceptable by fixing its IDs, and reporting the
    other few hundred issues alongside it would bury the one that matters.
    """
    elements = model.elements()
    if len(elements) > max_elements:
        return [
            ValidationIssue(
                code="too-many-elements",
                message=f"the model has {len(elements)} elements, over the"
                f" {max_elements}-element limit; split the system into"
                " smaller models and submit them separately",
            )
        ]

    issues: list[ValidationIssue] = []
    legal_tags = allowed_asset_tags(extra_asset_tags)

    for element_id, count in duplicate_ids(elements).items():
        issues.append(
            ValidationIssue(
                code="duplicate-id",
                message=f"element ID {element_id!r} is used by {count} elements;"
                " IDs must be unique within one System Model",
                element_id=element_id,
                field="id",
            )
        )

    zoned_ids = {
        element.id
        for element in (*model.external_entities, *model.processes, *model.data_stores)
    }
    boundary_ids = {boundary.id for boundary in model.trust_boundaries}

    for element in elements:
        try:
            expected = derive_element_id(element)
        except ValueError:
            expected = None
        if expected is not None and element.id != expected:
            issues.append(
                ValidationIssue(
                    code="id-mismatch",
                    message=f"element ID {element.id!r} is not the deterministic"
                    f" typed slug for its type and name; expected {expected!r}",
                    element_id=element.id,
                    field="id",
                )
            )

        for tag in element.assets:
            if tag not in legal_tags:
                issues.append(
                    ValidationIssue(
                        code="illegal-asset-tag",
                        message=f"asset tag {tag!r} is not in the controlled"
                        f" vocabulary {sorted(legal_tags)}",
                        element_id=element.id,
                        field="assets",
                    )
                )

        issues.extend(_blank_control_issues(element))
        issues.extend(_ambiguous_control_issues(element))

    for zoned in model.zoned_elements():
        # The unknown sentinel is a placement the sources do not make, which
        # ADR 0039 admits: a component the sources place is placed, and one
        # they do not is unplaced. Any other value must still name a boundary
        # this model holds, so a typo is caught as it always was.
        if zoned.trust_zone == UNKNOWN:
            continue
        if zoned.trust_zone not in boundary_ids:
            issues.append(
                ValidationIssue(
                    code="invalid-reference",
                    message=f"trust_zone {zoned.trust_zone!r} does not reference"
                    " an existing Trust Boundary element",
                    element_id=zoned.id,
                    field="trust_zone",
                )
            )

    for flow in model.data_flows:
        for field in ("source", "destination"):
            endpoint = getattr(flow, field)
            if endpoint not in zoned_ids:
                issues.append(
                    ValidationIssue(
                        code="invalid-reference",
                        message=f"flow {field} {endpoint!r} does not reference an"
                        " existing External Entity, Process, or Data Store",
                        element_id=flow.id,
                        field=field,
                    )
                )

    if not model.trust_boundaries:
        issues.append(
            ValidationIssue(
                code="no-trust-zones",
                message="the model declares no Trust Boundary; at least one"
                " trust zone is required",
            )
        )

    by_id = {element.id: element for element in elements}
    for assumption in model.assumptions:
        issues.extend(_assumption_issues(assumption, by_id))

    issues.extend(_citation_issues(elements, sources))
    return issues


def _blank_control_issues(element: Element) -> list[ValidationIssue]:
    """Every control attribute this element leaves blank, addressed to repair.

    The free-text control fields carry a maximum length and no minimum, so an
    empty ``authentication`` passes the shape gate. Read as ``stated`` by
    :func:`~analysis_service.analysis.control_state`, it would suppress the
    candidate rules that ask about a missing control and the evidence row
    beside them. A model whose gate said *ready*
    was quietly asserting a control nobody described.

    ``unknown`` is the value ``prompts/extract.md`` asks for, and it is not the
    same fact as an empty string, so this reports rather than rewrites: the
    repair pass writes the sentinel and the source of the blank stays visible.
    ``control_state`` now reads a blank as ``unverified`` whatever happens here,
    so the two answers agree if one arrives anyway.

    Walks :data:`~analysis_service.analysis.CONTROL_ATTRIBUTES` — the one
    registry of which attributes are controls — rather than testing every string
    field: ``technology`` and ``data_description`` are prose about the element,
    and neither states a control whose absence is a finding.
    """
    return [
        ValidationIssue(
            code="blank-control",
            message=f"{attribute!r} is empty; a control nobody described is"
            f" {UNKNOWN!r}, which is a value an analyst can act on, and an"
            " empty string is not",
            element_id=element.id,
            field=attribute,
        )
        for attribute in CONTROL_ATTRIBUTES
        if attribute in type(element).model_fields
        and not str(getattr(element, attribute)).strip()
    ]


#: Leading words that read as an absence and are not the absence sentinel. A
#: control value starting with one — "no MFA on the password login", "not
#: stated", "without TLS" — is read as *stated* by the leading-token rule,
#: because the rest of the sentence usually names a mechanism that exists;
#: but the same words also open a sentence that means the control is absent
#: or was never mentioned. The two readings differ in whether a candidate
#: rule fires, so the gate refuses the shape and the repair pass picks one.
AMBIGUOUS_CONTROL_LEADS: frozenset[str] = frozenset(
    {"no", "not", "without", "never", "neither", "nor", "n/a", "na", "nil"}
)


def _ambiguous_control_issues(element: Element) -> list[ValidationIssue]:
    """Every control whose leading word is a negation other than ``none``.

    The extraction contract gives a control three shapes: ``unknown`` for a
    control the input never mentions, ``none`` for one it says is not there,
    and otherwise the mechanism, named first. A value leading with ``no`` or
    ``not`` is none of the three and is read differently by a person and by
    :func:`~analysis_service.analysis.control_state` (#675 D02). Refused here
    rather than reclassified: which of the three the writer meant is a fact
    about the input, and the repair pass has the input in front of it. The
    corpus and every archived model were checked and carry no such value.
    """
    issues = []
    for attribute in CONTROL_ATTRIBUTES:
        if attribute not in type(element).model_fields:
            continue
        # Through the same reader ``control_state`` uses for its two
        # sentinels, so "where a word ends" has one answer: ``no-mfa`` and
        # ``no/unknown`` open with ``no`` here exactly as ``none;`` opens with
        # ``none`` there.
        lead = leading_word(str(getattr(element, attribute)), AMBIGUOUS_CONTROL_LEADS)
        if lead is not None:
            issues.append(
                ValidationIssue(
                    code="ambiguous-control",
                    message=f"{attribute!r} opens with {lead!r}: write {UNKNOWN!r} for a"
                    " control the input never mentions, 'none' for one it says is not"
                    " there, and otherwise name the mechanism first",
                    element_id=element.id,
                    field=attribute,
                )
            )
    return issues


def _states_nothing(value: object) -> bool:
    """Whether an attribute holds no value for an assumption to have inferred.

    Two shapes, because the schema has two. ``assets`` is a list and states
    nothing when it is empty — ``extract.md`` rule 6 makes an empty list the
    honest answer where no tag applies, exactly as ``unknown`` is for a string.
    Every other attribute is a string, and
    :func:`~analysis_service.analysis.control_state` is the classifier the rest
    of the repo reads it with, so a decorated hedge is refused with the bare
    word.
    """
    if isinstance(value, list):
        return not value
    return control_state(str(value)) == "unverified"


def _assumption_issues(
    assumption: Assumption, by_id: Mapping[str, Element]
) -> list[ValidationIssue]:
    """One assumption resolves to a real element, a real attribute, and a value.

    Three rules, and the third is the one that carries the meaning. The pair
    has to *resolve*: an element that exists, and an attribute that element
    actually declares — ``assumable_attributes`` is the registry, derived from
    the schema, so a field added to an element type is covered here the day it
    lands and nothing lists the names twice.

    Then the attribute has to hold an inference. An **Assumption** is what
    extraction inferred with a stated basis, which makes an assumption on an
    attribute still reading ``unknown`` a contradiction of its own record:
    nothing was inferred, so there is nothing for the basis to support.
    ``CONTEXT.md`` draws the same line — a hedge somebody voiced is
    ``unknown`` and goes in ``notes``, never here — and this is where the
    prompt's rule stops being wording and becomes a rule.

    :func:`~analysis_service.analysis.control_state` classifies rather than an
    equality test against the sentinel, so a decorated hedge
    (``"unknown; possibly a shared group account"``) is refused with the bare
    word. A control the input states is *absent* is left alone: "there is no
    authentication here" is a fact a model can infer, and refusing it would
    delete the one inference most worth recording.
    """
    element = by_id.get(assumption.element_id)
    if element is None:
        return [
            ValidationIssue(
                code="invalid-reference",
                message=f"assumption references element {assumption.element_id!r},"
                " which does not exist in the model",
                element_id=assumption.element_id,
                field="assumptions",
            )
        ]
    legal = assumable_attributes(element)
    if assumption.attribute not in legal:
        return [
            ValidationIssue(
                code="invalid-reference",
                message=f"assumption names attribute {assumption.attribute!r},"
                f" which {assumption.element_id!r} does not carry; it declares"
                f" {list(legal)}",
                element_id=assumption.element_id,
                field="assumptions",
            )
        ]
    if _states_nothing(getattr(element, assumption.attribute)):
        return [
            ValidationIssue(
                code="assumption-on-unknown",
                message=f"assumption records an inference for"
                f" {assumption.attribute!r} on {assumption.element_id!r}, but"
                " that attribute is still unknown; an unknown value is not an"
                " inference and needs no assumption",
                element_id=assumption.element_id,
                field="assumptions",
            )
        ]
    return []


def _citation_issues(
    elements: Collection[Element], sources: Mapping[str, str]
) -> list[ValidationIssue]:
    """The traceability chain exists, resolves *and* leads somewhere, or the model fails.

    Three rungs, and the first one is what lets the other two mean anything.
    **An element with no excerpt cites nothing**, so it asserts its facts on the
    model's own authority; ``prompts/extract.md`` rule 7 asks for an excerpt on
    every element, and this is the rung that holds it. Skipping an empty excerpt
    rather than refusing it makes erasure the cheapest way through the whole
    gate — quieter than a wrong citation, because a rule that only inspects the
    citations it is given says nothing about the ones nobody wrote. It also
    takes :func:`~analysis_service.basis.stated_controls` with it, since that
    diagnostic passes over an element whose label resolves to nothing *on the
    stated grounds that this rule refuses that shape* (#925).

    Excerpt and label are then **coupled**: a quote with no label cites nothing,
    and a label naming a source the job never carried asserts a chain that is not
    there — which is worse than no citation at all, because a reader who
    follows it finds a source that does not exist. Set membership is
    mechanical, so it belongs here rather than in a prompt.

    The label resolving is only half the chain. The other half — is the quoted
    span actually *in* that source — is the same question
    :mod:`analysis_service.grounding` answers for a threat's ``quote`` ground,
    asked here of the excerpt that ties an element to the words it came from.
    The two are one question, asked of both.
    That ladder was calibrated on exactly this data — "the 12 corpus cases' 206
    element excerpts" — at **0 false rejections in 206**, so turning it on here
    is the one rung in this repo whose cost was measured before it was spent.

    Failing closed is affordable because extraction has the ``repair`` pass: an
    excerpt the gate cannot find is cited back to the transcriber that wrote
    it, with the source still in front of it. That is the opposite of the draft
    seam, where the same failure has nowhere to go.

    This is the one gate rule taking data from outside the model. Where no
    sources are supplied none of the three rungs runs: a hand-authored model
    checked without a job has nothing to check against, and inventing it would
    fail every such model on a citation that is not wrong. **Presence rides
    with them** rather than running always, because the population this rule
    exists to hold is a model that came out of a job — the message names the
    job's labels, which is the repair the writer can act on, and a model with
    no job has no such list. A source carried with empty text skips the
    verbatim rung alone, for the same reason.
    """
    if not sources:
        return []

    # Folded once, then reused across every element's excerpt — the gate admits
    # up to ``MAX_ELEMENTS`` of them against the same handful of sources.
    folded = {label: normalize(text) for label, text in sources.items()}
    issues: list[ValidationIssue] = []
    for element in elements:
        if not element.source_excerpt:
            issues.append(
                ValidationIssue(
                    code="missing-citation",
                    message="the element carries no source_excerpt, so nothing"
                    " ties it to the submitted text; quote the shortest span of"
                    f" one of this job's sources {sorted(sources)} that names it",
                    element_id=element.id,
                    field="source_excerpt",
                )
            )
            continue
        if not element.source_label:
            issues.append(
                ValidationIssue(
                    code="invalid-reference",
                    message="source_excerpt is present with no source_label naming"
                    " the source it was quoted from",
                    element_id=element.id,
                    field="source_label",
                )
            )
            continue
        # Snapped rather than matched exactly, for the reason element IDs are
        # derived: which spelling of the job's label arrived is mechanical, and
        # ``repair``'s one pass is too scarce to spend on a re-cased word.
        label = canonical(element.source_label, sources)
        if not label:
            issues.append(
                ValidationIssue(
                    code="invalid-reference",
                    message=f"source_label {element.source_label!r} does not name one"
                    f" of this job's sources {sorted(sources)}",
                    element_id=element.id,
                    field="source_label",
                )
            )
        elif sources[label] and not verify_normalized(
            element.source_excerpt, folded[label]
        ):
            issues.append(
                ValidationIssue(
                    code="unverifiable-excerpt",
                    message=f"source_excerpt {element.source_excerpt!r} is not found"
                    f" in the source it cites, {label!r}",
                    element_id=element.id,
                    field="source_excerpt",
                )
            )
    return issues


def parse_and_validate(
    data: object,
    extra_asset_tags: Collection[str] = (),
    max_elements: int = MAX_ELEMENTS,
    normalize_ids: bool = False,
    sources: Mapping[str, str] = MappingProxyType({}),
) -> tuple[SystemModel | None, list[ValidationIssue]]:
    """Parse raw extraction output and run the validity gate.

    Returns ``(model, issues)``. The model is ready for analysis only when
    ``issues`` is empty; a model returned alongside issues exists solely so
    the repair pass has both the artifact and the errors. Schema-level
    failures return ``(None, issues)`` — fail closed, never a partial model.

    ``normalize_ids`` runs :func:`~analysis_service.system_model.normalize_element_ids`
    over the parsed model first, making ``id-mismatch`` unreachable and
    returning the normalized model for the caller to carry forward. It is
    **off by default and on only where a model arrives from a model**: hand-
    authored artifacts — the golden corpus above all — want a mismatch
    reported, because there the two disagreeing fields are an authoring error
    a human should see rather than a slug to canonicalize. It carries the same
    decision for an element's ``source_label``, which that pass snaps to the
    job's own spelling: same flag, same reason, so a model arriving from a
    model has one policy about its spellings rather than two.

    ``sources`` maps each of the job's source labels to that source's text. The
    keys are what a ``source_label`` must name; the values are what a
    ``source_excerpt`` must be found in. Empty — the default — runs neither
    half, which is what a hand-authored model checked outside a job wants.

    **An ID two elements arrived with is refused before it is derived away.**
    Normalization gives each of them its own derived ID, and with the emitted
    ID gone the gate alone would see two well-formed elements and a dangling
    reference, with nothing to say why. So each element that carried a
    shared emitted ID is reported here as ``duplicate-id`` under its derived
    ID, ahead of the gate's own issues, and the references normalization left
    unresolved arrive beside it as ``invalid-reference``. Naming the elements
    rather than the ID they shared keeps the repair scoped to them and the
    references, which is the narrowest repair that can answer it (#961).
    """
    try:
        model = SystemModel.model_validate(data)
    except ValidationError as exc:
        issues = [
            ValidationIssue(
                code="schema",
                message=f"{'.'.join(str(part) for part in error['loc'])}: "
                f"{error['msg']}",
            )
            for error in exc.errors()
        ]
        return None, issues
    issues = []
    if normalize_ids:
        emitted = model.elements()
        shared = duplicate_ids(emitted)
        model = normalize_element_ids(model, sources)
        # The deep copy keeps the walk order, so the two walks pair by position.
        issues = [
            ValidationIssue(
                code="duplicate-id",
                message=f"element {element.id!r} ({element.name!r}) arrived with"
                f" ID {before.id!r}, which {shared[before.id]} elements carried;"
                " every reference to that ID names none of them and was left"
                " unresolved, so name the element each reference means",
                element_id=element.id,
                field="id",
            )
            for before, element in zip(emitted, model.elements(), strict=True)
            if before.id in shared
        ]
    return model, issues + validate(model, extra_asset_tags, max_elements, sources)


#: The issue codes whose repair changes an element's *name*, and therefore its
#: ID. Only ``duplicate-id`` does: two elements hold one ID, and the repair
#: either names them apart or keeps one of them. ``id-mismatch`` is not here
#: because :func:`~analysis_service.system_model.normalize_element_ids` settles
#: it before this gate runs, so no extraction reaches the repair pass carrying
#: one. A code absent from this set keeps its element's ID, which is the safe
#: default: the flows stay pinned.
RENAMING_CODES: frozenset[str] = frozenset({"duplicate-id"})


def repair_scope(
    issues: Sequence[ValidationIssue], model: Mapping[str, Any]
) -> tuple[str, list[str]]:
    """What a repair of these issues may change: the scope, and the elements it names.

    ``"elements"`` when every issue names an element: the repair may change
    those elements, add elements, and nothing else. ``"whole"`` when any issue
    has no element to name — a schema fault over the whole object, no trust
    zones, too many elements — because there is no narrower patch that could
    answer it, and a whole re-extraction is the honest reading rather than a
    preservation repair that preserved nothing.

    **The flows through a renamed element are named with it.** A flow ID is
    derived from its endpoints, so renaming an element renames every flow
    through it: the repair returns that flow under a new ID, and the model it
    was given holds it under the ID the rename replaces. Unless that flow is
    in scope, :func:`restore_unimplicated` puts the replaced entry back,
    pointing at an element ID the repaired model does not carry; the gate
    reports the dangling reference, and the one repair pass is spent on a
    repair that was correct (#1040). The flows are the rename's own half
    rather than a change of the repair's own, which is why they are derived
    here instead of being asked for in the prompt.

    Only :data:`RENAMING_CODES` widens the scope that way. An element named by
    any other issue keeps its ID, so a flow through it that changed did so on
    the repair's own initiative, which is the edit the preservation rule
    exists to stop.

    ``model`` is the parsed model's own dump wherever the gate has one. A
    payload that failed the schema outright reaches here too, and it is read by
    nobody: every issue such a payload raises carries ``code="schema"`` and no
    ``element_id``, so the guard below returns ``"whole"`` and the flows are
    never asked for. :func:`_flows_touching` still reads defensively, on the
    rule that a guard belongs beside the read rather than beside the caller.
    """
    if any(issue.element_id is None for issue in issues):
        return "whole", []
    named = {issue.element_id for issue in issues if issue.element_id}
    renamed = {
        issue.element_id
        for issue in issues
        if issue.element_id and issue.code in RENAMING_CODES
    }
    return "elements", sorted(named | _flows_touching(model, renamed))


def _flows_touching(model: Mapping[str, Any], element_ids: Collection[str]) -> set[str]:
    """Every flow ID in ``model`` with an endpoint in ``element_ids``.

    Every shape the value can take is handled, because ``model`` is a payload a
    model wrote: ``data_flows`` need not be a list, an entry need not be a
    table, and an endpoint need not be a string. An endpoint of any other shape
    names no element, so it matches nothing — and it is filtered rather than
    compared, because a list or a table raises ``TypeError`` on the membership
    test itself.
    """
    flows = model.get("data_flows")
    if not isinstance(flows, list):
        return set()
    wanted = set(element_ids)
    touching: set[str] = set()
    for flow in flows:
        if not isinstance(flow, dict) or not isinstance(flow.get("id"), str):
            continue
        endpoints = [
            value
            for value in (flow.get("source"), flow.get("destination"))
            if isinstance(value, str)
        ]
        if wanted.intersection(endpoints):
            touching.add(flow["id"])
    return touching


def restore_unimplicated(
    previous: Mapping[str, Any],
    repaired: Mapping[str, Any],
    implicated: Collection[str],
) -> tuple[dict[str, Any], list[str]]:
    """The repaired model with every element the issues did not name put back.

    ``prompts/repair.md`` asks for untouched elements byte-identical and
    forbids a "while I'm here" edit; this is what enforces it (#675 D01). Both
    arguments are JSON dumps, the previous one normalized — the model the
    issues were computed against. ``implicated`` is what :func:`repair_scope`
    returns: the elements the issues named, and the flows through them, whose
    IDs a rename of a named element re-derives. Per element group: an element the issues
    named keeps whatever the repair made of it, deleted included; an element
    they did not name reads as it did before, whether the repair changed or
    dropped it, and its ID is returned so the report can say so; an element
    the repair added is kept, since a dangling reference is sometimes answered
    by the element it was pointing at. That last rule is also what lets an
    endpoint repair through: a flow's ID is derived from its endpoints, so
    repointing one arrives as the cited flow deleted and a new flow added,
    and both halves are permitted. ``assumptions`` are the repair's own:
    the prompt tells it to add one for every value it inferred, and the gate
    checks each against the model.
    """
    named = set(implicated)
    result: dict[str, Any] = dict(repaired)
    restored: list[str] = []
    for group in ELEMENT_GROUPS:
        before = {entry["id"]: entry for entry in previous.get(group, ())}
        after = {entry["id"]: entry for entry in repaired.get(group, ())}
        merged = []
        for element_id, entry in before.items():
            if element_id in named:
                if element_id in after:
                    merged.append(after[element_id])
                continue
            if after.get(element_id) != entry:
                restored.append(element_id)
            merged.append(entry)
        merged += [
            entry for element_id, entry in after.items() if element_id not in before
        ]
        result[group] = merged
    return result, sorted(restored)
