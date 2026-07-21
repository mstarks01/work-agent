"""Mechanical validity gate for the System Model.

Implements the well-formedness rules from wayfinder ticket 003: unique typed
IDs deterministic from type+name, referential integrity of flow endpoints and
``trust_zone``, at least one trust zone, legal enum values (including the
config-extendable asset-tag vocabulary), and assumptions referencing real
elements. Ticket 010 added the admission cap on model size
(:data:`MAX_ELEMENTS`), enforced here because this is the one gate every
extraction passes through before any analyst spend.

Ticket 037 moved ID derivation into code: callers passing ``normalize_ids``
to :func:`parse_and_validate` get their IDs canonicalized before the gate
runs, so ``id-mismatch`` is unreachable from the extraction pipeline. The rule
stays enforced here for models that arrive hand-authored.

Errors are structured (:class:`ValidationIssue`) so the repair pass can feed
them back to the extraction agent verbatim. Analysts only ever see models for
which :func:`validate` returns an empty list. On any failure the gate reports
and denies — never silently auto-repairs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from stride_service.system_model import (
    CORE_ASSET_TAGS,
    SystemModel,
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
]

# Admission cap on model size (ticket 010). Deliberately loose: it is a
# blast-radius guard, not a quality threshold. Every artifact downstream
# scales with element count — six analysts each read the whole model, and the
# critic reads every draft they produce in one pass — so an unbounded model
# means unbounded spend and a critic dedupeing hundreds of drafts with no
# error anywhere. Rejecting here costs one ``len()`` and happens before any
# analyst call. Element count rather than a token estimate because it is the
# number a user can act on: "split the system" is advice they can follow.
#
# Where quality actually decays with model size is unmeasured — the golden
# corpus is 8-20 elements by design (ticket 009) — so this number is a guard
# awaiting evidence, not a calibrated limit.
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
) -> list[ValidationIssue]:
    """Run every gate rule; an empty result means the model is analyst-ready.

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

    for element in (*model.external_entities, *model.processes, *model.data_stores):
        if element.trust_zone not in boundary_ids:
            issues.append(
                ValidationIssue(
                    code="invalid-reference",
                    message=f"trust_zone {element.trust_zone!r} does not reference"
                    " an existing Trust Boundary element",
                    element_id=element.id,
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

    element_ids = {element.id for element in elements}
    for assumption in model.assumptions:
        if assumption.element_id not in element_ids:
            issues.append(
                ValidationIssue(
                    code="invalid-reference",
                    message=f"assumption references element {assumption.element_id!r},"
                    " which does not exist in the model",
                    element_id=assumption.element_id,
                    field="assumptions",
                )
            )

    return issues


def parse_and_validate(
    data: object,
    extra_asset_tags: Collection[str] = (),
    max_elements: int = MAX_ELEMENTS,
    normalize_ids: bool = False,
) -> tuple[SystemModel | None, list[ValidationIssue]]:
    """Parse raw extraction output and run the validity gate.

    Returns ``(model, issues)``. The model is analyst-ready only when
    ``issues`` is empty; a model returned alongside issues exists solely so
    the repair pass has both the artifact and the errors. Schema-level
    failures return ``(None, issues)`` — fail closed, never a partial model.

    ``normalize_ids`` runs :func:`~stride_service.system_model.normalize_element_ids`
    over the parsed model first, making ``id-mismatch`` unreachable and
    returning the normalized model for the caller to carry forward. It is
    **off by default and on only where a model arrives from a model**: hand-
    authored artifacts — the golden corpus above all — want a mismatch
    reported, because there the two disagreeing fields are an authoring error
    a human should see rather than a slug to canonicalize.
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
        model = normalize_element_ids(model)
    return model, validate(model, extra_asset_tags, max_elements)
