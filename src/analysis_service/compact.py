"""The compact extraction transport: a wire form of the System Model, and its adapter.

Extraction emits the largest artifact any node in this service produces, and a
measurable share of it is text the code throws away. An element ID is a pure
function of type and name (:func:`~analysis_service.system_model.derive_element_id`),
so every ``"id": "process:payment-api"`` a model writes is a field
:func:`~analysis_service.system_model.normalize_element_ids` overwrites, and every
reference to it is the same long string again.

**Measured at 10.1% of the emitted characters over the thirteen blessed corpus
models**, by ``uv run python -m evals.bench.deterministic transport``. Read that
as a floor rather than a ceiling: the benchmark's refs run four to six characters
where a model writes two or three, and a hand-corrected corpus model carries
fewer empty optional fields than a live emission does. Read it as *characters*,
too — nothing offline here tokenizes — and as an emission size rather than a
latency.

**The input side is close to free.** The delta prompt is about 490 coarse
tokens, and the compact schema is about 1,500 characters smaller than
``SystemModel``'s, because six classes lose the ``id`` field and its long
pattern. The first live run measured the whole request at 6,116 prompt tokens
against 6,098 for the full route on the same case — **18 tokens**, where the
prompt text alone would have predicted a few hundred.

Whether the output saving is worth having is #938 stage 4, a paired live
comparison. The first one could not resolve it: the emitted-token difference was
under half the full route's own run-to-run spread on one case.
``docs/adr/0035-the-compact-transport-promotes-on-a-predeclared-gate.md`` fixes
what the next run has to show, and it was written before that run. Until it
passes, this route is off.

**This is a transport, not an ontology.** The compact model carries exactly the
facts a :class:`~analysis_service.system_model.SystemModel` carries, under the
same field names, with the same defaults and the same bounds. What changes is
how an element is *named on the wire*: it gets a ``ref``, a short token the
model invents and uses wherever the full model would repeat an ID. Everything
downstream of :func:`parse_extraction` still sees a ``SystemModel``.

**The adapter infers nothing.** It resolves refs, copies fields and hands the
result to the shared validity gate. It does not decide technology, protocol,
classification or any other fact, and it is the reason a compact run and a full
run are comparable at all: the only difference between them is what the model
was asked to write.

Three properties make the expansion safe to put in front of the gate:

- **A provisional ID is unmistakable.** The adapter does not derive element IDs.
  It writes ``ref_process:<ref>`` — a prefix no element class declares, so a
  provisional ID can never equal a derived one — and lets
  :func:`~analysis_service.system_model.normalize_element_ids`, the one reader
  of the ID rule, overwrite it and follow every reference. Deriving here would
  be a second reader of that rule, and it would re-impose the 300-character
  ``id`` bound on a value the full route never re-checks.
- **An unresolved ref survives as itself.** A ref naming no element is left in
  the field verbatim, so the gate reports ``invalid-reference`` naming the ref
  the model wrote. Reference typing is the gate's rule and stays there: a
  ``trust_zone`` pointing at a process resolves to that process's ID and fails
  the gate with a message naming a real element.
- **A reference resolves inside the scope of the field that reads it.** That is
  what gives the transport back the type a full-model ID carries in its prefix,
  and it is the correction the first live run bought: a model gave
  ``card-processor`` to an external entity and to its own trust zone, which the
  full model spells as two IDs and a flat namespace could not tell apart. A ref
  several elements of one scope claim resolves to none of them, and the adapter
  reports ``duplicate-ref`` rather than picking — picking would be a silent
  wrong binding, which is the failure this transport has to be incapable of.

The one deliberate divergence from :class:`~analysis_service.system_model.DataFlow`
is ``operations``, which is required here and defaulted there. The default exists
so archived artifacts written before the field keep loading; nothing archived is
in this format, and #938's rule 4 is that an omitted epistemic field is malformed
output rather than an unknown fact.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from types import MappingProxyType
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analysis_service.system_model import (
    CORE_ASSET_TAGS,
    ELEMENT_GROUPS,
    ZONE_ATTRIBUTE,
    DataFlow,
    DataStore,
    Element,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)
from analysis_service.validation import (
    MAX_ELEMENTS,
    ValidationIssue,
    parse_and_validate,
)

#: Which transport ``extract`` writes in. The strings are spelled here and
#: re-spelled once, in :class:`~analysis_service.report.ExecutionEnvelope`, which
#: holds no import from this module; ``tests/test_compact.py`` holds the two to
#: each other.
ExtractionFormat = Literal["full", "compact-v1"]

#: The full-model route: the model writes a :class:`SystemModel` itself.
FULL_FORMAT: ExtractionFormat = "full"

#: The compact route, version 1. **Versioned in its name**, because a reader of
#: an archived report has to be able to tell which wire form produced it, and
#: because ``prompts/extract-compact.md`` names this string — so a format change
#: moves the composed instruction and therefore every node's fingerprint.
COMPACT_FORMAT: ExtractionFormat = "compact-v1"

#: Every extraction transport a graph can be built for.
EXTRACTION_FORMATS: tuple[ExtractionFormat, ...] = (FULL_FORMAT, COMPACT_FORMAT)

#: A response-local reference, as the model writes it: a lowercase slug.
#:
#: The pattern is the bound, not a tidiness rule, and it is on **every field
#: that holds a ref** rather than only on the declaration. An unresolved ref
#: survives expansion verbatim into a flow endpoint, where
#: :func:`~analysis_service.system_model.make_flow_id` splices it into an
#: element ID that is later rendered into a lane agent's prompt table. Holding a
#: ref to ``[a-z0-9-]`` is what keeps a newline or a backtick run out of that
#: table — see :data:`~analysis_service.system_model.ELEMENT_ID` for why the
#: table's neighbours cannot fence it. A pattern on the declaration alone would
#: bound every ref that resolves and none of the ones that do not, which is
#: exactly backwards.
REF = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

#: The prefix every provisional element ID carries, ahead of the element class's
#: own ``id_prefix``. No element type declares a prefix holding ``_``, so a
#: provisional ID is disjoint from every derived one by construction — which is
#: what lets :func:`~analysis_service.system_model.normalize_element_ids` rewrite
#: references without a provisional ID of one element colliding with the derived
#: ID of another.
PROVISIONAL_PREFIX = "ref_"

#: A name that :func:`~analysis_service.system_model.normalize_name` can slug.
#: It needs one ASCII alphanumeric character; a name of ``"!!!"`` normalizes to
#: an empty slug and has no derived ID at all. The full route lets such a name
#: through with its emitted ID standing unchecked; this route refuses it at the
#: schema, because here there is no emitted ID to fall back on.
NAMEABLE = r"[A-Za-z0-9]"


def _ref_field() -> Any:
    """One field holding a ref: the length bound and the pattern together.

    Spelled once so no reference field can be declared with one and not the
    other. A field bounded by length alone would admit a newline, which is the
    half that matters.

    Forty characters is a blast-radius guard rather than a measured limit, on
    the reading :data:`~analysis_service.validation.MAX_ELEMENTS` is: a ref is a
    handle a model invents for one response, the prompt asks for a short one,
    and a model that writes a long one has written a legal ref rather than a
    wrong one. Nothing downstream reads a ref at all.
    """
    return Field(max_length=40, pattern=REF)


#: The groups whose elements a flow may run between: everything that carries a
#: trust zone. Named here from the model's own fields rather than spelled, so it
#: follows a sixth element type that carries one.
ZONED_GROUPS: tuple[str, ...] = tuple(
    name
    for name in ELEMENT_GROUPS
    if ZONE_ATTRIBUTE
    in get_args(SystemModel.model_fields[name].annotation)[0].model_fields
)


class _CompactElement(BaseModel):
    """What every compact element carries: its ref, its name, its provenance.

    Field for field :class:`~analysis_service.system_model._Element`, with ``id``
    replaced by ``ref`` and every default kept identical. Keeping the defaults
    identical is what makes an omission mean the same thing on both routes: an
    element that omits ``source_excerpt`` fails the citation rung here exactly
    as it does there, rather than being refused earlier by a stricter schema.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str = _ref_field()
    name: str = Field(min_length=1, max_length=200, pattern=NAMEABLE)
    description: str = Field(default="", max_length=2000)
    assets: list[str] = Field(default_factory=list, max_length=len(CORE_ASSET_TAGS) * 4)
    source_excerpt: str = Field(default="", max_length=1000)
    source_label: str = Field(default="", max_length=200)
    source_speaker: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)


