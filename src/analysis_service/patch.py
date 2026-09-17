"""The bounded repair pass: typed operations over a graph and a catalog.

#1003's arms C and D both read the sources once more and ask what the first
pass missed — which supported component is absent, which fact is attached to
the wrong subject, which hedge or conflict was lost, which parallel interaction
was collapsed. **They share this code.** An arm that applied its own patches
would be a second reader of every rule here, and the comparison between C and D
would then measure two applicators rather than two extraction orders.

Nothing routes to it. There is no review prompt yet and ``build_pipeline`` has
no entry for it, so this is the applicator the route will call.

Five operation kinds, and a correction is spelled as two of them. An
**Assertion**'s identity is computed from its own parts, so changing a value
makes a different row: ``retract-assertion`` drops one identity and
``add-assertion`` writes another, and nothing edits a row in place.

**Every add goes through** :func:`~analysis_service.factbundle.resolve_bundle`
**with the arm's model as its base.** That is what makes the mechanics one
route: a source-backed component omitted from the graph is added by the code
arm B builds its whole graph with, so a citation this pass accepts is one that
pass would have accepted.

Three rules keep a failed batch from leaving half a change behind:

**A refused operation refuses what depends on it.** An interaction naming an
element that was refused, a fact naming that interaction — each is refused in
turn, and a cycle among operations refuses every operation in it. Where the
resolver is what refused the element, the resolver is also what refuses the
rows reaching it, so the reason a reader sees is the one that stopped the row.

**A precondition is read against the state the batch started from.** An
operation asserting that an ID is absent, or that an identity is present, is
refused when the graph says otherwise, so a review written against a stale
model cannot land.

**A batch whose result fails the gates is discarded whole.** The original model
and record stand, every operation is reported as rolled back, and the reasons
are kept. A partly applied batch is the one outcome nobody could attribute.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.assertions import (
    GATE_REFUSALS,
    AssertionCatalog,
    AssertionRecord,
    assertion_id,
    gate_issues,
    merged,
    without,
)
from analysis_service.factbundle import (
    HANDLE_PATTERN,
    MAX_HANDLE_CHARS,
    MAX_REFERENCE_CHARS,
    DispositionRow,
    FactProposal,
    InteractionProposal,
    MentionProposal,
    SourceFactBundle,
    resolve_bundle,
)
from analysis_service.system_model import SystemModel
from analysis_service.validation import validate

#: The operation schema's own version, apart from
#: :data:`~analysis_service.factbundle.BUNDLE_VERSION` because a patch says
#: what to change and a bundle says what a source states.
PATCH_VERSION = 1

#: What one operation does. ``retract-assertion`` and ``add-assertion`` are how
#: a correction is written, because an assertion's identity is computed from
#: its parts and a changed value is a different row.
OperationKind = Literal[
    "add-element",
    "add-interaction",
    "add-assertion",
    "retract-assertion",
    "mark-unresolved",
]


#: Which payload field each kind reads. A table rather than a branch, held
#: against :data:`OperationKind` by ``tests/test_patch.py``, so a kind added on
#: one side fails rather than reading as an operation that carries nothing.
@dataclass(frozen=True)
class PayloadRule:
    """What one kind carries, and where it goes.

    ``field`` is the :class:`Operation` field the kind reads. ``table`` is the
    :class:`~analysis_service.factbundle.SourceFactBundle` list the payload
    joins, or ``""`` for a kind that adds nothing — which is what makes
    :data:`ADDITIONS` a reading of this table rather than a second list.
    ``names`` are the payload's own reference fields, which is what the
    dependency graph walks.
    """

    field: str
    table: str
    names: tuple[str, ...] = ()


PAYLOADS: Mapping[str, PayloadRule] = MappingProxyType(
    {
        "add-element": PayloadRule("element", "mentions"),
        "add-interaction": PayloadRule(
            "interaction", "interactions", ("initiator", "receiver")
        ),
        "add-assertion": PayloadRule("assertion", "facts", ("subject", "value")),
        "retract-assertion": PayloadRule("retract", ""),
        "mark-unresolved": PayloadRule("question", ""),
    }
)

#: Every :class:`Operation` field a kind may carry, read off the table.
PAYLOAD_FIELDS: frozenset[str] = frozenset(rule.field for rule in PAYLOADS.values())

#: The kinds that reach :func:`~analysis_service.factbundle.resolve_bundle`,
#: which is every kind whose payload joins a bundle table. Read off the table,
#: so a kind added there is classified by what it carries rather than by name.
ADDITIONS: frozenset[str] = frozenset(
    kind for kind, rule in PAYLOADS.items() if rule.table
)

#: The most operations one batch may carry. A bounded repair pass, in the shape
#: the extraction repair rung already has: one pass, then the result stands.
MAX_OPERATIONS = 50

#: The most preconditions one operation may carry.
MAX_PRECONDITIONS = 4


class Precondition(BaseModel):
    """What must be true of the state before one operation may land.

    ``target`` is an **Element ID**, a flow ID or an **Assertion**'s computed
    identity, and ``state`` says which side of present the batch was written
    for. Read against the model and catalog the batch started from, never
    against what an earlier operation in the same batch produced: a review
    states what it saw, and a precondition that moved under it is the stale
    reading this field exists to catch.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["absent", "present"]
    target: str = Field(min_length=1, max_length=MAX_REFERENCE_CHARS)


