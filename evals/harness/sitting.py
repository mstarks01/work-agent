"""Holding a **Case Sitting**: what it reads, and what a reader may say about it.

The act is #327's, and ``evals/BLESSING.md`` step 6 is the method. This module
is the part a front end does not get to reinvent. It holds which files a sitting
must read, the digest of each as it stood, what a reader may say about one
recorded finding and the key that mark files under, the draft that carries their
answers, and the rail of every case with the status it has.

**A sitting becomes a record by merging, and not before.**
:mod:`evals.review_submission` is where one leaves this repository: one JSON
file under ``evals/review/submissions``, carrying the reader's own list, their
marks, their missing list, their notes and a digest of every file they read.
Nothing here writes into a case directory or into the unreviewed list, so a
reader who stops has nothing to clean up and a surface has nothing to undo.

Which cases are read has **one reader**, and it is not here:
:func:`evals.review_submission.current_reviews` answers it from the merged
submissions, and the rail, the CI gate in ``tests/test_case_review.py`` and the
printed count all ask that one function. So no surface can call a case read
while CI still asks somebody to read it.

Recording a sitting is one act, and so is taking it back. :func:`finish` writes
the reader's answers onto their draft and marks it finished. :func:`withdraw`
puts it back to open, keeping every word they wrote. They are a pair rather than
two sequences a surface assembles.

The own list comes first, and that is a property rather than an instruction.
What the order protects is the evidence in the filled document: it prints the
reader's own list above the recorded sets, and a later reader takes that order
on trust. A caller here therefore asks for part one and part two separately, and
:attr:`Prepared.part_two_blocks` is what a surface must withhold until the
reader has written their own list down. That mirrors the review app's
configuration-blindness, which the queue item enforces by having no field for
it, rather than by asking the reviewer not to peek.

A mark is keyed by the finding, never by its position. The reader answers one
recorded finding with one of :data:`MARKS`, and
:func:`~evals.harness.fingerprint.key_claim` computes the key. An insertion into
a claim file moves every position below it and moves no fingerprint, so a mark
recorded today still names the same finding after somebody edits the set. It is
the same key a vote is filed under, so improving the identity rule re-keys both
by recomputation.

Nothing here talks to a network or a provider. A sitting is reading, and the
whole path is free.
"""

from __future__ import annotations