class CompactExternalEntity(_CompactElement):
    """An :class:`~analysis_service.system_model.ExternalEntity` on the wire."""

    kind: Literal["human", "external-system"]
    trust_zone: str = _ref_field()


class CompactProcess(_CompactElement):
    """A :class:`~analysis_service.system_model.Process` on the wire."""

    technology: str = Field(max_length=200)
    trust_zone: str = _ref_field()
    exposure: Literal["internet-facing", "internal", "unknown"]
    interface_kind: Literal["web", "non-web", "unknown"]


class CompactDataStore(_CompactElement):
    """A :class:`~analysis_service.system_model.DataStore` on the wire."""

    technology: str = Field(max_length=200)
    trust_zone: str = _ref_field()
    data_classification: str = Field(max_length=200)
    encryption_at_rest: str = Field(max_length=200)


class CompactDataFlow(_CompactElement):
    """A :class:`~analysis_service.system_model.DataFlow` on the wire.

    ``operations`` carries no default here. See this module's docstring.
    """

    source: str = _ref_field()
    destination: str = _ref_field()
    protocol: str = Field(max_length=200)
    authentication: str = Field(max_length=200)
    data_description: str = Field(max_length=1000)
    encryption_in_transit: str = Field(max_length=200)
    operations: Literal["read", "write", "read-write", "unknown"]