class Operation(BaseModel):
    """One typed change, with what it rests on and why it is asked for.

    ``handle`` is this operation's name and **the name of the row it adds**:
    the payload's own handle is not read, so one row can never carry two names,
    and another operation reaching this one names this handle.

    ``reason`` is what the review says it found. It is required, because an
    operation with no stated reason is a change nobody can weigh against the
    source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(pattern=HANDLE_PATTERN)
    kind: OperationKind
    reason: str = Field(min_length=1, max_length=1000)
    preconditions: list[Precondition] = Field(
        default_factory=list, max_length=MAX_PRECONDITIONS
    )
    element: MentionProposal | None = None
    interaction: InteractionProposal | None = None
    assertion: FactProposal | None = None
    retract: str = Field(default="", max_length=MAX_REFERENCE_CHARS)
    question: str = Field(default="", max_length=1000)


class PatchBatch(BaseModel):
    """Every operation one review pass proposes, applied or discarded together."""

    model_config = ConfigDict(extra="forbid")

    patch_version: int = Field(default=PATCH_VERSION, ge=1)
    operations: list[Operation] = Field(default_factory=list)


class OperationOutcome(BaseModel):
    """What became of one operation, and why.

    ``applied`` reached the graph or the catalog. ``refused`` did not, and
    ``code`` names the check that stopped it. ``unresolved`` is a question this
    pass raised and nothing answered, which changes no artifact by design.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    handle: str = Field(default="", max_length=MAX_HANDLE_CHARS)
    state: Literal["applied", "refused", "unresolved"]
    code: str = Field(default="", max_length=60)
    message: str = Field(default="", max_length=1000)


@dataclass(frozen=True)
class PatchResult:
    """The graph and the record after one batch, and what every operation came to.

    ``rolled_back`` says the batch was discarded whole: ``model`` and ``record``
    are then the ones the caller passed in, unchanged, and every outcome reads
    ``refused``. A caller keeps the result either way, because the cost was
    spent either way and #1003 counts it.
    """

    model: SystemModel
    record: AssertionRecord
    outcomes: tuple[OperationOutcome, ...]
    rolled_back: bool

    @property
    def applied(self) -> tuple[str, ...]:
        """The handles that changed something. Derived, never stored beside."""
        return tuple(
            outcome.handle for outcome in self.outcomes if outcome.state == "applied"
        )

    @property
    def refusals(self) -> tuple[OperationOutcome, ...]:
        """Every operation that did not land, with the reason it did not."""
        return tuple(outcome for outcome in self.outcomes if outcome.state == "refused")


