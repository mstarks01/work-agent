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

from collections import Counter
from collections.abc import Collection, Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from analysis_service.analysis import control_state
from analysis_service.grounding import normalize, verify_normalized
from analysis_service.references import canonical
from analysis_service.system_model import (
    CORE_ASSET_TAGS,
    Assumption,
    Element,
    SystemModel,
    assumable_attributes,
    derive_element_id,
    normalize_element_ids,
)

IssueCode = Literal[
    "schema",
    "duplicate-id",
    "id-mismatch",
    "invalid-reference",
    "no-trust-zones",
    "illegal-asset-tag",
    "too-many-elements",
    "unverifiable-excerpt",
    "assumption-on-unknown",
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

    id_counts = Counter(element.id for element in elements)
    for element_id, count in sorted(id_counts.items()):
        if count > 1:
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

    for zoned in model.zoned_elements():
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
    actually declares — ``attribute_names`` is the registry, derived from the
    schema, so a field added to an element type is covered here the day it
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
                field="attribute",
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
                field="attribute",
            )
        ]
    return []


def _citation_issues(
    elements: Collection[Element], sources: Mapping[str, str]
) -> list[ValidationIssue]:
    """The traceability chain resolves *and* leads somewhere, or the model fails.

    Excerpt and label are **coupled**: a quote with no label cites nothing, and
    a label naming a source the job never carried asserts a chain that is not
    there — which is worse than no citation at all, because a reader who
    follows it finds a source that does not exist. Set membership is
    mechanical, so it belongs here rather than in a prompt.

    The label resolving is only half the chain. The other half — is the quoted
    span actually *in* that source — is the same question
    :mod:`analysis_service.grounding` answers for a threat's ``quote`` ground,
    asked here of the excerpt that ties an element to the words it came from.
    The two were never different questions; only one of them used to be asked.
    That ladder was calibrated on exactly this data — "the 12 corpus cases' 206
    element excerpts" — at **0 false rejections in 206**, so turning it on here
    is the one rung in this repo whose cost was measured before it was spent.

    Failing closed is affordable because extraction has the ``repair`` pass: an
    excerpt the gate cannot find is cited back to the transcriber that wrote
    it, with the source still in front of it. That is the opposite of the draft
    seam, where the same failure has nowhere to go.

    This is the one gate rule taking data from outside the model. Where no
    sources are supplied neither half runs: a hand-authored model checked
    without a job has nothing to check against, and inventing it would fail
    every such model on a citation that is not wrong. A source carried with
    empty text skips the text half alone, for the same reason.
    """
    if not sources:
        return []

    # Folded once, then reused across every element's excerpt — the gate admits
    # up to ``MAX_ELEMENTS`` of them against the same handful of sources.
    folded = {label: normalize(text) for label, text in sources.items()}
    issues: list[ValidationIssue] = []
    for element in elements:
        if not element.source_excerpt:
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
    if normalize_ids:
        model = normalize_element_ids(model, sources)
    return model, validate(model, extra_asset_tags, max_elements, sources)
