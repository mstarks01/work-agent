"""Holding a **Case Sitting**: what it reads, and what it writes when it ends.

The act is #327's, and ``evals/BLESSING.md`` step 6 is the method. This module
is the part a front end does not get to reinvent: which files a sitting must
read, the digest of each as it stood, what a reader may say about one recorded
finding and the key that mark files under, the append-only entry that records
it and the one a surface may take back off, the line it clears in the
unreviewed list and puts back, whether a recorded sitting clears its case at
all, and the rail of every case with the status that rule gives it.
``webapp/sitting.py`` is one surface over this; the CLI path writes the same
files by hand and the checks cannot tell them apart, which is the point — one
implementation of the rules. CI reads :func:`clears` through
``tests/test_case_review.py``, so no surface can call a case read while CI
still asks somebody to read it.

**Recording a sitting is one act, and so is taking it back.** :func:`finish`
writes the three files and says on the draft what it wrote; :func:`withdraw`
takes off exactly that. They are a pair rather than two sequences a surface
assembles, because what makes them correct is that the tree comes back byte for
byte — a stray byte left under a case directory puts that case in the pull
request, and a field one of them sets that the other forgets is a case that
cannot be put back. The primitives they compose stay named, because a reader
following one of them needs to see what it does.

**The own list comes first, and that is a property rather than an
instruction.** What the order protects is the evidence in the filled
document: it prints the reader's own list above the recorded sets, and a
later reader takes that order on trust. So a caller here asks for part
one and part two separately, and :attr:`Prepared.part_two_blocks` is what a
surface must withhold until the reader has written their own list down. That
mirrors the review app's configuration-blindness, which is enforced by the queue item
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
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analysis_service.report import FrameworkName
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

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "evals" / "corpus"

#: Where the unreviewed list lives. A sitting that does not clear its line
#: leaves the count a lie, so ``submit sitting`` refuses one that has not.
UNREVIEWED_FILE = "tests/test_case_review.py"

#: What a reader may say about one recorded finding. The method's own closed
#: set, which ``evals.build_review_docs.MARK_GUIDANCE`` writes out for the
#: reader who fills the document by hand — free prose here would record less
#: than that path does, and no count could be taken over it.
Mark = Literal["agree", "reject", "duplicate"]

#: The same three, for a surface that offers them and a check that reads them.
MARKS: tuple[Mark, ...] = get_args(Mark)

#: What the rail says about a case no sitting clears. The other status a row
#: can carry names the signer, and it is spelled where it is computed.
TO_DO = "to do"

#: The five states a rail row can be in, which is what a surface colours a dot
#: by. The prose beside each one is :attr:`Row.status`, and it is a tooltip.
RowState = Literal["todo", "draft", "finished", "signed", "error"]


class SittingError(ValueError):
    """The sitting cannot be recorded; the message says what stops it."""


#: The two states a **Draft Sitting** file can name. It is ``open`` while the
#: reader is still reading, and ``finished`` once they record the sitting —
#: the record is written into the working tree by then, and the draft says
#: only that it has not merged yet.
DraftState = Literal["open", "finished"]

#: What the store reports for a draft it cannot read. It is not a state the
#: file can name: no file says this about itself, and a file that will not
#: read cannot say anything about itself at all.
UNREADABLE = "unreadable"

#: What a live draft puts on its rail row, keyed by the state in the file so
#: a state the shape gains arrives here rather than through an ``if``. A key
#: it does not hold raises, which is the whole reason it is a table: a state
#: nobody wrote a row for stops the rail rather than drawing a wrong one.
DRAFT_ROW: Mapping[str, tuple[str, RowState]] = {
    "open": ("draft in progress", "draft"),
    "finished": ("finished, not submitted", "finished"),
}


class DraftError(ValueError):
    """This draft will not read; the message names the file.

    Deliberately not a :class:`SittingError`. A sitting that cannot be
    recorded is about the corpus, and this is about one file in the reader's
    own store — the surface refuses that one case and walks every other.
    """


class Draft(BaseModel):
    """A **Draft Sitting**: one reader's part-finished read of one case.

    It lives outside the repository, one file per case, and it never merges.
    That is what keeps an unsigned own list out of a pull request.

    **It caches no case text.** Part one and part two are read from the case
    directory every time, because a cache that can disagree with its source
    is a defect that waits.

    ``opened_digests`` pins each required file to the bytes it held when the
    draft opened. That is a different question from the digest in the case
    metadata, which pins what a recorded sitting signed, and it is what lets
    a surface tell the reader the text moved under a read in progress.

    **A hand-edited draft costs nothing this project can price, and nothing
    notices it.** A reader who types their own list into this file by hand
    wrote a list, which is the whole of what the filled document claims. A
    timestamp pair is rejected for the same reason: the reader owns the file,
    so they can write the timestamps too. The gate's job is to make the
    ordinary path the correct path, not to police the person at the keyboard.
    """

    model_config = ConfigDict(extra="forbid")

    case: str
    #: The clone this draft was written from. It is in the file rather than in
    #: the path so a reader who moves their checkout keeps their drafts and
    #: gets a warning, rather than a silently empty store.
    clone: str
    state: DraftState = "open"
    own_list: list[str] = Field(default_factory=list)
    #: Keyed by the finding's fingerprint, exactly as a mark is keyed
    #: everywhere else, so an edit to a claim file moves no mark.
    marks: dict[str, Mark] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    notes: str = ""
    opened_digests: dict[str, str] = Field(default_factory=dict)
    #: The entry the record appended to ``case.json``, or ``None`` while this
    #: read is unrecorded. It is in the draft rather than in a surface's
    #: memory because a reader records a case one day and re-records or drops
    #: it the next. It is also the whole of what a surface may take back off:
    #: an entry it did not write is untouchable, which is what keeps
    #: ``reviews`` append-only.
    recorded: dict[str, Any] | None = None
    #: The ``UNREVIEWED`` entry the record removed, verbatim, or ``""`` where
    #: it removed none. The reason in that entry is prose a person wrote and
    #: nothing can recompute it, so a surface that puts the case back on the
    #: list puts back the lines it took out.
    unreviewed_entry: str = ""


@dataclass(frozen=True)
class DraftStatus:
    """What the store says about one case, for the rail that reads it."""

    #: A :data:`DraftState`, or :data:`UNREADABLE`.
    state: str
    #: The file itself, because the refusal an unreadable draft gets names it.
    path: Path


def draft_root() -> Path:
    """Where drafts live when a caller names nowhere else.

    A function rather than a constant, so importing this module resolves no
    home directory and a test can pass a temporary one instead.
    """
    return Path.home() / ".local" / "state" / "work-agent" / "sittings"


def draft_path(root: Path, login: str, case_id: str) -> Path:
    """The one file this reader's draft of this case lives in.

    The login is in the path because a sitting binds to the GitHub account
    that submits it, so two logins on one machine never collide. One file per
    case, which is the **Ledger**'s own answer to the same problem: two
    writers never touch one file.

    Both segments are checked here rather than trusted. The case id arrives in
    a request, and a value carrying a separator would write outside the store.
    """
    for name in (login, case_id):
        if not name or name.startswith(".") or name != Path(name).name:
            raise DraftError(f"{name!r} is not a name a draft can be filed under")
    return root / login / f"{case_id}.json"


def save_draft(root: Path, login: str, draft: Draft) -> Path:
    """Write the draft over whatever stood there, and return the file.

    Written beside the target and moved into place, because a half-written
    draft would refuse its own case and the file holds an hour of somebody's
    attention. The store is one reader's own, so it is created readable by
    them alone.
    """
    path = draft_path(root, login, draft.case)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    scratch = path.with_name(f"{path.name}.part")
    scratch.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    scratch.chmod(0o600)
    os.replace(scratch, path)
    return path


def load_draft(root: Path, login: str, case_id: str) -> Draft | None:
    """This reader's draft of this case, ``None`` where they hold none.

    **A draft that will not read raises rather than reporting nothing.** The
    two alternatives are rejected. To treat it as absent throws the reader's
    own list away and re-arms the gate, so they retype a list they already
    wrote and never learn the first one existed. To repair it writes a guess
    into the one file the reader owns.
    """
    path = draft_path(root, login, case_id)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DraftError(f"{path}: this draft will not open — {exc}") from exc
    try:
        draft = Draft.model_validate_json(text)
    except ValidationError as exc:
        raise DraftError(f"{path}: {_first_problem(exc)}") from exc
    if draft.case != case_id:
        raise DraftError(f"{path}: this draft names the case {draft.case!r}")
    return draft


def _first_problem(error: ValidationError) -> str:
    """One problem out of a validation report, for a reader to act on.

    The whole report names every field at once and reads as a stack trace.
    The reader's next step is to open the file this message already names, so
    one field and one reason is what carries them there.
    """
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "the file"
    return f"{where} — {first['msg']}"


def discard_draft(root: Path, login: str, case_id: str) -> bool:
    """Delete one draft, and say whether there was one.

    Unlinking is the whole of it. A draft never merged, so nothing else
    records it and the case goes back to the state it had before.
    """
    path = draft_path(root, login, case_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def draft_states(root: Path, login: str) -> dict[str, DraftStatus]:
    """Every draft this reader holds, keyed by case id.

    A file that will not read reports :data:`UNREADABLE` and stops nothing
    else, so one bad draft costs its own case and no other. Nothing here
    writes, so a survey of the store changes nothing on disk.
    """
    folder = root / login
    if not folder.is_dir():
        return {}
    held: dict[str, DraftStatus] = {}
    for path in sorted(folder.glob("*.json")):
        try:
            draft = load_draft(root, login, path.stem)
        except DraftError:
            held[path.stem] = DraftStatus(UNREADABLE, path)
            continue
        if draft is not None:
            held[path.stem] = DraftStatus(draft.state, path)
    return held


def claim_files(frameworks: Iterable[str]) -> list[str]:
    """One reference set per declared framework, named by the declaration.

    Derived rather than listed, so a **Framework Package** nobody wrote yet
    arrives the moment a case declares it. Both the reading a sitting must
    cover and the files a sitting submission may change read this.
    """
    return [f"{CLAIMS_DIR}/{name}.json" for name in frameworks]


def document_name(submitted_by: str) -> str:
    """The filled reading document one submission writes beside a case.

    Spelled once: the app writes this name, and ``submit sitting`` admits
    this name and no other under the case prefix. A document under another
    name is another submission's, and a submission may not change one (#388).

    **It carries the submitting login, never the reader's.** The allowlist
    derives this name from the authenticated account, so a name the diff
    supplies can never widen what a pull request may write — which stays true
    however the read was arranged.
    """
    return f"REVIEW-{submitted_by}.md"


def required_files(frameworks: Iterable[str]) -> list[str]:
    """What a complete sitting reads, derived from the case's own declaration.

    The shared artefacts plus one reference set per declared framework, so a
    case that gains a package requires its set read by construction and no
    table here needs editing. The caller passes the declared names, because
    the declaration reaches this module as raw JSON on one path and as a
    loaded :class:`~evals.harness.reference.CaseMetadata` on the other.
    """
    return ["source.md", "model.json", *claim_files(frameworks)]


def moved(case_dir: Path, digests: Mapping[str, str]) -> list[str]:
    """The named files whose bytes no longer match the digest beside them.

    One comparison, and two questions asked of it. A recorded sitting asks
    whether the bytes it signed still stand, through :func:`drifted`. A
    **Draft Sitting** asks whether the text moved under a read in progress,
    which is the warning the reader gets at open and at finish.

    A file that is gone counts as moved, so a deleted source fails closed.
    """
    stale = []
    for name, digest in digests.items():
        target = case_dir / name
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != digest
        ):
            stale.append(name)
    return stale


def drifted(case: GoldenCase, sitting: CaseSitting, corpus_dir: Path) -> list[str]:
    """The read files whose bytes no longer match the sitting's digests.

    The corpus directory is passed rather than defaulted, because a caller
    that means a temporary tree must not silently read the shipped one.
    """
    return moved(
        corpus_dir / case.meta.id,
        {record.file: record.sha256 for record in sitting.read},
    )


def clears(
    case: GoldenCase, sitting: CaseSitting, roster: Roster, corpus_dir: Path
) -> bool:
    """Whether this sitting takes its case off the unreviewed list.

    A sitting clears when a rostered account carries it, every required file
    was read, and the digests it recorded still match the tree (#327). This is
    a different question from whether the case carries an entry: a drifted
    digest leaves an entry that clears nothing, and the case is unread again.
    Every surface that greys a case asks this one, so no page can call a case
    read while CI still asks somebody to read it.

    **The rostered name is ``submitted_by`` and only that.** ``submitted_for``
    is provenance, so a read carried for an anonymous reader clears its case on
    the submitter's standing, and no clearing rule has to decide what an
    unnamed reader's judgement is worth.
    """
    required = set(required_files(declared.name for declared in case.meta.frameworks))
    covered = not required - {record.file for record in sitting.read}
    return (
        covered
        and sitting.submitted_by in roster
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
    #: The machine-readable half of :attr:`status`. The prose is the tooltip
    #: a reader hovers; this is what a surface draws the five states apart by,
    #: so no surface parses a sentence to colour a dot.
    state: RowState
    #: Whether the reader may open this case. A row that does not press is
    #: also off the offered list, so the refusal a signed case needs costs no
    #: code of its own.
    pressable: bool


def rail(
    corpus_dir: Path,
    roster: Roster,
    drafts: Mapping[str, DraftStatus] | None = None,
) -> tuple[Row, ...]:
    """Every case in the corpus, in corpus order, with the status a reader reads.

    **The status comes from :func:`clears`, never from the presence of an
    entry in ``reviews``.** Those are different questions: a drifted digest
    leaves an entry that clears nothing, CI puts that case back on the
    unreviewed list, and a rail keyed on the entry would grey a case CI asks
    somebody to read.

    A case a sitting clears is not pressable once no draft of it is left,
    whoever signed it. The status names the signer, which reads correctly
    either way.

    ``drafts`` is what the reader's own store says, from
    :func:`draft_states`. A caller that passes none gets the rail of a reader
    who holds no drafts, which is what the corpus alone can say.
    """
    held = drafts or {}
    return tuple(
        _row(case, _signature(case, roster, corpus_dir), held.get(case.meta.id))
        for case in load_corpus(corpus_dir)
    )


def _signature(case: GoldenCase, roster: Roster, corpus_dir: Path) -> str | None:
    """Who cleared this case, or ``None`` while nobody has.

    A phrase rather than a login, because a proxied read has two names and a
    row that printed one of them would say something untrue: *ada* alone hides
    the reader, and the reader alone hides who answers for it. Where the two
    match, the phrase is the one login.
    """
    return next(
        (
            _named(sitting)
            for sitting in case.meta.reviews
            if clears(case, sitting, roster, corpus_dir)
        ),
        None,
    )


def _named(sitting: CaseSitting) -> str:
    """One recorded sitting's two names, as a rail row prints them."""
    return naming(sitting.submitted_by, sitting.submitted_for)


def naming(submitted_by: str, submitted_for: str) -> str:
    """The two names as one phrase, spelled here so every surface reads alike.

    A read somebody carried for another person is one fact, and a page, a rail
    row and a pull-request body that each phrased it themselves would be three
    chances to print only half of it. Where the names match there is one name,
    because *ada for ada* says nothing twice.
    """
    if submitted_for == submitted_by:
        return submitted_by
    return f"{submitted_by} for {submitted_for}"


def _row(case: GoldenCase, signature: str | None, draft: DraftStatus | None) -> Row:
    status, state, pressable = _standing(signature, draft)
    return Row(
        case_id=case.meta.id,
        number=case.meta.id.split("-")[0],
        title=case.meta.title,
        status=status,
        state=state,
        pressable=pressable,
    )


def _standing(
    signature: str | None, draft: DraftStatus | None
) -> tuple[str, RowState, bool]:
    """What one case is waiting for, out of the two things that can be true of it.

    **A live draft outranks a signature.** The draft is what makes a case
    re-openable, and a case dies in the rail when it carries a clearing
    signature and no draft — whoever carries it, and whoever it was read for. So a reader who records a
    case still holds its draft, and their row reads *finished, not
    submitted* and presses, which is how they record it again.

    An unreadable draft answers before the state in it, because it is the one
    answer that is not about how far the read got. It refuses its own case
    and names the file, and every other case still walks.
    """
    if draft is None:
        if signature is not None:
            return f"signed by {signature}", "signed", False
        return TO_DO, "todo", True
    if draft.state == UNREADABLE:
        return f"draft unreadable: {draft.path}", "error", False
    status, state = DRAFT_ROW[draft.state]
    return status, state, True


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


def digests(case_dir: Path, files: list[str]) -> dict[str, str]:
    """The same reading, keyed by file name, for a draft to hold.

    A **Draft Sitting** pins what the reader opened; an entry in the case
    metadata pins what a recorded sitting signed. Both read the bytes the
    same way, so they agree about what a file said at the moment each was
    taken.
    """
    return {record.file: record.sha256 for record in read_records(case_dir, files)}


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
    #: The system as the reader meets it, as the blocks a surface lays out.
    part_one_blocks: list[dict]
    #: Framework -> that package's recorded set, as blocks. A surface must not
    #: send this until the reader's own list is in.
    part_two_blocks: dict[str, dict]
    files: list[str]
    #: One target per recorded finding, in the order the parts render. Part of
    #: part two rather than part one: a target names a claim, and a count of
    #: them is a count of the recorded set. No default, because a ``Prepared``
    #: built without them would refuse every mark a reader made.
    mark_targets: tuple[MarkTarget, ...]

    @property
    def part_one(self) -> str:
        """Part one as the reading document prints it.

        Derived rather than stored beside the blocks: the filled evidence
        document and the page must lay out one description of the case, and a
        second stored copy is where the two start to disagree.
        """
        return docs.part_one_markdown(self.part_one_blocks)

    @property
    def part_two(self) -> dict[str, str]:
        """Framework -> that set as the reading document prints it."""
        return {
            name: docs.part_markdown(rendered, part)
            for part, (name, rendered) in enumerate(
                self.part_two_blocks.items(), start=2
            )
        }


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
        part_one_blocks=docs.part_one_blocks(case_dir),
        part_two_blocks=docs.parts_after_blocks(case_dir),
        files=required_files(case.frameworks),
        mark_targets=mark_targets(case),
    )