class CompactTrustBoundary(_CompactElement):
    """A :class:`~analysis_service.system_model.TrustBoundary` on the wire."""

    kind: Literal["network", "privilege", "tenant", "other"]


class CompactAssumption(BaseModel):
    """An :class:`~analysis_service.system_model.Assumption` on the wire.

    ``element`` rather than ``element_id``, because it holds a ref. The field is
    named for what it carries: a reader who sees ``element_id`` beside a value
    of ``"api"`` has to guess which naming scheme is in play.
    """

    model_config = ConfigDict(extra="forbid")

    assumption: str = Field(min_length=1, max_length=1000)
    element: str = _ref_field()
    attribute: str = Field(min_length=1, max_length=100)
    basis: str = Field(min_length=1, max_length=1000)


class CompactSystemModel(BaseModel):
    """The compact transport's root object: the same six lists, compact rows.

    The group names are the :class:`~analysis_service.system_model.SystemModel`
    field names unchanged, so expansion is a row-by-row copy and a reader can
    hold one vocabulary for both routes.

    **No list here carries a cap.** A cap on a root-level array of objects is a
    shape at least one vendor's structured output refuses outright — see
    ``docs/research/structured-output-shapes.md`` — and the bound on model size
    is the validity gate's :data:`~analysis_service.validation.MAX_ELEMENTS`,
    which reads the output rather than constraining the schema.
    """

    model_config = ConfigDict(extra="forbid")

    external_entities: list[CompactExternalEntity] = Field(default_factory=list)
    processes: list[CompactProcess] = Field(default_factory=list)
    data_stores: list[CompactDataStore] = Field(default_factory=list)
    data_flows: list[CompactDataFlow] = Field(default_factory=list)
    trust_boundaries: list[CompactTrustBoundary] = Field(default_factory=list)
    assumptions: list[CompactAssumption] = Field(default_factory=list)


#: Each compact element class against the full-model class it expands into, in
#: :data:`~analysis_service.system_model.ELEMENT_GROUPS` order. A table rather
#: than a branch, and ``tests/test_compact.py`` checks it against
#: ``SystemModel``'s own fields, so a sixth element type joins both sides or
#: fails the lint.
COMPACT_ELEMENTS: Mapping[str, tuple[type[BaseModel], type[Element]]] = (
    MappingProxyType(
        {
            "external_entities": (CompactExternalEntity, ExternalEntity),
            "processes": (CompactProcess, Process),
            "data_stores": (CompactDataStore, DataStore),
            "data_flows": (CompactDataFlow, DataFlow),
            "trust_boundaries": (CompactTrustBoundary, TrustBoundary),
        }
    )
)

#: Every field a compact element may leave out, in declaration order.
#:
#: Read off the schema rather than listed, so the allowlist #938 asks for is the
#: one the code enforces. A field is here exactly when it carries a default, and
#: every default here is the one :class:`~analysis_service.system_model._Element`
#: carries — so an omission means on this route what it means on the other.
#:
#: ``source_excerpt`` and ``source_label`` are in this list and are not safe to
#: omit, which is not a contradiction: they default to ``""`` on both routes and
#: the *gate* is what refuses an element that cites nothing. The transport
#: copies the full model's answer rather than inventing a stricter one.
OMITTABLE_FIELDS: tuple[str, ...] = tuple(
    name
    for name, field in _CompactElement.model_fields.items()
    if not field.is_required()
)