import ast
import hashlib
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analysis_service.markdown_loader import RESOLVE_ERRORS
from analysis_service.report import FrameworkName
from evals import build_review_docs as docs
from evals.harness.fingerprint import FingerprintError, key_claim
from evals.harness.identity import FlowMap
from evals.harness.reference import (
    CLAIMS_DIR,
    GoldenCase,
    ReadRecord,
    ReferenceClaim,
    load_case,
    load_corpus,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "evals" / "corpus"

#: Where the unreviewed list lives. A sitting that does not clear its line
#: leaves the count a lie, so ``submit sitting`` refuses one that has not.
UNREVIEWED_FILE = "tests/test_case_review.py"

#: The shortest own list a sitting accepts, counted over the reader's own
#: words with the blank lines and the padding taken out. A press with nothing
#: typed opened the recorded sets, which made the gate a click rather than a
#: read.
#:
#: It lives here rather than on one surface because every surface has to hold
#: it: the app's endpoint, and the standalone file a reader opens offline. A
#: second spelling would be a second gate, and the looser one would win.
MIN_OWN_LIST = 10


def own_list_is_written(items: Iterable[str]) -> bool:
    """Whether one own list says enough to open that case's recorded sets.

    Counted over the stripped words, so a box full of blank lines does not
    pass. The caller keeps the stripped list; this only rules on it.
    """
    return sum(len(item.strip()) for item in items) >= MIN_OWN_LIST


#: What a reader may say about one recorded finding. The method's own closed
#: set, which ``evals.build_review_docs.MARK_GUIDANCE`` writes out for the
#: reader who fills the document by hand — free prose here would record less
#: than that path does, and no count could be taken over it.
#:
#: ``unsure`` is a real answer and is counted as one, exactly as it is for a
#: **Ledger** vote: review sitting 01 answered ``unclear`` on 4 of 30 pairs, and
#: that finding is what fixed the specificity rule in ``BLESSING.md``. The two
#: are keyed by one fingerprint and the word means one thing on both — the
#: reader read it and cannot decide.
#:
#: It is last because it is the answer a reader reaches for when the other
#: three do not fit, and a control lists it where they look for it.
Mark = Literal["agree", "reject", "duplicate", "unsure"]

#: The same set, for a surface that offers them and a check that reads them.
MARKS: tuple[Mark, ...] = get_args(Mark)

#: What the rail says about a case no submission clears. The other status a row
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


#: What every sitting reads whatever the case declares: the words the
#: submitter wrote, and the **System Model** built from them. A submission that
#: carries no digest for these read no case at all.
SHARED_FILES = ("source.md", "model.json")


def claim_file(framework: str) -> str:
    """The one reference set a **Framework** records under."""
    return f"{CLAIMS_DIR}/{framework}.json"


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

    One comparison, and two questions asked of it. A merged submission asks
    whether the bytes it signed still stand, through
    :func:`evals.review_submission.current_reviews`. A **Draft Sitting** asks
    whether the text moved under a read in progress, which is the warning the
    reader gets at open and at finish.

    A file that is gone counts as moved, so a deleted source fails closed.
    """
    stale = []
    root = case_dir.resolve()
    for name, digest in digests.items():
        target = case_dir / name
        # Resolve before reading, and treat anything outside the case as gone.
        # `CORPUS_RELATIVE_PATH` bounds the NAME -- it refuses `..`, a leading
        # slash and a backslash -- and a symlink needs none of those. A
        # committed `source.md` pointing at `/etc/hostname` matches the pattern
        # and reopens the whole finding: the digest comparison below answers
        # whether the attacker guessed a file's bytes, the read is unbounded,
        # and a file the process may not open raises out of the lint that
        # `contribution.yml` runs over a stranger's pull request tree.
        #
        # The rule is `markdown_loader`'s, which resolves and asks
        # `is_relative_to` before it reads. A name that leaves the directory is
        # treated the same as absent: deny, and reveal nothing about outside.
        try:
            resolved = target.resolve()
            outside = not resolved.is_relative_to(root)
            readable = not outside and resolved.is_file()
            digests_match = (
                readable and hashlib.sha256(resolved.read_bytes()).hexdigest() == digest
            )
        except RESOLVE_ERRORS:
            # Unreadable is not stale-or-not; it is a file this process cannot
            # answer for, and it must not escape as a traceback.
            digests_match = False
        if not digests_match:
            stale.append(name)
    return stale


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
    signatures: Mapping[str, str] | None = None,
    drafts: Mapping[str, DraftStatus] | None = None,
    partial: Mapping[str, str] | None = None,
) -> tuple[Row, ...]:
    """Every case in the corpus, in corpus order, with the status a reader reads.

    ``signatures`` says who cleared each case, from
    :func:`evals.review_submission.clearing_signatures` — the one reader of
    that question. It is passed in rather than read here because a submission
    is a merged file rather than a fact about a case directory, and this
    module is what a submission is validated against.

    A case a submission clears is not pressable once no draft of it is left,
    whoever signed it. The status names the signer, which reads correctly
    either way.

    ``partial`` says what a case still waits for where some sitting covers it
    in part, from :func:`evals.review_submission.partial_signatures`. Such a
    case presses like any unread one — work remains — and its status names the
    **Framework** that waits rather than reading ``to do``, which would tell a
    reader their own finished work was never done.

    ``drafts`` is what the reader's own store says, from
    :func:`draft_states`. A caller that passes none gets the rail of a reader
    who holds no drafts, which is what the corpus alone can say.
    """
    signed = signatures or {}
    part = partial or {}
    held = drafts or {}
    return tuple(
        _row(
            case,
            signed.get(case.meta.id),
            held.get(case.meta.id),
            part.get(case.meta.id),
        )
        for case in load_corpus(corpus_dir)
    )


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


def _row(
    case: GoldenCase,
    signature: str | None,
    draft: DraftStatus | None,
    partial: str | None = None,
) -> Row:
    status, state, pressable = _standing(signature, draft, partial)
    return Row(
        case_id=case.meta.id,
        number=case.meta.id.split("-")[0],
        title=case.meta.title,
        status=status,
        state=state,
        pressable=pressable,
    )


def _standing(
    signature: str | None, draft: DraftStatus | None, partial: str | None = None
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
        if partial is not None:
            return partial, "todo", True
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


def check_marks(prepared: Prepared, marks: Mapping[str, Mark]) -> None:
    """Refuse a mark key naming no recorded finding of this case.

    **One reader, three callers.** :func:`finish` asks it when a reader
    presses, :func:`document` asks it before it renders one, and
    :func:`evals.review_submission.validate` asks it of a merged file. A key
    the case does not carry is either a page that lost its targets or a request
    that never read one, and either way the mark answers nothing.
    """
    unknown = sorted(
        set(marks) - {target.fingerprint for target in prepared.mark_targets}
    )
    if unknown:
        raise SittingError(
            f"{prepared.case_id}: {', '.join(unknown)} names no recorded"
            " finding of this case, so the mark answers nothing"
        )


def targets_of(prepared: Prepared, framework: str) -> tuple[MarkTarget, ...]:
    """The recorded findings one **Framework** contributed to this case."""
    return tuple(
        target for target in prepared.mark_targets if target.framework == framework
    )


def check_every_finding_marked(
    prepared: Prepared, marks: Mapping[str, Mark], frameworks: Iterable[str]
) -> None:
    """Refuse a sitting that judges none of the findings it read.

    **The bar is a judgement, not an open file.** A submission used to clear a
    case on the digests alone, so a reader could open every set, write ten
    characters of own list, and record nothing about any **Claim** — and CI
    counted that case as read. The one defect a sitting exists to catch is a
    claim asserting a fact its own **System Model** does not hold, and a reader
    records that as a ``reject``. A case with no marks holds no such answer.

    One mark per recorded finding, which is what the by-hand path already asks:
    the reading document carries a ``> mark:`` slot per claim, and a browser
    sitting that filled fewer recorded less than the shell beside it.

    **Every** finding rather than some of them, because a fraction is a number
    nobody can defend. The cost is small where the cost actually falls: the
    hour goes on reading the sets, and the mark is a press the reader makes
    while they read.

    ``frameworks`` names the sets this sitting read, and the rule holds inside
    each one. A **Framework** that arrives after a reader finished is not their
    unfinished work, so a message that counted across both would name a debt
    the reader does not owe.
    """
    for framework in frameworks:
        targets = targets_of(prepared, framework)
        unmarked = [target for target in targets if target.fingerprint not in marks]
        if unmarked:
            raise SittingError(
                f"{prepared.case_id}: {len(unmarked)} of {len(targets)}"
                f" recorded {framework} findings carry no mark. A sitting"
                " answers every finding it read, because a set nobody judged"
                " is a set nobody tested."
            )


def document(
    prepared: Prepared,
    own_list: list[str],
    marks: Mapping[str, Mark],
    missing: list[str],
    notes: str,
    submitted_by: str,
    submitted_for: str,
    held: str,
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

    ``held`` names the surface the read happened on. It is a value the caller
    supplies rather than a sentence written here, because there is more than
    one surface now and a hardcoded one would be false on all but the first.

    ``marks`` is keyed by fingerprint, and a key naming no recorded finding of
    this case refuses the whole document. It is either a page that lost its
    targets or a request that never read one, and writing it would put a mark
    in the evidence that answers nothing.
    """
    check_marks(prepared, marks)
    lines = [
        f"# Case Sitting — `{prepared.case_id}`\n",
        f"\n**{prepared.title}**\n",
        f"\nRead by {submitted_for}, submitted by {submitted_by}.\n",
        (
            f"\nHeld through {held}. The own list below was written before the"
            " recorded sets were shown.\n"
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
    tables = [
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
    ]
    if not tables:
        raise SittingError(f"{UNREVIEWED_FILE}: no UNREVIEWED table to read")
    # Exactly one, rather than the first of several. Reading the first is a
    # disagreement with Python, which binds the last -- so a decoy empty table
    # above the real one makes this answer "no cases listed" about a file that
    # lists them, and a checker built on that answer is checking a table nobody
    # imports. There is one list; a file with two is malformed, and saying so is
    # cheaper than picking a winner and being right by convention.
    if len(tables) > 1:
        raise SittingError(
            f"{UNREVIEWED_FILE}: {len(tables)} UNREVIEWED tables; there is one"
            " list, and a reader cannot tell which of two a checker meant"
        )
    table = tables[0]

    starts = [
        (key.value, key.lineno - 1)
        for key in table.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    if len(starts) != len(table.keys):
        raise SittingError(
            f"{UNREVIEWED_FILE}: UNREVIEWED holds a key that is not a string"
        )
    # A dict value is an arbitrary expression, and this table is a module
    # `pytest` imports: importing it evaluates every value. A reason is prose,
    # so anything but a string literal here is something other than a reason,
    # and a submission may not carry it. Refused at the one place that reads the
    # table, because a check that read it some other way is a second opinion
    # about what an entry is -- and a disagreement between two readers of this
    # file is exactly what lets a value through.
    if any(
        not (isinstance(value, ast.Constant) and isinstance(value.value, str))
        for value in table.values
    ):
        raise SittingError(
            f"{UNREVIEWED_FILE}: UNREVIEWED holds a value that is not a string"
            " literal, and importing this module would evaluate it"
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


def unreviewed_cases(root: Path) -> list[str]:
    """Every case the unreviewed list still names, in file order."""
    source = (root / UNREVIEWED_FILE).read_text(encoding="utf-8")
    entries, _ = _unreviewed_table(source)
    return [case for case, _, _ in entries]


def covered_frameworks(case_dir: Path, opened_digests: Mapping[str, str]) -> list[str]:
    """Which **Framework**s a sitting read, in the case's declared order.

    A sitting covers a framework when it carries a digest for that framework's
    reference set and the digest still matches the tree.

    **A framework the case gained later is not covered, and is not a fault.**
    Nobody judges a set that did not exist when they read, so a sitting that
    covers some of a case's frameworks is sound and leaves the rest waiting.
    """
    prepared = prepare(case_dir)
    return [
        framework
        for framework in prepared.part_two_blocks
        if claim_file(framework) in opened_digests
        and not moved(
            case_dir, {claim_file(framework): opened_digests[claim_file(framework)]}
        )
    ]


def sitting_problems(
    case_dir: Path,
    *,
    own_list: Sequence[str],
    opened_digests: Mapping[str, str],
    marks: Mapping[str, Mark],
) -> list[str]:
    """Everything wrong with what one sitting claims to have read.

    **One reader, three callers.** The app's press, the CI check over a merged
    submission and the offline import all ask this, so a rule that refused an
    offline reader's case cannot accept the same case from the app.

    **It judges the sets the reader opened, never the ones they did not.** A
    case that gains a **Framework Package** after a sitting merged leaves that
    framework waiting; it does not make the merged sitting wrong, and it does
    not put the reader's own work back on their desk.

    Every problem at once, because a reader may be hours away by email and a
    round trip has to carry every question.
    """
    prepared = prepare(case_dir)
    case_id = prepared.case_id
    problems: list[str] = []

    if not own_list_is_written(own_list):
        problems.append(
            f"{case_id}: the independent list is shorter than"
            f" {MIN_OWN_LIST} characters, so this case's sets should never"
            " have opened"
        )

    recorded = set(opened_digests)
    if absent := sorted(set(SHARED_FILES) - recorded):
        problems.append(f"{case_id}: the review carries no digest for {absent}")
    if extra := sorted(recorded - set(prepared.files)):
        problems.append(f"{case_id}: the review carries unexpected digests for {extra}")

    # Over the files the reader says they read, and no others. A file the case
    # gained since is not one that moved under them, and a message that said so
    # would send them looking for an edit nobody made.
    if stale := moved(case_dir, dict(opened_digests)):
        problems.append(
            f"{case_id}: {', '.join(stale)} changed since the reviewer opened the case"
        )

    # Computed from the digests whatever else is wrong. Gated on `problems`
    # it would read as zero, and every mark the reader made would then be
    # reported as answering a set they never opened — a wall of text hiding the
    # one line that says what is actually wrong.
    read = covered_frameworks(case_dir, opened_digests)
    if not problems and not read:
        problems.append(
            f"{case_id}: the review opened no reference set, so it judges nothing"
        )
    for rule in (check_marks, partial(check_every_finding_marked, frameworks=read)):
        try:
            rule(prepared, marks)
        except SittingError as exc:
            problems.append(str(exc))

    # Named by framework rather than by fingerprint: a reader who marked a set
    # they did not record reading needs the set's name, and a list of every key
    # would bury it.
    unread = sorted(
        framework
        for framework in prepared.part_two_blocks
        if framework not in read
        and any(
            target.fingerprint in marks for target in targets_of(prepared, framework)
        )
    )
    if unread:
        problems.append(
            f"{case_id}: this review marks {', '.join(unread)} findings and"
            f" carries no digest for {[claim_file(name) for name in unread]},"
            " so it judges a set it does not say it read"
        )
    return problems


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
    #: The surface this sitting is held on, named in the filled document. No
    #: default: a second surface arrived, and a default would have let it
    #: write the first one's name into its own evidence.
    held: str

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
    """Record one sitting onto its draft, which is the whole forward half.

    *Record the sitting* and *Put back* reach this by two routes — one carrying
    what the reader just typed, one carrying what their draft already holds —
    and the two must write the same record, so the write is here rather than at
    each route.

    **Nothing is written into the working tree.** A sitting becomes a record by
    merging as one submission under ``evals/review/submissions``, and until then
    it lives in the reader's draft, which sits outside the repository. So a
    second press corrects the draft rather than appending to a file, and a
    reader who stops has nothing to clean up.

    Two rules refuse the press, and a merged submission is refused by the
    same two readers: :func:`check_marks` on a key naming no finding of this
    case, and :func:`check_every_finding_marked` on a finding this reader left
    unanswered.

    The draft is saved before this returns, because it is the only thing here
    that outlives a process.
    """
    check_marks(prepared, marks)
    check_every_finding_marked(prepared, marks, prepared.part_two_blocks)
    draft.marks = dict(marks)
    draft.missing = list(missing)
    draft.notes = notes
    draft.state = "finished"
    save_draft(store.drafts, store.submitted_by, draft)
    return draft


def withdraw(store: Store, prepared: Prepared, draft: Draft) -> Draft:
    """Take one recorded sitting back off its draft.

    The exact inverse of :func:`finish`, and written beside it for that reason:
    what one puts on, the other takes off.

    **The reader keeps every word they wrote.** Only the state changes, so
    :func:`finish` writes the same record again from the same draft.
    """
    draft.state = "open"
    save_draft(store.drafts, store.submitted_by, draft)
    return draft