def document(
    prepared: Prepared,
    own_list: list[str],
    marks: Mapping[str, Mark],
    missing: list[str],
    notes: str,
    submitted_by: str,
    submitted_for: str,
) -> str:
    """The filled reading document — the evidence that the method ran.

    Only the filled copy shows the own list was written before the recorded
    sets were opened, which is the one thing a generated ``REVIEW.md`` cannot
    show. ``submit sitting`` checks it exists; a reader checks it means
    something.

    **It says whose words these are**, because the file name carries the
    submitting login and a proxied read is not that account's own list. The
    line states both names, so a later reader of the evidence does not take
    the file name for the author.

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
        f"\nRead by {submitted_for}, submitted by {submitted_by}.\n",
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
    submitted_by: str,
    submitted_for: str,
    read: list[ReadRecord],
    document_name: str,
    notes: str,
    replaces: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append the sitting to ``case.json``'s ``reviews``, and return the entry.

    Append-only: a correction is a new entry, never an edit to one recorded
    (#327). The file is rewritten whole because it is small and JSON has no
    append, but nothing already in ``reviews`` is touched.

    ``replaces`` is an entry the caller appended itself and now re-records.
    It comes off before the new one goes on, so one submission and one case
    never write two entries. **Only an entry this account submitted comes
    off**, checked here rather than assumed of the caller: the entry reaches
    this function from a **Draft Sitting**, which is a file the reader owns, so
    a value in it is a value this module did not write. A sitting somebody else
    submitted is untouchable, which is what keeps the rule above true.

    ``submitted_for`` is written and never resolved: it names who read the
    case, and :func:`clears` reads ``submitted_by``.
    """
    path = case_dir / "case.json"
    raw = path.read_text(encoding="utf-8")
    meta = json.loads(raw)
    entry = {
        "submitted_by": submitted_by,
        "submitted_for": submitted_for,
        "date": datetime.now(UTC).date().isoformat(),
        "read": [{"file": item.file, "sha256": item.sha256} for item in read],
        "document": document_name,
        "notes": notes,
    }
    reviews = meta.setdefault("reviews", [])
    if replaces is not None:
        _yours(replaces, submitted_by)
        if replaces in reviews:
            reviews.remove(replaces)
    reviews.append(entry)
    _write_meta(path, meta, raw)
    return entry


