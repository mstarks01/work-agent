"""The compact extraction transport: a wire form of the System Model, and its adapter.

Extraction emits the largest artifact any node in this service produces, and a
measurable share of it is text the code throws away. An element ID is a pure
function of type and name (:func:`~analysis_service.system_model.derive_element_id`),
so every ``"id": "process:payment-api"`` a model writes is a field
:func:`~analysis_service.system_model.normalize_element_ids` overwrites, and every
reference to it is the same long string again.

**Measured at 3.3% of the emitted tokens**, over five corpus sweeps per route
on 2026-09-15, and that is the figure to use. ``uv run python -m
evals.bench.deterministic transport`` prints 5.0% of *characters*, which is an
upper bound: the corpus models are hand-corrected, and characters are not
tokens.

Both numbers were nearly double this until the third live sweep. The bench
priced against a six-character ref; five sweeps put the mean ref a model
actually writes at 20.8 characters, because a model names a ref after the thing
it points at. The bench now uses a name-slug ref and the earlier 10.1% figure
is gone from this tree.

**The input side is close to free.** The delta prompt is about 500 coarse
tokens, and the compact schema is smaller than ``SystemModel``'s because six
classes lose the ``id`` field and its long pattern. The first live run measured
the whole request at 6,116 prompt tokens against 6,098 for the full route on the
same case — **18 tokens**, where the prompt text alone would have predicted a
few hundred.

**What the saving costs is element naming, and not the facts.** Five corpus
sweeps per route, 2026-09-15: every figure reading an *attribute* is unmoved at
two decimal places of a standard deviation — control-state agreement +0.02 sd,
scored-field agreement −0.05 sd — while every figure reading an *identity* moves
2 to 4 sd against, and rename candidates rise 5.7 sd. The compact route finds
the same system and names its parts differently more often. It also invents
fewer elements out of nothing.

There is a mechanism to test rather than a cause to state. The full route writes
``"id": "process:scheduling-web-app"`` beside ``"name": "Scheduling web app"``,
and ``prompts/extract.md`` rule 3 spends its longest passage on what that slug
costs — an abbreviated ID is replaced by the name's slug, and one word added to
an element renames every flow through it. This route removes that field, which
is exactly where the saving comes from. The remedy, if the mechanism holds, is
to restate rule 3's discipline in the delta where the ref is introduced.

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
ExtractionFormat = Literal["full", "compact-v4"]

#: The full-model route: the model writes a :class:`SystemModel` itself.
FULL_FORMAT: ExtractionFormat = "full"

#: The compact route, version 4. Each earlier version is out of the tree, and
#: each was retired by a measurement rather than an argument — nothing persisted
#: is in this format, so every change is a cutover rather than a migration.
#:
#: Version 1 wrote an untyped ref, so an element and its own trust zone sharing
#: a name made every use of that ref ambiguous. Version 2 kept the untyped ref
#: and moved the assumption inside its element to dodge the one reference no
#: scope could decide; that removed the `duplicate-ref` it was aimed at and
#: **caused nine failures of a kind neither the full route nor version 1 ever
#: produced** — an inference written on the flow carrying the data rather than
#: the store holding it. Version 3 put the type back in the ref, which is what
#: a full-model ID's prefix always was, and put the assumption back where it
#: never failed; it failed the gate on two flow refs the model had derived from
#: the flow's *endpoints*, so two flows between one pair collided.
#:
#: **Version 4 says what a flow's ref is named after, and nothing else changes.**
#: The schema, the adapter and every other rule are version 3's. A flow's ref is
#: its label, because a label is what tells two flows between one pair apart,
#: and ``prompts/extract-compact.md`` says so where it introduces the ref. This
#: is the only version whose safety fix also *increased* the saving: a label
#: slug runs 17.4 characters against 33.2 for an endpoint pair over the blessed
#: corpus, worth 1.04% of the full emission on top of the transport's own.
#:
#: **Versioned in its name**, because a reader of
#: an archived report has to be able to tell which wire form produced it, and
#: because ``prompts/extract-compact.md`` names this string — so a format change
#: moves the composed instruction and therefore every node's fingerprint.
COMPACT_FORMAT: ExtractionFormat = "compact-v4"

#: Every extraction transport a graph can be built for.
EXTRACTION_FORMATS: tuple[ExtractionFormat, ...] = (FULL_FORMAT, COMPACT_FORMAT)

#: Each element group's ref tag: the first letter of the element class's own
#: ``id_prefix``. Derived rather than invented, so the tag a model writes is the
#: type the full model would have spelled out, and a sixth element type brings
#: its own. ``tests/test_compact.py`` holds the five to being distinct — two
#: types sharing a letter would put the ambiguity straight back.
REF_TAGS: Mapping[str, str] = MappingProxyType(
    {
        group: get_args(SystemModel.model_fields[group].annotation)[0].id_prefix[0]
        for group in ELEMENT_GROUPS
    }
)

#: A response-local reference, as the model writes it: a type tag, a colon and a
#: lowercase slug — ``p:api``, ``s:orders-db``.
#:
#: **The tag is what a full-model ID's prefix always was**, and leaving it out
#: is what cost versions 1 and 2. An element and its own trust zone share a name
#: routinely, and ``entity:card-processor`` and ``boundary:card-processor`` are
#: two IDs; two untyped refs spelled ``card-processor`` are one handle naming
#: neither. Measured at 1.1 percentage points of the saving — 10.1% of the
#: corpus emission against 9.2% — which is what the type is worth.
#:
#: The pattern is also the bound, not a tidiness rule, and it is on **every
#: field that holds a ref** rather than only on the declaration. An unresolved
#: ref survives expansion verbatim into a flow endpoint, where
#: :func:`~analysis_service.system_model.make_flow_id` splices it into an
#: element ID that is later rendered into a lane agent's prompt table. Holding a
#: ref to ``[a-z0-9-]`` is what keeps a newline or a backtick run out of that
#: table — see :data:`~analysis_service.system_model.ELEMENT_ID` for why the
#: table's neighbours cannot fence it. A pattern on the declaration alone would
#: bound every ref that resolves and none of the ones that do not, which is
#: exactly backwards.
REF = rf"^[{''.join(sorted(REF_TAGS.values()))}]:[a-z0-9]+(?:-[a-z0-9]+)*$"

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

    ``element`` is a typed ref, and ``element`` rather than ``element_id``
    because it holds one: a reader who sees ``element_id`` beside a value of
    ``"p:api"`` has to guess which naming scheme is in play.

    **Naming the subject is what keeps the pairing honest.** Version 2 wrote
    this entry inside its element instead, so the subject was where the entry
    sat and no reference could dangle. It cost more than it saved: across five
    corpus sweeps the model wrote nine ``data_classification`` inferences on a
    flow or a process — an attribute only a Data Store declares — where the full
    route and version 1 wrote none in five sweeps each. Naming the element
    beside the attribute is the moment the model checks that the two go
    together, and removing it moved the inference to whatever the model was
    thinking about rather than what the fact is true of.
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

#: Every field that holds a ref.
#:
#: **There is no scope table any more, and that is the point of version 3.** A
#: typed ref names exactly one element, so resolution is a lookup rather than a
#: search narrowed by which field is reading. Version 1 had no way to tell an
#: entity from its own trust zone; version 2 gave the three endpoint types one
#: namespace still, so a process and a store both called ``orders-db`` remained
#: ambiguous for a flow endpoint. The tag settles all of it.
#:
#: Reference *typing* stays the gate's rule, and the good diagnostic survives: a
#: ``trust_zone`` written ``p:api`` resolves to ``process:api`` and fails the
#: gate with a message naming a real element, rather than a token nobody can
#: place.
REFERENCE_FIELDS: tuple[str, ...] = ("trust_zone", "source", "destination")


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

    claims, ambiguous = _claims(compact)
    expanded: dict[str, Any] = {
        group: [_expand_element(group, row, claims) for row in getattr(compact, group)]
        for group in COMPACT_ELEMENTS
    }
    expanded["assumptions"] = [
        {
            "assumption": entry.assumption,
            "element_id": claims.get(entry.element, entry.element),
            "attribute": entry.attribute,
            "basis": entry.basis,
        }
        for entry in compact.assumptions
    ]
    return expanded, [
        ValidationIssue(
            code="duplicate-ref",
            message=f"reference {ref!r} is claimed by more than one element, so"
            " every use of it is ambiguous and none was resolved; a ref is"
            " unique among the elements of its own type",
        )
        for ref in sorted(ambiguous)
    ]


def _claims(
    compact: CompactSystemModel,
) -> tuple[Mapping[str, str], set[str]]:
    """Each ref against the provisional ID it names, and the refs that collide.

    A typed ref names one element, so this is a plain map and resolution is a
    lookup. A ref two elements claim can only be two elements of one *type* —
    the tag rules out the cross-type case that cost versions 1 and 2 — and that
    is a genuine ambiguity: it binds to neither, and the gate reports the
    dangling reference beside the ``duplicate-ref`` that explains it.
    """
    claims: dict[str, str] = {}
    ambiguous: set[str] = set()
    for group, (_, element_type) in COMPACT_ELEMENTS.items():
        for row in getattr(compact, group):
            if row.ref in claims:
                ambiguous.add(row.ref)
                continue
            claims[row.ref] = _provisional_id(element_type, row.ref)
    return {
        ref: provisional for ref, provisional in claims.items() if ref not in ambiguous
    }, ambiguous


def _provisional_id(element_type: type[Element], ref: str) -> str:
    """The stand-in ID one element carries until the ID rule overwrites it.

    Typed, so :func:`~analysis_service.system_model.normalize_element_ids` puts
    it in the right group's rewrite, and prefixed with
    :data:`PROVISIONAL_PREFIX` so it cannot collide with any derived ID.
    """
    return f"{PROVISIONAL_PREFIX}{element_type.id_prefix}:{ref.split(':', 1)[-1]}"


def _expand_element(
    group: str, row: BaseModel, claims: Mapping[str, str]
) -> dict[str, Any]:
    """One compact row as full-model JSON: ``ref`` becomes ``id``, refs resolve.

    Every other field is copied unchanged. A ref naming no element is left as
    the model wrote it, so the gate reports ``invalid-reference`` quoting the
    ref rather than an ID this function invented. A ref of the wrong *type*
    still resolves, and the gate reports it against a real element ID: which
    types a field may name is the gate's rule and stays there.
    """
    _, element_type = COMPACT_ELEMENTS[group]
    expanded = row.model_dump(mode="json")
    expanded["id"] = _provisional_id(element_type, expanded.pop("ref"))
    for field in REFERENCE_FIELDS:
        if field in expanded:
            expanded[field] = claims.get(expanded[field], expanded[field])
    return expanded