def apply_patch(
    batch: PatchBatch,
    model: SystemModel,
    record: AssertionRecord,
    sources: Mapping[str, str],
) -> PatchResult:
    """Apply one batch to one arm's output, or leave that output as it was.

    Five passes. The batch's own shape is checked and refused whole; each
    operation's shape and preconditions are checked against the state the batch
    started from; operations that reach one of those refusals, or each other in
    a cycle, are refused with it; the additions go through
    :func:`~analysis_service.factbundle.resolve_bundle` over ``model``;
    retractions apply to the catalog the batch started from, the additions merge
    on top, and the result runs the two gates every arm shares.

    **A batch lands whole or not at all.** One refused operation discards every
    other one, and ``model`` and ``record`` are the ones the caller passed in.
    A correction is a retraction and an addition that replaces it, and nothing
    in the batch says which addition replaces which retraction — so a rule that
    applied what it could would delete a row whose replacement the resolver
    refused, and the catalog would lose a fact to a repair pass. Each refusal
    still carries its own code and message, so what the review got wrong is
    readable off the outcomes.

    **The dependency walk runs once, before the resolver.** After it, the
    resolver *is* the reader of a broken reference: a row whose element it
    refused draws ``dangling-endpoint`` or ``dangling-subject`` from the same
    code arm B runs, and a second walk here would relabel a refusal that is
    already right.

    **Retract first, then merge.** A correction retracts an identity and adds a
    row that replaces it, and a batch that merged first could drop the new row
    where the two identities happened to agree.
    """
    whole = _batch_refusal(batch)
    if whole is not None:
        return PatchResult(model, record, (whole,), rolled_back=True)

    refused: dict[str, OperationOutcome] = {}
    by_handle = {operation.handle: operation for operation in batch.operations}
    for operation in batch.operations:
        stopped = _shape_refusal(operation) or _precondition_refusal(
            operation, model, record
        )
        if stopped is not None:
            refused[operation.handle] = stopped
    refused.update(_dependency_refusals(batch.operations, by_handle, refused))

    additions = [
        operation
        for operation in batch.operations
        if operation.kind in ADDITIONS and operation.handle not in refused
    ]
    resolution = resolve_bundle(_bundle(additions), sources, base=model)
    landed = {row.handle: row for row in resolution.dispositions}
    for operation in additions:
        row = landed.get(operation.handle)
        if row is None or row.disposition in ("consumed", "preserved"):
            continue
        refused[operation.handle] = OperationOutcome(
            handle=operation.handle,
            state="refused",
            code=row.code,
            message=row.message,
        )

    outcomes = _outcomes(batch.operations, refused)
    if refused:
        return PatchResult(
            model,
            record,
            (*_rolled_back(outcomes), _refused_row(refused)),
            rolled_back=True,
        )

    retracted = without(
        record.catalog,
        [
            operation.retract
            for operation in batch.operations
            if operation.kind == "retract-assertion" and operation.handle not in refused
        ],
    )
    catalog = merged(retracted, resolution.record.catalog)
    built = AssertionRecord.over(
        catalog,
        resolution.model,
        sources,
        proposed=record.proposed + resolution.record.proposed,
        issues=[*record.issues, *resolution.record.issues],
    )

    broken = _gate_refusals(resolution.model, catalog, sources)
    if broken:
        return PatchResult(
            model,
            record,
            (*_rolled_back(outcomes), _rollback_row(broken)),
            rolled_back=True,
        )
    return PatchResult(resolution.model, built, outcomes, rolled_back=False)


def _batch_refusal(batch: PatchBatch) -> OperationOutcome | None:
    """A batch refused before any operation of it is read.

    The version and the count, in that order and returned alone, on the rule
    :func:`~analysis_service.factbundle.resolve_bundle` already follows: a batch
    written under another spelling is not made readable by naming which of its
    operations also failed a check.
    """
    if batch.patch_version != PATCH_VERSION:
        return OperationOutcome(
            state="refused",
            code="wrong-version",
            message=(
                f"patch_version {batch.patch_version} is not {PATCH_VERSION};"
                " this applicator reads one spelling of the schema"
            ),
        )
    if len(batch.operations) > MAX_OPERATIONS:
        return OperationOutcome(
            state="refused",
            code="too-many-operations",
            message=(
                f"{len(batch.operations)} operations proposed; the cap is"
                f" {MAX_OPERATIONS}"
            ),
        )
    repeated = _repeated(operation.handle for operation in batch.operations)
    if repeated:
        return OperationOutcome(
            state="refused",
            code="duplicate-handle",
            message=(
                f"{', '.join(sorted(repeated))} each name more than one"
                " operation, so no reference to them names one"
            ),
        )
    return None