#: The name an assumption holds its subject's ref under.
ASSUMPTION_SUBJECT = "element"

#: Every field that holds a ref, against the element groups that field may name.
#:
#: **A ref is resolved inside the scope of the field that reads it**, which is
#: what gives the transport back the type a full-model ID carries in its prefix.
#: A live extraction of ``01-payments-checkout`` gave ``card-processor`` to both
#: the external entity and its own trust zone, which is ordinary naming and
#: legal in the full model — ``entity:card-processor`` and
#: ``boundary:card-processor`` are different IDs. A flat namespace made every use
#: of that ref ambiguous, and a whole extraction failed the gate over a name a
#: reader would call correct.
#:
#: The scopes are the gate's own reference rules, and the gate stays the reader
#: that *rules* on them: a ref found nowhere in its scope is looked up across
#: every group, so a ``trust_zone`` naming a process still resolves and still
#: fails the gate with a message naming a real element. Widening only where the
#: scope is empty is what keeps that diagnostic without letting a wide lookup
#: overrule a narrow hit.
REFERENCE_SCOPES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "trust_zone": ("trust_boundaries",),
        "source": ZONED_GROUPS,
        "destination": ZONED_GROUPS,
        # An assumption may name any element, so its scope is every group and
        # there is nothing to widen to. A ref two elements share is genuinely
        # ambiguous here, and that is the one place it still is.
        ASSUMPTION_SUBJECT: ELEMENT_GROUPS,
    }
)

#: The reference fields an *element* carries, as :func:`_expand_element` walks
#: them. Derived from the scopes table, so a fifth reference field joins both by
#: being declared once.
REFERENCE_FIELDS: tuple[str, ...] = tuple(
    name for name in REFERENCE_SCOPES if name != ASSUMPTION_SUBJECT
)


def parse_extraction(
    payload: object,
    extraction_format: str = FULL_FORMAT,
    *,
    sources: Mapping[str, str] = MappingProxyType({}),
    extra_asset_tags: Collection[str] = (),
    max_elements: int = MAX_ELEMENTS,
) -> tuple[SystemModel | None, list[ValidationIssue]]:
    """The one reader of "what extraction emitted" against "a validated model".

    Two callers cross this seam: the graph's validity gate and the eval
    harness's extraction mode. They call this rather than
    :func:`~analysis_service.validation.parse_and_validate` directly, so the
    transport choice and the gate arrive together or not at all. A transport
    only one of them applied would grade an extraction against a gate
    production never ran.

    ``normalize_ids`` is not a parameter: both callers pass it, and the compact
    route *requires* it, since the IDs it hands the gate are provisional.

    Returns ``(model, issues)`` on
    :func:`~analysis_service.validation.parse_and_validate`'s contract. A
    ``None`` model under :data:`COMPACT_FORMAT` means the transport failed
    rather than the model being invalid, and the caller routes it accordingly:
    ``prompts/repair.md`` is written for a full System Model, so handing it a
    compact payload would ask a repair pass to do a format conversion.
    """
    issues: list[ValidationIssue] = []
    if extraction_format == COMPACT_FORMAT:
        payload, issues = expand(payload)
        if payload is None:
            return None, issues
    elif extraction_format != FULL_FORMAT:
        raise ValueError(f"unknown extraction format: {extraction_format!r}")
    model, gate_issues = parse_and_validate(
        payload,
        extra_asset_tags,
        max_elements,
        normalize_ids=True,
        sources=sources,
    )
    return model, issues + gate_issues