def _yours(entry: Mapping[str, Any], submitted_by: str) -> None:
    """Refuse an entry another account submitted.

    Both the entry :func:`record` replaces and the entry :func:`unrecord`
    removes arrive from a caller that read them off a **Draft Sitting** — a
    file outside the repository that the reader owns. So "only your own
    append comes off" is checked where the removal happens, rather than
    assumed of the caller who supplied the value.

    **It asks ``submitted_by`` and never ``submitted_for``.** The submitting
    account is the one this process can prove, so reading the other field here
    would let a draft name a reader and take somebody else's entry off.
    """
    named = entry.get("submitted_by")
    if named != submitted_by:
        raise SittingError(
            f"that entry was submitted by {named!r} and you are"
            f" {submitted_by!r}; a sitting somebody else submitted is not"
            " yours to take off"
        )


def unrecord(case_dir: Path, submitted_by: str, entry: dict[str, Any]) -> bool:
    """Take one of your own entries back off ``reviews``, and say whether it
    was there.

    The caller passes the entry :func:`record` returned. **Only an entry this
    account submitted comes off**, for the reason :func:`_yours` gives — a
    sitting somebody else submitted is untouchable here, exactly as it is
    under ``replaces``.

    This is what a reader who holds a recorded case back from a submission
    needs, because the submission is built from the working tree: a case
    whose entry stays behind is a case the pull request carries.

    **An empty list comes off with the entry that made it.** A case nobody
    has sat carries no ``reviews`` key at all, and :func:`record` is what
    writes one, so leaving ``[]`` behind would keep the case in the diff with
    a key that says nothing — and the pull request would carry a case the
    reader dropped.
    """
    path = case_dir / "case.json"
    _yours(entry, submitted_by)
    raw = path.read_text(encoding="utf-8")
    meta = json.loads(raw)
    reviews = meta.get("reviews", [])
    if entry not in reviews:
        return False
    reviews.remove(entry)
    if not reviews:
        meta.pop("reviews", None)
    _write_meta(path, meta, raw)
    return True