def _repeated(handles: Iterable[str]) -> frozenset[str]:
    """Every handle claimed more than once."""
    seen: set[str] = set()
    twice: set[str] = set()
    for handle in handles:
        if handle in seen:
            twice.add(handle)
        seen.add(handle)
    return frozenset(twice)


def _shape_refusal(operation: Operation) -> OperationOutcome | None:
    """Whether the operation carries its kind's payload, and only that one."""
    wanted = PAYLOADS[operation.kind].field
    carried = {
        field for field in PAYLOAD_FIELDS if getattr(operation, field) not in (None, "")
    }
    if carried != {wanted}:
        named = ", ".join(sorted(carried)) or "nothing"
        return OperationOutcome(
            handle=operation.handle,
            state="refused",
            code="wrong-payload",
            message=(
                f"a {operation.kind} reads {wanted!r} and this operation"
                f" carries {named}"
            ),
        )
    return None


def _precondition_refusal(
    operation: Operation, model: SystemModel, record: AssertionRecord
) -> OperationOutcome | None:
    """The first precondition the state the batch started from does not hold."""
    held = {element.id for element in model.elements()}
    held |= {assertion_id(entry) for entry in record.catalog.entries}
    for precondition in operation.preconditions:
        there = precondition.target in held
        if there != (precondition.state == "present"):
            return OperationOutcome(
                handle=operation.handle,
                state="refused",
                code="stale-precondition",
                message=(
                    f"{precondition.target!r} is"
                    f" {'present' if there else 'absent'} and this operation"
                    f" was written for it {precondition.state}"
                ),
            )
    return None


def _references(operation: Operation) -> tuple[str, ...]:
    """Every handle or ID one operation names, whatever its kind.

    **The one reader of "what does this operation reach".** The dependency
    graph and the cycle check both ask it, and :attr:`PayloadRule.names` is
    where a reference field is declared, so a bundle row that grows one moves
    both walks together.

    An operation carrying no payload names nothing. It was already refused for
    its shape, and reading a field off ``None`` to say so would raise instead.
    """
    rule = PAYLOADS[operation.kind]
    payload = getattr(operation, rule.field)
    if payload is None:
        return ()
    return tuple(getattr(payload, name) for name in rule.names)


def _dependency_refusals(
    operations: Sequence[Operation],
    by_handle: Mapping[str, Operation],
    refused: Mapping[str, OperationOutcome],
) -> dict[str, OperationOutcome]:
    """Every operation that cannot stand because one it reaches does not.

    Two shapes, and the second is the one a caller forgets. An operation naming
    a refused operation is refused, and so is one naming *that* — the walk
    repeats until the answer stops changing, so a chain of any length falls.
    An operation in a **cycle** is refused too: nothing in it can be applied
    before the rest, and applying a cycle in array order is the silent half a
    dependency chain this pass must not leave.
    """
    found: dict[str, OperationOutcome] = {}
    stopped = set(refused)
    changed = True
    while changed:
        changed = False
        for operation in operations:
            if operation.handle in stopped:
                continue
            broken = [
                reference
                for reference in _references(operation)
                if reference in by_handle and reference in stopped
            ]
            if broken:
                found[operation.handle] = OperationOutcome(
                    handle=operation.handle,
                    state="refused",
                    code="refused-dependency",
                    message=(
                        f"this operation names {', '.join(sorted(broken))},"
                        " which did not land"
                    ),
                )
                stopped.add(operation.handle)
                changed = True
    for handle in _cycles(operations, by_handle, stopped):
        found[handle] = OperationOutcome(
            handle=handle,
            state="refused",
            code="circular-operation",
            message=(
                "this operation and the ones it names reach each other, so none"
                " of them can be applied first"
            ),
        )
    return found


def _cycles(
    operations: Sequence[Operation],
    by_handle: Mapping[str, Operation],
    stopped: Collection[str],
) -> frozenset[str]:
    """Every operation that cannot be ordered after the ones it names.

    Kahn's rule, read the other way round: the operations that *can* be ordered
    are peeled off until none is left, and whatever remains is in a cycle or
    reaches one. Refused operations are out of the graph already, so a cycle
    broken by a refusal is not reported twice.
    """
    pending = {
        operation.handle: {
            reference
            for reference in _references(operation)
            if reference in by_handle and reference not in stopped
        }
        for operation in operations
        if operation.handle not in stopped
    }
    ordered = True
    while ordered:
        ordered = False
        for handle, names in list(pending.items()):
            if not names & set(pending):
                del pending[handle]
                ordered = True
    return frozenset(pending)