def expand(payload: object) -> tuple[dict[str, Any] | None, list[ValidationIssue]]:
    """Expand a compact payload into full-model JSON. Pure, and it infers nothing.

    Returns ``(model, issues)``. ``model`` is ``None`` only when ``payload`` is
    not a well-formed :class:`CompactSystemModel`, and then ``issues`` holds the
    schema faults — the same fail-closed shape
    :func:`~analysis_service.validation.parse_and_validate` uses, so no caller
    ever sees a half-converted model.

    A returned model may still carry issues: a ref two elements claimed is
    reported here and bound nowhere. Everything else the gate decides, over IDs
    this function only made provisional.
    """
    try:
        compact = CompactSystemModel.model_validate(payload)
    except ValidationError as exc:
        return None, [
            ValidationIssue(
                code="schema",
                message=f"{'.'.join(str(part) for part in error['loc'])}: "
                f"{error['msg']}",
            )
            for error in exc.errors()
        ]

    claims = _claims(compact)
    ambiguous: set[str] = set()
    expanded: dict[str, Any] = {
        group: [
            _expand_element(group, row, claims, ambiguous)
            for row in getattr(compact, group)
        ]
        for group in COMPACT_ELEMENTS
    }
    expanded["assumptions"] = [
        {
            "assumption": entry.assumption,
            "element_id": _resolve(
                entry.element, ASSUMPTION_SUBJECT, claims, ambiguous
            ),
            "attribute": entry.attribute,
            "basis": entry.basis,
        }
        for entry in compact.assumptions
    ]
    return expanded, [
        ValidationIssue(
            code="duplicate-ref",
            message=f"reference {ref!r} is claimed by"
            f" {len(claims[ref])} elements that the field reading it cannot tell"
            " apart, so it resolved to none of them; a ref is unique among the"
            " elements a reference could confuse it with",
        )
        for ref in sorted(ambiguous)
    ]


def _claims(compact: CompactSystemModel) -> Mapping[str, list[tuple[str, str]]]:
    """Every ref the payload declares, against the elements claiming it.

    One entry per claim rather than a count, because which *group* an element
    sits in is what a reference field uses to tell two same-named elements
    apart — see :data:`REFERENCE_SCOPES`.
    """
    claims: dict[str, list[tuple[str, str]]] = {}
    for group, (_, element_type) in COMPACT_ELEMENTS.items():
        for row in getattr(compact, group):
            claims.setdefault(row.ref, []).append(
                (group, _provisional_id(element_type, row.ref))
            )
    return claims


def _resolve(
    ref: str,
    field: str,
    claims: Mapping[str, list[tuple[str, str]]],
    ambiguous: set[str],
) -> str:
    """The provisional ID one reference names, or the ref itself if none does.

    Two lookups, narrow then wide, and the order is the whole rule. The narrow
    one is the field's own scope, which is what lets a trust zone and the
    external entity inside it share a name the way the full model's type
    prefixes let them. The wide one runs **only when the scope holds nothing**,
    so a ref of the wrong type still resolves and the gate reports it against a
    real element ID rather than against a token it cannot place.

    A ref several elements of one scope claim resolves to none of them: picking
    would be a silent wrong binding. It is recorded in ``ambiguous`` and left in
    the field, so the gate reports the dangling reference beside the
    ``duplicate-ref`` that explains it.
    """
    scoped = [
        provisional
        for group, provisional in claims.get(ref, ())
        if group in REFERENCE_SCOPES[field]
    ]
    if len(scoped) == 1:
        return scoped[0]
    if scoped:
        ambiguous.add(ref)
        return ref
    wide = [provisional for _, provisional in claims.get(ref, ())]
    if len(wide) == 1:
        return wide[0]
    if wide:
        ambiguous.add(ref)
    return ref


def _provisional_id(element_type: type[Element], ref: str) -> str:
    """The stand-in ID one element carries until the ID rule overwrites it.

    Typed, so :func:`~analysis_service.system_model.normalize_element_ids` puts
    it in the right group's rewrite, and prefixed with
    :data:`PROVISIONAL_PREFIX` so it cannot collide with any derived ID.
    """
    return f"{PROVISIONAL_PREFIX}{element_type.id_prefix}:{ref}"


def _expand_element(
    group: str,
    row: BaseModel,
    claims: Mapping[str, list[tuple[str, str]]],
    ambiguous: set[str],
) -> dict[str, Any]:
    """One compact row as full-model JSON: ``ref`` becomes ``id``, refs resolve.

    Every other field is copied unchanged. Each reference resolves in its own
    field's scope (:func:`_resolve`), and one that resolves to nothing is left
    as the model wrote it, so the gate reports ``invalid-reference`` quoting the
    ref rather than an ID this function invented.
    """
    _, element_type = COMPACT_ELEMENTS[group]
    expanded = row.model_dump(mode="json")
    expanded["id"] = _provisional_id(element_type, expanded.pop("ref"))
    for field in REFERENCE_FIELDS:
        if field in expanded:
            expanded[field] = _resolve(expanded[field], field, claims, ambiguous)
    return expanded