def _write_meta(path: Path, meta: dict[str, Any], raw: str) -> None:
    """Write the case metadata back, in the escaping the file already uses.

    **An append reformats nothing else.** The corpus does not spell a
    non-ASCII character one way: some files carry the character and some
    carry the ``\\uXXXX`` escape, so a write that picked either would rewrite
    unrelated lines in about a third of the cases. Matching what the file
    already does means an append adds one entry to the diff and
    :func:`unrecord` takes it away again, which is what lets a surface put a
    case back the way it found it.
    """
    path.write_text(
        json.dumps(meta, ensure_ascii=raw.isascii(), indent=2) + "\n",
        encoding="utf-8",
    )


def _unreviewed_table(source: str) -> tuple[list[tuple[str, int, int]], int]:
    """Each ``UNREVIEWED`` entry as ``(case id, first line, last line)``, and
    the line the table closes on.

    Read through :mod:`ast` rather than by matching text, because the entry
    prose is arbitrary English: counting brackets to find where an entry ends
    works only while no reason writes one, and the reasons cite issues. The
    parser knows where every entry starts, so an entry runs to the line before
    the next one — which is what carries the trailing comma, the closing
    parenthesis and any comment with it, whatever shape they were written in.

    Line numbers are 0-based and the end is exclusive, ready to slice.

    A source that will not parse raises a :class:`SittingError` like every
    other answer this gives, because one caller is a submission check reading
    a file somebody edited — and a checklist line is what a contributor can
    act on, where a traceback is not.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SittingError(
            f"{UNREVIEWED_FILE}: this file will not parse — {exc}"
        ) from exc
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
    close = (table.end_lineno or 0) - 1
    # An empty table names no case and bounds nothing. It is the day the last
    # case is read, and the pairing below has no entry to close.
    if not starts:
        return [], close
    # The closing brace bounds the last entry; every other one ends where the
    # next begins. `end_lineno` on the value would stop before the `),`.
    bounds = [line for _, line in starts[1:]] + [close]
    return [
        (case, start, end) for (case, start), end in zip(starts, bounds, strict=True)
    ], close


def clear_unreviewed(root: Path, case_id: str) -> str:
    """Remove this case's entry from ``UNREVIEWED``, and return what it removed.

    The list names the cases nobody has read, so it is only accurate while a
    case that gets read comes off it.

    **It hands the removed lines back** because the reason in them is prose a
    person wrote, and nothing can recompute it. A reader who holds a recorded
    case back from a submission puts that case on the list again, and these
    are the lines that go there. The answer is ``""`` where the list did not
    name the case, so a caller that only asks whether it wrote reads it as a
    falsehood.
    """
    path = root / UNREVIEWED_FILE
    source = path.read_text(encoding="utf-8")
    entries, _ = _unreviewed_table(source)
    span = next(
        ((start, end) for case, start, end in entries if case == case_id),
        None,
    )
    if span is None:
        return ""
    start, end = span
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")
    return "".join(lines[start:end])


def restore_unreviewed(root: Path, case_id: str, entry: str) -> bool:
    """Put one case's ``UNREVIEWED`` entry back, and say whether it wrote.

    The reverse of :func:`clear_unreviewed`, and the reason that function
    returns its lines. A reader who holds a recorded case back from a
    submission leaves that case unread, so the list has to name it again —
    otherwise the count CI reads is a lie the pull request carries.

    The entry goes in front of the first key that sorts after it, which is
    where the table keeps it: the ids are numbered and the table is in corpus
    order. A case the list already names is left alone, so putting one back
    twice writes once.

    **The entry is checked before it is written.** It is text from a
    **Draft Sitting**, which is a file outside the repository, and it lands
    in a module ``pytest`` imports — so text that is anything other than one
    table entry for this case would be Python nobody wrote running in
    everybody's checkout. :func:`_one_entry` decides that, and a value that
    fails it changes nothing on disk.
    """
    _one_entry(case_id, entry)
    path = root / UNREVIEWED_FILE
    source = path.read_text(encoding="utf-8")
    entries, close = _unreviewed_table(source)
    if any(case == case_id for case, _, _ in entries):
        return False
    at = next((start for case, start, _ in entries if case > case_id), close)
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[:at] + [entry] + lines[at:]), encoding="utf-8")
    return True


def _one_entry(case_id: str, entry: str) -> None:
    """Refuse text that is not exactly one ``UNREVIEWED`` entry for this case.

    Read as a dict literal of its own rather than by matching text, because
    what has to be true is a shape and not a spelling: one string key equal
    to this case, one string value, and nothing else in the fragment. Text
    that closes the table and opens another parses as a module and never as
    one expression, which is the whole of what this refuses.
    """
    try:
        parsed = ast.parse(f"{{{entry}}}", mode="eval").body
    except SyntaxError as exc:
        raise SittingError(
            f"{case_id}: that UNREVIEWED entry is not one table entry — {exc}"
        ) from exc
    keys = getattr(parsed, "keys", [])
    values = getattr(parsed, "values", [])
    ok = (
        isinstance(parsed, ast.Dict)
        and len(keys) == 1
        and isinstance(keys[0], ast.Constant)
        and keys[0].value == case_id
        and isinstance(values[0], ast.Constant)
        and isinstance(values[0].value, str)
    )
    if not ok:
        raise SittingError(
            f"{case_id}: that UNREVIEWED entry names something other than this"
            " case, or carries something other than a reason"
        )


def without_unreviewed(source: str, cases: Iterable[str]) -> str:
    """The unreviewed list with these cases' entries taken out, as text.

    What is left is everything a sitting may not touch: the module's own
    prose and code, and every entry naming a case the submission does not
    carry. A caller compares this across a diff, so an edit anywhere but the
    carried cases' own lines shows up as a difference.
    """
    entries, _ = _unreviewed_table(source)
    drop = set(cases)
    cut = {
        number
        for case, start, end in entries
        if case in drop
        for number in range(start, end)
    }
    return "".join(
        line
        for number, line in enumerate(source.splitlines(keepends=True))
        if number not in cut
    )


def unreviewed_cases(root: Path) -> list[str]:
    """Every case the unreviewed list still names, in file order."""
    source = (root / UNREVIEWED_FILE).read_text(encoding="utf-8")
    entries, _ = _unreviewed_table(source)
    return [case for case, _, _ in entries]


@dataclass(frozen=True)
class Store:
    """Where one reader's sitting reads and writes.

    Four values, because a surface that holds three of them and derives the
    fourth has half of this. ``corpus_dir`` and :attr:`document_name` are
    derived here for the same reason the rest of the rules are: a surface that
    spelled the document name itself could spell one ``submit sitting`` does
    not admit.
    """

    #: The clone. The unreviewed list is read and written under it.
    root: Path
    #: The GitHub login this sitting binds to, and the one the pull request
    #: opens as. Every rule that grants anything reads this field.
    submitted_by: str
    #: Who read the case: the same login, or ``ANONYMOUS``. It reaches the
    #: record and stops there, so it never names a draft file, a document or
    #: an allowlist entry.
    submitted_for: str
    #: Where this reader's **Draft Sitting**s live, outside the repository.
    drafts: Path

    @property
    def corpus_dir(self) -> Path:
        return self.root / "evals" / "corpus"

    @property
    def document_name(self) -> str:
        return document_name(self.submitted_by)

    def case_dir(self, case_id: str) -> Path:
        return self.corpus_dir / case_id


def finish(
    store: Store,
    prepared: Prepared,
    draft: Draft,
    *,
    marks: Mapping[str, Mark],
    missing: Iterable[str],
    notes: str,
) -> Draft:
    """Record one sitting: three files written, and the draft that says so.

    The whole forward half of the act, in one place, because *Record the
    sitting* and *Put back* reach it by two routes — one carrying what the
    reader just typed, one carrying what their draft already holds — and the
    two must write the same record.

    **A second press corrects the record rather than adding to it.** The entry
    this reader appended comes off before the new one goes on, so a submission
    never carries two entries by one reader for one case. An entry they did
    not write is untouchable, which is what keeps ``reviews`` append-only.

    **A re-record clears no line.** The case came off the unreviewed list at
    the first press, so the lines the draft already holds stay.

    What the record left behind goes onto the draft, because :func:`withdraw`
    takes back exactly what this put on and the draft is the only thing here
    that outlives a process. The draft is saved before this returns, and it
    stays until a submission carries it: the record is in the working tree by
    now, and nothing is a record until it merges.
    """
    case_dir = store.case_dir(prepared.case_id)
    marks = dict(marks)
    missing = list(missing)
    text = document(
        prepared,
        draft.own_list,
        marks,
        missing,
        notes,
        store.submitted_by,
        store.submitted_for,
    )
    (case_dir / store.document_name).write_text(text, encoding="utf-8")
    entry = record(
        case_dir,
        store.submitted_by,
        store.submitted_for,
        read_records(case_dir, prepared.files),
        store.document_name,
        notes,
        replaces=draft.recorded,
    )
    cleared = clear_unreviewed(store.root, prepared.case_id)
    draft.marks = marks
    draft.missing = missing
    draft.notes = notes
    draft.recorded = entry
    draft.unreviewed_entry = cleared or draft.unreviewed_entry
    draft.state = "finished"
    save_draft(store.drafts, store.submitted_by, draft)
    return draft


def withdraw(store: Store, prepared: Prepared, draft: Draft) -> Draft:
    """Take one recorded sitting back out of the working tree.

    The exact inverse of :func:`finish`, and written beside it for that
    reason: what one puts on, the other takes off, and a field added to one
    that the other forgets is a case that cannot be put back.

    **The order is load-bearing.** The entry comes off first, because that is
    what a submission reads a case directory for, and the untracked document
    comes off last. A write that fails part way leaves the draft saying
    ``finished``, so a surface still lists the case and the submission
    checklist still refuses it — which is the safe direction.

    **The reader keeps every word they wrote.** Only the two fields the record
    set are cleared, so :func:`finish` writes the same record again from the
    same draft.
    """
    case_dir = store.case_dir(prepared.case_id)
    if draft.recorded is not None:
        unrecord(case_dir, store.submitted_by, draft.recorded)
    if draft.unreviewed_entry:
        restore_unreviewed(store.root, prepared.case_id, draft.unreviewed_entry)
    (case_dir / store.document_name).unlink(missing_ok=True)
    draft.recorded = None
    draft.unreviewed_entry = ""
    draft.state = "open"
    save_draft(store.drafts, store.submitted_by, draft)
    return draft