def _bundle(additions: Sequence[Operation]) -> SourceFactBundle:
    """The bundle one batch's add-operations describe.

    Each payload takes the **operation's** handle, so the row a reference names
    and the operation that proposed it are one name. The payload's own handle
    is overwritten rather than checked: a field nothing reads cannot disagree
    with anything.
    """
    rows: dict[str, list[BaseModel]] = {
        rule.table: [] for rule in PAYLOADS.values() if rule.table
    }
    for operation in additions:
        rule = PAYLOADS[operation.kind]
        payload = getattr(operation, rule.field)
        rows[rule.table].append(payload.model_copy(update={"handle": operation.handle}))
    return SourceFactBundle.model_validate(rows)


def _outcomes(
    operations: Sequence[Operation], refused: Mapping[str, OperationOutcome]
) -> tuple[OperationOutcome, ...]:
    """One outcome per operation, in the order the batch wrote them."""
    built = []
    for operation in operations:
        stopped = refused.get(operation.handle)
        if stopped is not None:
            built.append(stopped)
        elif operation.kind == "mark-unresolved":
            built.append(
                OperationOutcome(
                    handle=operation.handle,
                    state="unresolved",
                    code="open-question",
                    message=operation.question,
                )
            )
        else:
            built.append(OperationOutcome(handle=operation.handle, state="applied"))
    return tuple(built)


def _gate_refusals(
    model: SystemModel, catalog: AssertionCatalog, sources: Mapping[str, str]
) -> tuple[str, ...]:
    """Every gate code the patched result raises, or nothing.

    The two gates every arm already runs, over the result rather than over the
    patch: a batch that builds a model the validity gate refuses, or a catalog
    the assertion gate refuses, is a batch this service cannot hand on.

    :data:`~analysis_service.assertions.GATE_REFUSALS` is what counts, so the
    one code that is not a refusal does not discard a batch: a row standing
    beside a graph attribute that says the opposite is the defect the assertion
    layer exists to show, and dropping the batch would hide it.
    """
    codes: list[str] = [issue.code for issue in validate(model, sources=sources)]
    codes += [
        issue.code
        for issue in gate_issues(catalog, model, sources)
        if issue.code in GATE_REFUSALS
    ]
    return tuple(sorted(set(codes)))


def _rolled_back(
    outcomes: Sequence[OperationOutcome],
) -> tuple[OperationOutcome, ...]:
    """Every outcome of a discarded batch, reported as the refusal it became."""
    return tuple(
        outcome
        if outcome.state == "refused"
        else outcome.model_copy(
            update={
                "state": "refused",
                "code": "batch-rolled-back",
                "message": "the batch was discarded whole, so this did not land",
            }
        )
        for outcome in outcomes
    )


def _refused_row(refused: Mapping[str, OperationOutcome]) -> OperationOutcome:
    """The batch-level row naming which operations discarded it."""
    return OperationOutcome(
        state="refused",
        code="operation-refused",
        message=(
            f"{', '.join(sorted(refused))} did not land, so the batch was"
            " discarded and the original output stands"
        ),
    )


def _rollback_row(codes: Collection[str]) -> OperationOutcome:
    """The batch-level row saying which gate discarded it."""
    return OperationOutcome(
        state="refused",
        code="gate-refused",
        message=(
            f"the patched result raises {', '.join(codes)}, so the batch was"
            " discarded and the original output stands"
        ),
    )


def gaps(result: PatchResult) -> tuple[DispositionRow, ...]:
    """The questions this pass raised, in the shape the bundle's sidecar uses.

    One sidecar for both stages, so a reader counting what a job left open
    reads one row type whether the extraction raised it or the review did.
    """
    return tuple(
        DispositionRow(
            handle=outcome.handle,
            kind="unresolved",
            disposition="unresolved",
            code=outcome.code,
            message=outcome.message,
        )
        for outcome in result.outcomes
        if outcome.state == "unresolved"
    )
