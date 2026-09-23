"""#926's falsification fixtures: what a corrupted reading costs the instruments.

A recall figure says how much of a signed reference a run recovered. It does
not say that the figure would move if the run were wrong, and a denominator
nobody has tried to cheat measures nothing. These probes try to cheat it.

**Each probe corrupts a perfect reading and states what the corruption must
cost.** The perfect reading is the case's signed reference played back as its
own produced catalog, which every instrument scores as a clean run. One
corruption is applied to it — a fact flipped, a control copied onto another
subject, a flow relabelled, a span removed — and the probe declares what the
two instruments must then say:

* **the gate** (:func:`~analysis_service.assertions.gate_issues`, the one
  reader of "does this catalog pass") refuses the row, by code; or
* **the endpoint** (:class:`~evals.harness.arms.ArmRun`, the one reader of
  required-fact recall) stops counting the row, and the matcher's fate says
  why.

A falsification probe that costs nothing at either instrument is a hole in the
measurement, and :data:`PROBES` is written so that the suite fails when one
appears. A **positive control** costs nothing by design and is declared with
``loses=0`` and no refusal, so the two are never confused.

**Nothing here is an arm.** :data:`PROBES` keys are held apart from
:data:`~evals.harness.arms.ARMS`, and no probe writes a runs file: the
``ArmRun`` a probe builds exists to read the endpoint through its one reader
rather than to recompute recall beside it.

**A criterion nothing probes says why**, in :data:`UNPROBED`. #926's acceptance
list is the registry this table answers to, and a line with neither a probe nor
a reason is the silence the parity rule refuses.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from analysis_service.assertions import (
    Assertion,
    AssertionCatalog,
    Subject,
    assertion_id,
    gate_issues,
)
from analysis_service.system_model import (
    SystemModel,
    flow_id_version,
    flow_label,
    make_flow_id,
    parse_flow_id,
)
from evals.harness.alignment import align
from evals.harness.arms import ArmRun
from evals.harness.modes import AssertionResult
from evals.harness.reference import GoldenCase, load_corpus
from evals.harness.replay import (
    SignedReference,
    classify_elements,
    replay_assertions,
    required_rows,
    signed_reference,
)

#: Every falsification and positive-control line #926's acceptance list names,
#: keyed by the slug :data:`PROBES` and :data:`UNPROBED` answer it under. The
#: registry the table is compared against: a line here with neither a probe nor
#: a recorded reason fails ``tests/test_evals_falsification.py``.
CRITERIA: Mapping[str, str] = MappingProxyType(
    {
        "perfect-reading": (
            "A reading with no corruption in it costs nothing at either"
            " instrument, so every loss below belongs to its own corruption."
        ),
        "mfa-enforced": (
            '"No MFA" changed to "MFA enforced" fails the independent'
            " factual-support evaluation."
        ),
        "support-copied": (
            "Copying receipt-storage authentication onto the processor webhook"
            " is detected as wrong-subject support and cannot quietly remove"
            " the webhook's uncertainty evidence."
        ),
        "empty-citation": (
            "Empty citations cannot yield a fully supported model; blank"
            " citations never count as support."
        ),
        "stopword-mechanism": (
            "Nonsense values and stopword-only mechanisms cannot yield a fully"
            " supported model."
        ),
        "removed-assertion": (
            "Removing a meaningful assertion lowers required-fact recall even"
            " when every remaining compared assertion is correct."
        ),
        "renamed-flow": (
            "Renaming flows cannot remove their facts from evaluation without"
            " visible unmatched coverage."
        ),
        "deleted-parallel-interaction": (
            "Deleting a parallel WebSocket interaction is counted as a missing"
            " interaction and its associated missing facts."
        ),
        "signature-is-not-authentication": (
            "Package-signature verification is distinct from connection authentication."
        ),
        "destination-is-not-authentication": (
            "Missing fax destination verification does not become a present"
            " authentication safeguard."
        ),
        "control-name-elsewhere": (
            "The same control name on unrelated components does not satisfy"
            " subject-specific support."
        ),
        "absence-inferred": ("Stated and inferred absences are distinct."),
        "scope-dropped": (
            "A conditional or scoped control is not generalized to all principals."
        ),
        "paraphrase": (
            "Correct paraphrases are preserved; matching every literal token is"
            " not required."
        ),
        "valid-inference": ("Clearly marked valid inferences are preserved."),
        "same-name-two-environments": (
            "Two same-named services in different environments, repeated"
            " quotations, and multiple sources for one attribute retain correct"
            " identity and provenance."
        ),
        "changed-source": (
            "Changing or removing source evidence invalidates affected support"
            " assessments."
        ),
        "output-limit": (
            "Input/output limits produce explicit incomplete or failed outcomes"
            " rather than an apparently complete model."
        ),
        "reviewed-shapes": (
            "Questions, proposed components, hedges, self-corrections,"
            " independent source conflicts, compatible refinements and source"
            " silence have reviewed expected outcomes."
        ),
    }
)

#: Why a criterion has no probe here. Each names the instrument that decides it
#: instead, because a probe needs a corruption the endpoint or the gate can
#: charge, and three of these are decided before either one reads a catalog.
UNPROBED: Mapping[str, str] = MappingProxyType(
    {
        "same-name-two-environments": (
            "No corpus case holds two same-named services in different"
            " environments, so a probe would corrupt a fixture nobody signed."
            " The repeated-quotation half is the gate's `ambiguous-span`, held"
            " by tests/test_assertions.py."
        ),
        "changed-source": (
            "The gate's `stale-digest` decides it, and no assessment reads it"
            " yet: every `Assertion.assessment` in this service is `unchecked`,"
            " so there is no support assessment for a changed source to"
            " invalidate. Held by tests/test_assertions.py."
        ),
        "output-limit": (
            "The gate's `too-many-assertions` and `too-many-spans` decide it"
            " over a catalog no reference is the size of. Held by"
            " tests/test_assertions.py."
        ),
        "reviewed-shapes": (
            "The signed reference is the reviewed expectation: a hedged"
            " unknown, a silent unknown and a self-correction are each a row in"
            " it, and `perfect-reading` is what says the instruments read them"
            " as the reviewer ruled."
        ),
    }
)


@dataclass(frozen=True)
class Corruption:
    """One corrupted reading: what the run produced, and what it aimed at.

    ``touched`` names the reference rows the corruption is about, so a probe
    declares the fate of the rows it meant to move rather than a fate table
    over the whole case.

    ``model`` is the graph the run produced, where the corruption removes or
    renames an element as well as its facts. ``None`` leaves the blessed graph,
    which is what every catalog-only probe runs against.

    **A corruption that moves a subject moves the graph and the subject list
    with it.** Otherwise the gate refuses the row as ``dangling-subject``
    before the matcher reads it, and the probe would measure a catalog no run
    could produce rather than the rule it names.
    """

    entries: tuple[Assertion, ...]
    touched: tuple[str, ...] = ()
    model: SystemModel | None = None
    #: The subjects the run declared, where the corruption renames or drops
    #: one. ``None`` leaves the reference's own, which is what every probe that
    #: moves no subject runs against.
    subjects: tuple[Subject, ...] | None = None


@dataclass(frozen=True)
class Probe:
    """One corruption and what it must cost.

    ``loses`` is how many of the case's required rows the corruption must stop
    the endpoint counting, and ``fates`` is what the matcher must say about the
    rows it touched, in the order ``Corruption.touched`` names them.

    ``refuses`` is the gate codes the corrupted catalog must raise, and
    ``elements`` the fate of each blessed element the corrupted graph no longer
    holds. A probe declaring none of these and no fate but ``found`` is a
    positive control, and :attr:`control` is how the suite tells the two apart.

    ``credits`` is how many touched rows the looser, label-dropping reading
    answers where the strict one does not (#1015). It is a reading reported
    beside the endpoint and never inside it, so a probe states it rather than
    leaving a reader to assume the two agree.
    """

    case_id: str
    corrupt: Callable[[SignedReference, GoldenCase], Corruption]
    loses: int = 0
    fates: tuple[str, ...] = ()
    refuses: tuple[str, ...] = ()
    credits: int = 0
    elements: tuple[str, ...] = ()

    @property
    def control(self) -> bool:
        """Whether this probe declares a reading that costs nothing anywhere.

        A positive control, and the only kind of probe allowed to cost nothing:
        no required row lost, no gate refusal, no element missing, and every
        touched row still answered.
        """
        costs = self.loses or self.refuses or self.elements
        return not costs and set(self.fates) <= {"found"}

    @property
    def endpoint_blind(self) -> bool:
        """Whether a real corruption costs every instrument but required-fact recall.

        **The measurement's own limit, named rather than left to be noticed.**
        A corruption of a fact the reference rules ``unknown`` moves no recall,
        because the endpoint's denominator is the stated rows; one the matcher
        cannot see at all moves no recall either. Both are still corruptions,
        and both are caught — by a row fate, an element fate or a gate code.
        """
        return not self.control and not self.loses


def _find(
    reference: SignedReference,
    predicate: str,
    *,
    flow: str = "",
    subject: str = "",
    value: str = "",
) -> Assertion:
    """The one signed row a probe names, or a refusal saying what it asked for.

    ``flow`` matches a **Data Flow** subject by its label, read with
    :func:`~analysis_service.system_model.flow_label` rather than by splitting
    the ID.
    """
    for entry in reference.entries:
        if entry.predicate != predicate:
            continue
        if subject and entry.subject != subject:
            continue
        if value and entry.value != value:
            continue
        if flow and _label(entry.subject) != flow:
            continue
        return entry
    asked = f"{predicate} flow={flow!r} subject={subject!r} value={value!r}"
    raise KeyError(f"no signed row for {asked}")


def _label(subject: str) -> str:
    """One flow subject's label, or ``""`` for a subject that is not a flow.

    :func:`~analysis_service.system_model.flow_label` is the one reader of what
    a flow calls itself, so this asks it rather than splitting the ID.
    """
    try:
        return flow_label(subject)
    except (KeyError, ValueError):
        return ""


def _relabelled(subject: str, label: str) -> str:
    """The same interaction under another label, built by the identity rule."""
    version = flow_id_version(subject)
    parts = parse_flow_id(subject, version)
    return make_flow_id(parts.source, parts.destination, label, version)


def _instead(
    reference: SignedReference, replacements: Mapping[str, Assertion | None]
) -> tuple[Assertion, ...]:
    """The reference's rows with named ones replaced, or dropped for ``None``."""
    kept = []
    for entry in reference.entries:
        if assertion_id(entry) not in replacements:
            kept.append(entry)
            continue
        found = replacements[assertion_id(entry)]
        if found is not None:
            kept.append(found)
    return tuple(kept)


def _one(
    reference: SignedReference, row: Assertion, found: Assertion | None
) -> Corruption:
    """One row replaced or dropped, with that row as what the probe aimed at."""
    return Corruption(
        _instead(reference, {assertion_id(row): found}), (assertion_id(row),)
    )


def _moved_subjects(
    reference: SignedReference, moves: Mapping[str, str]
) -> tuple[Subject, ...]:
    """The reference's subjects with named IDs moved, and ``""`` dropping one."""
    kept = []
    for subject in reference.subjects:
        if subject.id not in moves:
            kept.append(subject)
            continue
        moved = moves[subject.id]
        if moved:
            kept.append(
                subject.model_copy(
                    update={"id": moved, "label": _label(moved) or moved}
                )
            )
    return tuple(kept)


def _without_flow(case: GoldenCase, flow_id: str) -> SystemModel:
    """The blessed graph with one interaction removed."""
    raw = case.model.model_dump(mode="json")
    raw["data_flows"] = [flow for flow in raw["data_flows"] if flow["id"] != flow_id]
    return SystemModel.model_validate(raw)


def _renamed_model(case: GoldenCase, flow_id: str, label: str) -> SystemModel:
    """The blessed graph with one interaction under another label.

    A **Data Flow** carries its label inside its identity, so renaming one is
    rebuilding its ID and nothing else.
    """
    raw = case.model.model_dump(mode="json")
    for flow in raw["data_flows"]:
        if flow["id"] == flow_id:
            flow["id"] = _relabelled(flow_id, label)
    return SystemModel.model_validate(raw)


def _perfect(reference: SignedReference, case: GoldenCase) -> Corruption:
    """No corruption at all: the reading every other probe is a corruption of."""
    return Corruption(tuple(reference.entries))


def _mfa_enforced(reference: SignedReference, case: GoldenCase) -> Corruption:
    """The stated absence of MFA, written as MFA in force."""
    row = _find(reference, "mfa-requirement")
    return _one(reference, row, row.model_copy(update={"value": "required"}))


def _support_copied(reference: SignedReference, case: GoldenCase) -> Corruption:
    """The receipt archive's authentication, re-subjected to the webhook.

    The reference holds the webhook's own authentication as a hedged unknown.
    The copy is the audit's original defect in full: the control the source
    states about one interaction is written onto another, *and* the hedge that
    interaction actually carries is gone.
    """
    archive = _find(reference, "authentication-mechanism", flow="append-receipt")
    webhook = _find(reference, "authentication-mechanism", flow="settlement-webhook")
    moved = archive.model_copy(update={"subject": webhook.subject})
    return Corruption(
        _instead(
            reference, {assertion_id(archive): moved, assertion_id(webhook): None}
        ),
        (assertion_id(archive), assertion_id(webhook)),
    )


def _empty_citation(reference: SignedReference, case: GoldenCase) -> Corruption:
    """A stated fact with its span removed, and the value left right.

    The matcher cannot see this: support is not part of an identity and not
    part of the certainty three fields. The gate is the reader that can.
    """
    row = _find(reference, "data-classification")
    return _one(reference, row, row.model_copy(update={"support": []}))


def _stopword_mechanism(reference: SignedReference, case: GoldenCase) -> Corruption:
    """A mechanism written as function words, with its span left in place."""
    row = _find(reference, "authentication-mechanism", flow="read-write-orders")
    return _one(reference, row, row.model_copy(update={"value": "the of and a"}))


def _removed_assertion(reference: SignedReference, case: GoldenCase) -> Corruption:
    """One meaningful row dropped, every remaining row correct."""
    return _one(reference, _find(reference, "data-classification"), None)


def _renamed_flow(reference: SignedReference, case: GoldenCase) -> Corruption:
    """Both facts about one interaction, restated under another label."""
    rows = [
        _find(reference, predicate, flow="append-receipt")
        for predicate in ("transport-encryption", "authentication-mechanism")
    ]
    moved = _relabelled(rows[0].subject, "archive-receipt")
    renamed = {
        assertion_id(row): row.model_copy(update={"subject": moved}) for row in rows
    }
    return Corruption(
        _instead(reference, renamed),
        tuple(assertion_id(row) for row in rows),
        model=_renamed_model(case, rows[0].subject, "archive-receipt"),
        subjects=_moved_subjects(reference, {rows[0].subject: moved}),
    )


def _deleted_parallel(reference: SignedReference, case: GoldenCase) -> Corruption:
    """The WebSocket interaction deleted, its facts moved onto its sibling.

    Two interactions run between the console and the API; only one is the held
    socket. A run that emits one generic interaction carrying the socket's
    facts is the collapse #926 asks the evaluation to count, and the graph it
    produced holds no socket either.
    """
    rows = [
        _find(reference, predicate, flow="live-job-status")
        for predicate in ("transport-encryption", "authentication-mechanism")
    ]
    socket = rows[0].subject
    sibling = _relabelled(socket, "dispatch-requests")
    moved = {
        assertion_id(row): row.model_copy(update={"subject": sibling}) for row in rows
    }
    return Corruption(
        _instead(reference, moved),
        tuple(assertion_id(row) for row in rows),
        model=_without_flow(case, socket),
        subjects=_moved_subjects(reference, {socket: sibling}),
    )


def _signature_as_authentication(
    reference: SignedReference, case: GoldenCase
) -> Corruption:
    """The stated absence of signature checking, answered with a TLS claim."""
    row = _find(reference, "signature-verification")
    return _one(
        reference,
        row,
        row.model_copy(
            update={
                "predicate": "authentication-mechanism",
                "value": "TLS to the package registry",
            }
        ),
    )


def _destination_as_authentication(
    reference: SignedReference, case: GoldenCase
) -> Corruption:
    """The unverified fax destination, answered with a connection control."""
    row = _find(reference, "destination-verification")
    return _one(
        reference,
        row,
        row.model_copy(
            update={
                "predicate": "authentication-mechanism",
                "value": "the gateway signs in to the fax carrier",
            }
        ),
    )


def _control_name_elsewhere(reference: SignedReference, case: GoldenCase) -> Corruption:
    """One component's storage encryption, restated about another component."""
    row = _find(reference, "storage-encryption")
    return _one(reference, row, row.model_copy(update={"subject": "store:orders-db"}))


def _absence_inferred(reference: SignedReference, case: GoldenCase) -> Corruption:
    """A stated absence, answered as one a reader worked out."""
    row = _find(reference, "authentication-mechanism", flow="submit-order")
    return _one(
        reference,
        row,
        row.model_copy(
            update={
                "basis": "inferred",
                "explanation": "nothing in the source names a check",
                "support": [],
            }
        ),
    )


def _scope_dropped(reference: SignedReference, case: GoldenCase) -> Corruption:
    """A grant over one resource and one operation, restated over everything."""
    row = _find(
        reference, "authorization-grant", subject="principal:application-account"
    )
    return _one(reference, row, row.model_copy(update={"scope": []}))


def _paraphrase(reference: SignedReference, case: GoldenCase) -> Corruption:
    """One text value in other words, saying the same thing."""
    row = _find(reference, "storage-encryption")
    return _one(
        reference, row, row.model_copy(update={"value": "a key the customer manages"})
    )


def _valid_inference(reference: SignedReference, case: GoldenCase) -> Corruption:
    """Every inferred row restated as inferred, with its explanation kept."""
    return Corruption(
        tuple(reference.entries),
        tuple(
            assertion_id(entry)
            for entry in reference.entries
            if entry.basis == "inferred"
        ),
    )


#: Every probe, keyed by the criterion it answers. A key here is never an arm:
#: ``tests/test_evals_falsification.py`` holds the two namespaces apart.
PROBES: Mapping[str, Probe] = MappingProxyType(
    {
        "perfect-reading": Probe("01-payments-checkout", _perfect),
        "mfa-enforced": Probe(
            "01-payments-checkout", _mfa_enforced, loses=1, fates=("wrong_value",)
        ),
        "support-copied": Probe(
            "01-payments-checkout",
            _support_copied,
            loses=1,
            fates=("omitted", "wrong_value"),
        ),
        "empty-citation": Probe(
            "01-payments-checkout",
            _empty_citation,
            fates=("found",),
            refuses=("unsupported-assertion",),
        ),
        "stopword-mechanism": Probe(
            "01-payments-checkout", _stopword_mechanism, loses=1, fates=("worded",)
        ),
        "removed-assertion": Probe(
            "01-payments-checkout", _removed_assertion, loses=1, fates=("omitted",)
        ),
        "renamed-flow": Probe(
            "01-payments-checkout",
            _renamed_flow,
            loses=2,
            fates=("omitted", "omitted"),
            credits=2,
            elements=("renamed",),
        ),
        "deleted-parallel-interaction": Probe(
            "13-dispatch-control-plane",
            _deleted_parallel,
            fates=("omitted", "omitted"),
            credits=2,
            elements=("omitted",),
        ),
        "signature-is-not-authentication": Probe(
            "07-cicd-store-deploy",
            _signature_as_authentication,
            loses=1,
            fates=("omitted",),
        ),
        "destination-is-not-authentication": Probe(
            "09-cookbook-sokify-retail",
            _destination_as_authentication,
            loses=1,
            fates=("omitted",),
        ),
        "control-name-elsewhere": Probe(
            "01-payments-checkout", _control_name_elsewhere, loses=1, fates=("omitted",)
        ),
        "absence-inferred": Probe(
            "01-payments-checkout",
            _absence_inferred,
            loses=1,
            fates=("wrong_certainty",),
            credits=1,
        ),
        "scope-dropped": Probe(
            "01-payments-checkout",
            _scope_dropped,
            loses=1,
            fates=("rescoped",),
            refuses=("missing-scope",),
        ),
        "paraphrase": Probe(
            "01-payments-checkout", _paraphrase, loses=1, fates=("worded",)
        ),
        "valid-inference": Probe(
            "01-payments-checkout",
            _valid_inference,
            fates=("found",) * 5,
        ),
    }
)


@dataclass(frozen=True)
class Outcome:
    """What one probe's corruption actually cost, beside what it must cost."""

    probe: str
    case_id: str
    required: int
    recovered: int
    #: The required rows the perfect reading answered and this one did not.
    lost: tuple[str, ...]
    #: The fate of each row the corruption aimed at, in the order it names them.
    fates: tuple[str, ...]
    #: The gate's codes over the corrupted catalog, deduplicated and sorted.
    refused: tuple[str, ...]
    #: Touched rows only the label-dropping reading answers.
    credited: tuple[str, ...]
    #: The fate of each blessed element the corrupted graph no longer holds.
    elements: tuple[str, ...]
    #: Why the probe's declaration did not hold, empty where it did.
    broke: tuple[str, ...] = ()

    @property
    def held(self) -> bool:
        return not self.broke


def run_probe(name: str, case: GoldenCase, reference: SignedReference) -> Outcome:
    """Corrupt this case's perfect reading one way, and read both instruments."""
    probe = PROBES[name]
    corrupted = probe.corrupt(reference, case)
    subjects = (
        reference.subjects if corrupted.subjects is None else list(corrupted.subjects)
    )
    catalog = AssertionCatalog(subjects=subjects, entries=list(corrupted.entries))
    model = case.model if corrupted.model is None else corrupted.model
    sources = {source.label: source.text for source in case.sources}
    refused = sorted({issue.code for issue in gate_issues(catalog, model, sources)})
    graded = replay_assertions(
        case, reference, AssertionResult(case.id, {}, catalog, ())
    )
    run = ArmRun.of(graded, reference, arm=name)
    fates = {row.reference: row.fate for row in graded.rows}
    required = frozenset(required_rows(reference))
    element_fates = {
        row.reference: row.fate
        for row in classify_elements(case.model, model, align(case, model))
    }
    outcome = Outcome(
        probe=name,
        case_id=case.id,
        required=run.required,
        recovered=run.recovered,
        lost=tuple(
            sorted(row for row in required if fates.get(row, "omitted") != "found")
        ),
        fates=tuple(fates.get(row, "omitted") for row in corrupted.touched),
        refused=tuple(refused),
        credited=tuple(sorted(graded.credited & set(corrupted.touched))),
        elements=tuple(
            sorted(
                fate
                for element, fate in element_fates.items()
                if fate != "found"
                and element not in {one.id for one in model.elements()}
            )
        ),
    )
    return outcome.__class__(**{**vars(outcome), "broke": _broke(probe, outcome)})


def _broke(probe: Probe, outcome: Outcome) -> tuple[str, ...]:
    """Every way one outcome disagrees with what its probe declared."""
    broke = []
    if len(outcome.lost) != probe.loses:
        broke.append(
            f"lost {len(outcome.lost)} required row(s), declared {probe.loses}"
        )
    if outcome.fates != probe.fates:
        broke.append(f"fates {outcome.fates}, declared {probe.fates}")
    if outcome.refused != probe.refuses:
        broke.append(f"gate said {outcome.refused}, declared {probe.refuses}")
    if len(outcome.credited) != probe.credits:
        broke.append(
            f"the looser reading credited {len(outcome.credited)},"
            f" declared {probe.credits}"
        )
    if outcome.elements != probe.elements:
        broke.append(f"elements {outcome.elements}, declared {probe.elements}")
    return tuple(broke)


def outcomes(corpus_dir: Path) -> list[Outcome]:
    """Every probe, run. A probe whose case nobody signed raises rather than passes."""
    cases = {case.id: case for case in load_corpus(corpus_dir)}
    found = []
    for name, probe in PROBES.items():
        case = cases[probe.case_id]
        reference = signed_reference(corpus_dir, case)
        if reference is None:
            raise KeyError(
                f"{name} runs on {probe.case_id}, whose reference nobody signed"
            )
        found.append(run_probe(name, case, reference))
    return found


def render(found: Sequence[Outcome]) -> str:
    """The probe table, as text."""
    lines = [
        "## What a corrupted reading costs",
        "",
        (
            "Each row corrupts one case's signed reference, plays it back as the"
            " run's own catalog, and reads the gate and the endpoint. A"
            " falsification probe that costs nothing at either instrument is a"
            " hole in the measurement."
        ),
        "",
        "| probe | case | required | kept | lost | fates | gate | relabelled |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for row in found:
        lines.append(
            f"| `{row.probe}` | {row.case_id} | {row.required} | {row.recovered}"
            f" | {len(row.lost)} | {', '.join(row.fates) or '—'}"
            f" | {', '.join(row.refused) or '—'} | {len(row.credited)} |"
        )
    broken = [row for row in found if not row.held]
    blind = [name for name, probe in PROBES.items() if probe.endpoint_blind]
    lines += [
        "",
        f"**{len(found) - len(broken)} of {len(found)} probes hold.**",
        "",
        (
            f"{len(blind)} corruption(s) cost required-fact recall nothing and"
            f" are caught elsewhere: {', '.join(f'`{name}`' for name in blind)}."
            " Recall counts the stated rows, so a corrupted unknown and a"
            " removed span both read as clean runs at the endpoint."
        ),
        "",
    ]
    for row in broken:
        for why in row.broke:
            lines.append(f"- `{row.probe}`: {why}")
    for name, why in sorted(UNPROBED.items()):
        lines.append(f"- no probe for `{name}`: {why}")
    return "\n".join(lines) + "\n"


def command_falsify(args: argparse.Namespace) -> int:
    """Run every probe. It runs no model and reads no credential."""
    found = outcomes(Path(args.corpus))
    print(render(found), end="")
    return 0 if all(row.held for row in found) else 1
