"""Holding a **Case Sitting**: what it reads, and what it writes when it ends.

The act is #327's, and ``evals/BLESSING.md`` step 6 is the method. This module
is the part a front end does not get to reinvent: which files a sitting must
read, the digest of each as it stood, what a reader may say about one recorded
finding and the key that mark files under, the append-only entry that records
it, the line it clears in the unreviewed list, whether a recorded sitting
clears its case at all, and the rail of every case with the status that rule
gives it. ``webapp/sitting.py`` is one surface over this;
the CLI path writes the same files by hand and the checks cannot tell them
apart, which is the point — one implementation of the rules. CI reads
:func:`clears` through ``tests/test_case_review.py``, so no surface can call a
case read while CI still asks somebody to read it.

**The own list comes first, and that is a property rather than an
instruction.** A reader who opens the recorded sets first finds them
reasonable and the sitting measures nothing. So a caller here asks for part
one and part two separately, and :func:`parts_after` is what a surface must
withhold until the reader has written their own list down. That mirrors the
review app's configuration-blindness, which is enforced by the queue item
having no field for it rather than by asking the reviewer not to peek.

**A mark is keyed by the finding, never by its position.** The reader answers
one recorded finding with one of :data:`MARKS`, and
:func:`~evals.harness.fingerprint.key_claim` computes the key. An insertion
into a claim file moves every position below it and moves no fingerprint, so a
mark recorded today still names the same finding after somebody edits the set.
The same key a vote is filed under, so improving the identity rule re-keys both
by recomputation.

Nothing here talks to a network or a provider. A sitting is reading, and the
whole path is free.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

from evals import build_review_docs as docs
from evals.harness.fingerprint import FingerprintError, key_claim
from evals.harness.identity import FlowMap
from evals.harness.reference import (
    CLAIMS_DIR,
    CaseSitting,
    GoldenCase,
    ReadRecord,
    ReferenceClaim,
    load_case,
    load_corpus,
)
from evals.harness.roster import Roster
from stride_service.report import FrameworkName

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "evals" / "corpus"

#: Where the unreviewed list lives. A sitting that does not clear its line
#: leaves the count a lie, so ``submit sitting`` refuses one that has not.
UNREVIEWED_FILE = "tests/test_case_review.py"

#: What a reader may say about one recorded finding. The method's own closed
#: set, which ``evals.build_review_docs.MARK_GUIDANCE`` writes out for the
#: reader who fills the document by hand — free prose here would record less
#: than that path does, and no count could be taken over it.
Mark = Literal["agree", "doubt", "dup"]

#: The same three, for a surface that offers them and a check that reads them.
MARKS: tuple[Mark, ...] = get_args(Mark)

#: What the rail says about a case no sitting clears. The other status a row
#: can carry names the signer, and it is spelled where it is computed.
TO_DO = "to do"


class SittingError(ValueError):
    """The sitting cannot be recorded; the message says what stops it."""


def claim_files(frameworks: Iterable[str]) -> list[str]:
    """One reference set per declared framework, named by the declaration.

    Derived rather than listed, so a **Framework Package** nobody wrote yet
    arrives the moment a case declares it. Both the reading a sitting must
    cover and the files a sitting submission may change read this.
    """
    return [f"{CLAIMS_DIR}/{name}.json" for name in frameworks]


def document_name(reviewer: str) -> str:
    """The filled reading document one reader writes beside a case.

    Spelled once: the app writes this name, and ``submit sitting`` admits
    this name and no other under the case prefix. A document under another
    name is another reader's, and a reader may not change one (#388).
    """
    return f"REVIEW-{reviewer}.md"


def required_files(frameworks: Iterable[str]) -> list[str]:
    """What a complete sitting reads, derived from the case's own declaration.

    The shared artefacts plus one reference set per declared framework, so a
    case that gains a package requires its set read by construction and no
    table here needs editing. The caller passes the declared names, because
    the declaration reaches this module as raw JSON on one path and as a
    loaded :class:`~evals.harness.reference.CaseMetadata` on the other.
    """
    return ["source.md", "model.json", *claim_files(frameworks)]


def drifted(case: GoldenCase, sitting: CaseSitting, corpus_dir: Path) -> list[str]:
    """The read files whose bytes no longer match the sitting's digests.

    The corpus directory is passed rather than defaulted, because a caller
    that means a temporary tree must not silently read the shipped one.
    """
    case_dir = corpus_dir / case.meta.id
    stale = []
    for record in sitting.read:
        target = case_dir / record.file
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != record.sha256
        ):
            stale.append(record.file)
    return stale


def clears(
    case: GoldenCase, sitting: CaseSitting, roster: Roster, corpus_dir: Path
) -> bool:
    """Whether this sitting takes its case off the unreviewed list.

    A sitting clears when a rostered person read every required file and the
    digests it recorded still match the tree (#327). This is a different
    question from whether the case carries an entry: a drifted digest leaves
    an entry that clears nothing, and the case is unread again. Every surface
    that greys a case asks this one, so no page can call a case read while CI
    still asks somebody to read it.
    """
    required = set(required_files(declared.name for declared in case.meta.frameworks))
    covered = not required - {record.file for record in sitting.read}
    return (
        covered
        and sitting.reviewer in roster
        and not drifted(case, sitting, corpus_dir)
    )


@dataclass(frozen=True)
class Row:
    """One rail row: a case a reader sees, and whether they may open it.

    It carries the case number, the title and the status, and nothing else.
    A claim count or a reason the case waits would tell the reader how long
    to make their own list before they have written it, which is the one
    thing the page must never say.
    """

    case_id: str
    number: str
    title: str
    status: str
    #: Whether the reader may open this case. A row that does not press is
    #: also off the offered list, so the refusal a signed case needs costs no
    #: code of its own.
    pressable: bool


def rail(corpus_dir: Path, roster: Roster) -> tuple[Row, ...]:
    """Every case in the corpus, in corpus order, with the status a reader reads.

    **The status comes from :func:`clears`, never from the presence of an
    entry in ``reviews``.** Those are different questions: a drifted digest
    leaves an entry that clears nothing, CI puts that case back on the
    unreviewed list, and a rail keyed on the entry would grey a case CI asks
    somebody to read.

    A case a sitting clears is not pressable, whoever signed it. The status
    names the signer, which reads correctly either way.
    """
    return tuple(
        _row(case, _signer(case, roster, corpus_dir))
        for case in load_corpus(corpus_dir)
    )


def _signer(case: GoldenCase, roster: Roster, corpus_dir: Path) -> str | None:
    """Who signed this case off, or ``None`` while nobody has."""
    return next(
        (
            sitting.reviewer
            for sitting in case.meta.reviews
            if clears(case, sitting, roster, corpus_dir)
        ),
        None,
    )


def _row(case: GoldenCase, signer: str | None) -> Row:
    return Row(
        case_id=case.meta.id,
        number=case.meta.id.split("-")[0],
        title=case.meta.title,
        status=f"signed by {signer}" if signer else TO_DO,
        pressable=signer is None,
    )


def read_records(case_dir: Path, files: list[str]) -> list[ReadRecord]:
    """Each file pinned to the bytes it holds now.

    Taken at the moment the sitting is recorded, so the entry signs what will
    merge. A later edit to any of them moves the digest and puts the case
    back on the unreviewed list.
    """
    records = []
    for name in files:
        path = case_dir / name
        if not path.is_file():
            raise SittingError(
                f"{name}: not in the case directory, so it cannot be read"
            )
        records.append(
            ReadRecord(file=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        )
    return records


@dataclass(frozen=True)
class MarkTarget:
    """One recorded finding a reader marks, and the key its mark files under.

    A **Claim**'s identity is its fingerprint, so two recorded claims the rule
    calls one finding are one target and take one mark. :attr:`claims` names
    every claim the target covers, because a reader who marks one of them has
    to see that the same mark answers the other.

    **The claim sentence is what anchors a target to the text beside it**, and
    no position rides here. Each package renders its own part and numbers it in
    its own terms, so a number computed here would agree with one renderer and
    read wrong under the next.
    """

    fingerprint: str
    framework: FrameworkName
    claims: tuple[str, ...]


@dataclass(frozen=True)
class Prepared:
    """One case, ready to be sat with. Part two is deliberately separate."""

    case_id: str
    title: str
    part_one: str
    #: Framework -> the recorded set as the reading document renders it. A
    #: surface must not send this until the reader's own list is in.
    part_two: dict[str, str]
    files: list[str]
    #: One target per recorded finding, in the order the parts render. Part of
    #: part two rather than part one: a target names a claim, and a count of
    #: them is a count of the recorded set. No default, because a ``Prepared``
    #: built without them would refuse every mark a reader made.
    mark_targets: tuple[MarkTarget, ...]


def mark_targets(case: GoldenCase) -> tuple[MarkTarget, ...]:
    """Every recorded finding of one case, keyed by its own framework's rule.

    Keyed through :func:`~evals.harness.fingerprint.key_claim`, which is the
    single spelling of which version keys which package. A position would do
    for a page and for nothing else: an insertion into a claim file moves every
    position below it, so a mark recorded against ``stride:9`` would answer for
    a different claim the next time the file is read.

    The claim offers both an action verb and a catalog identifier, and the
    version keeps whichever it reads — so this states no rule about which
    package carries which, and a package that ships a new record type arrives
    here through its own answers.
    """
    flows: FlowMap = {
        flow.id: (flow.source, flow.destination) for flow in case.model.data_flows
    }
    grouped: dict[str, list[str]] = {}
    frameworks: dict[str, FrameworkName] = {}
    for framework in case.frameworks:
        for claim in case.claims_for(framework):
            value = _key_of(case.id, framework, claim, flows)
            grouped.setdefault(value, []).append(claim.claim)
            frameworks[value] = framework
    return tuple(
        MarkTarget(fingerprint=value, framework=frameworks[value], claims=tuple(claims))
        for value, claims in grouped.items()
    )


def _key_of(
    case_id: str, framework: FrameworkName, claim: ReferenceClaim, flows: FlowMap
) -> str:
    """One reference claim's fingerprint, or a refusal that names the claim.

    A claim the rule cannot key is a claim nobody can mark, and the reader
    meets it as a case that will not open. So the refusal names the case and
    the sentence, rather than reaching the reader as the identity rule's own
    message about a component it wanted.
    """
    try:
        value, _ = key_claim(
            framework,
            claim.lane,
            claim.affected_element_ids,
            flows,
            verb=claim.verb,
            identifier=claim.identifier,
        )
    except FingerprintError as exc:
        raise SittingError(
            f"{case_id}: {framework} cannot key {claim.claim!r}, so no mark"
            f" would name it — {exc}"
        ) from exc
    return value


def prepare(case_dir: Path) -> Prepared:
    """Everything a sitting needs, split at the own-list boundary."""
    case = load_case(case_dir)
    return Prepared(
        case_id=case.id,
        title=case.meta.title,
        part_one=docs.part_one(case_dir),
        part_two=docs.parts_after(case_dir),
        files=required_files(case.frameworks),
        mark_targets=mark_targets(case),
    )


def document(
    prepared: Prepared,
    own_list: list[str],
    marks: Mapping[str, Mark],
    missing: list[str],
    notes: str,
) -> str:
    """The filled reading document — the evidence that the method ran.

    Only the filled copy shows the own list was written before the recorded
    sets were opened, which is the one thing a generated ``REVIEW.md`` cannot
    show. ``submit sitting`` checks it exists; a reader checks it means
    something.

    ``marks`` is keyed by fingerprint, and a key naming no recorded finding of
    this case refuses the whole document. It is either a page that lost its
    targets or a request that never read one, and writing it would put a mark
    in the evidence that answers nothing.
    """
    unknown = sorted(
        set(marks) - {target.fingerprint for target in prepared.mark_targets}
    )
    if unknown:
        raise SittingError(
            f"{prepared.case_id}: {', '.join(unknown)} names no recorded"
            " finding of this case, so the mark answers nothing"
        )
    lines = [
        f"# Case Sitting — `{prepared.case_id}`\n",
        f"\n**{prepared.title}**\n",
        (
            "\nHeld through `webapp/sitting.py`. The own list below was"
            " written before the recorded sets were shown.\n"
        ),
        "\n---\n",
        prepared.part_one,
        "\n## Your list, written first\n",
    ]
    lines += [f"- {item}" for item in own_list] or ["- (nothing)"]
    for framework, body in prepared.part_two.items():
        lines.append(f"\n---\n\n## The recorded `{framework}` set\n")
        lines.append(body)
        # Selected off the target's framework, which the fingerprint's own
        # components carry. A key prefix would read the identity's spelling
        # rather than the identity.
        answered = [
            target
            for target in prepared.mark_targets
            if target.framework == framework and target.fingerprint in marks
        ]
        if answered:
            lines.append("\n### Marks\n")
            lines += [
                _mark_line(target, marks[target.fingerprint]) for target in answered
            ]
    lines.append("\n---\n\n## On your list and not on theirs\n")
    lines += [f"- {item}" for item in missing] or ["- (nothing)"]
    if notes:
        lines.append(f"\n## Notes\n\n{notes}\n")
    return "\n".join(lines) + "\n"


def _mark_line(target: MarkTarget, mark: Mark) -> str:
    """One mark, the finding it answers, and the claim it reads against.

    The fingerprint is written out because it is the identity a later reader
    matches a vote against. The claim sentence rides beside it so the line
    means something to a person, and it is the same sentence the part above
    prints.
    """
    return f"- `{mark}` — `{target.fingerprint}` — {' / '.join(target.claims)}"


def record(
    case_dir: Path,
    reviewer: str,
    read: list[ReadRecord],
    document_name: str,
    notes: str,
) -> dict[str, Any]:
    """Append the sitting to ``case.json``'s ``reviews``, and return the entry.

    Append-only: a correction is a new entry, never an edit to one recorded
    (#327). The file is rewritten whole because it is small and JSON has no
    append, but nothing already in ``reviews`` is touched.
    """
    path = case_dir / "case.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    entry = {
        "reviewer": reviewer,
        "date": datetime.now(UTC).date().isoformat(),
        "read": [{"file": item.file, "sha256": item.sha256} for item in read],
        "document": document_name,
        "notes": notes,
    }
    meta.setdefault("reviews", []).append(entry)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entry


def _unreviewed_entries(source: str) -> list[tuple[str, int, int]]:
    """Each ``UNREVIEWED`` entry as ``(case id, first line, last line)``.

    Read through :mod:`ast` rather than by matching text, because the entry
    prose is arbitrary English: counting brackets to find where an entry ends
    works only while no reason writes one, and the reasons cite issues. The
    parser knows where every entry starts, so an entry runs to the line before
    the next one — which is what carries the trailing comma, the closing
    parenthesis and any comment with it, whatever shape they were written in.

    Line numbers are 0-based and the end is exclusive, ready to slice.
    """
    tree = ast.parse(source)
    table = next(
        (
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign | ast.Assign)
            and isinstance(node.value, ast.Dict)
            and any(
                isinstance(target, ast.Name) and target.id == "UNREVIEWED"
                for target in (
                    [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                )
            )
        ),
        None,
    )
    if not isinstance(table, ast.Dict):
        raise SittingError(f"{UNREVIEWED_FILE}: no UNREVIEWED table to read")

    starts = [
        (key.value, key.lineno - 1)
        for key in table.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    if len(starts) != len(table.keys):
        raise SittingError(
            f"{UNREVIEWED_FILE}: UNREVIEWED holds a key that is not a string"
        )
    # The closing brace bounds the last entry; every other one ends where the
    # next begins. `end_lineno` on the value would stop before the `),`.
    bounds = [line for _, line in starts[1:]] + [(table.end_lineno or 0) - 1]
    return [
        (case, start, end) for (case, start), end in zip(starts, bounds, strict=True)
    ]


def clear_unreviewed(root: Path, case_id: str) -> bool:
    """Remove this case's entry from ``UNREVIEWED``. Returns whether it wrote.

    The list names the cases nobody has read, so it is only accurate while a
    case that gets read comes off it.
    """
    path = root / UNREVIEWED_FILE
    source = path.read_text(encoding="utf-8")
    span = next(
        (
            (start, end)
            for case, start, end in _unreviewed_entries(source)
            if case == case_id
        ),
        None,
    )
    if span is None:
        return False
    start, end = span
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")
    return True


def unreviewed_cases(root: Path) -> list[str]:
    """Every case the unreviewed list still names, in file order."""
    source = (root / UNREVIEWED_FILE).read_text(encoding="utf-8")
    return [case for case, _, _ in _unreviewed_entries(source)]
