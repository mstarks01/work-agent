"""The offline sitting envelope: one file out, one file back.

A **Case Sitting** needs no credentials and no network, but until now it needed
a clone and a command line. A reader whose own policy stops them taking part
under their name is often a reader who cannot install a toolchain either, and
the two together shut them out of a method that is otherwise free.

The whole read therefore moves into one file. ``webapp/offline_sitting.py``
writes a standalone HTML page carrying every case's sources, blessed **System
Model** and recorded sets, plus the digest of each file as it stood. The reader
opens it in whatever browser their machine has, walks as many cases as they
choose, and downloads one envelope. This module reads that envelope back.

The envelope is untrusted input, and this module treats it as such. It arrives
by email from outside the repository, and what it asks for is a write into the
corpus. So every field is bounded and typed here (A05). A case id resolves
against the corpus directory rather than reaching a path join (A01). Every
digest is recomputed from the operator's own tree rather than read out of the
envelope (A08). A mark that names no recorded finding refuses its case rather
than being dropped (A10). Nothing in the file decides anything: it supplies
answers, and the rules that judge them are the same ones the app runs.

It applies through :func:`~evals.harness.sitting.finish`, and never beside it.
That is what makes an imported sitting indistinguishable from one held in the
app: the same document, the same appended entry, the same cleared line, and the
same **Draft Sitting** left behind, so the operator can drop a case or put it
back before the press. A second write path here would be a second set of rules,
and the looser one would win.

The operator runs ``submit sitting`` afterwards. Nothing here opens a pull
request, and nothing here reaches the network.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.harness import sitting as sittings
from evals.harness.reference import (
    MAX_NAME,
    SUBMITTED_FOR_PATTERN,
    is_submitted_for,
)
from evals.harness.sitting import Draft, Mark, SittingError, Store

#: What this path calls itself in the evidence it writes. A read held on the
#: offline page and imported here is not a read held on the local app, and the
#: filled document is where somebody later asks which it was.
HELD = "the offline sitting page, imported with `run sitting-import`"

#: The envelope format, so a file written by an older page refuses loudly
#: rather than being read under rules it was not built for. There is one
#: version and no compatibility path: a stale envelope is re-read, not
#: migrated, because the read is an hour and the migration is a guess about
#: what somebody meant.
VERSION = 1

#: One written line, matching the cap the app puts on the same values. A cap
#: on a list bounds how many lines arrive and not how long one is, so both
#: bounds are spelled.
MAX_LINE = 500
MAX_LINES = 200

#: The notes field, which is the one place a reader writes prose rather than a
#: line. Every one of these values is written into a document that a pull
#: request then carries, so the ceiling is on what merges, not on what parses.
MAX_NOTES = 20_000

#: A digest as it must be spelled, so a value that is not one refuses before
#: it reaches a comparison.
_SHA256 = r"^[0-9a-f]{64}$"

Line = Annotated[str, Field(max_length=MAX_LINE)]

#: A generous ceiling on one envelope's cases and marks. The corpus is 13
#: cases with tens of records each; these bound a hostile file rather than a
#: real one.
MAX_CASES = 100
MAX_MARKS = 500

#: A file bigger than this is not a sitting. Read before parsing, because the
#: cheapest refusal is the one that never allocates the document.
MAX_BYTES = 4 * 1024 * 1024


class EnvelopeError(RuntimeError):
    """An envelope this operator's tree will not take, and why."""


class CaseAnswers(BaseModel):
    """What one reader decided about one case, as the page hands it over.

    It carries no case text. Everything the reader read is in the operator's
    own tree, so an envelope that described a case would be a second copy of
    it — and the digests below are exactly the check that the two agree.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    own_list: list[Line] = Field(min_length=1, max_length=MAX_LINES)
    #: Keyed by the finding's fingerprint, as a mark is keyed everywhere else,
    #: so an insertion into a claim file moves no mark.
    marks: dict[str, Mark] = Field(default_factory=dict, max_length=MAX_MARKS)
    missing: list[Line] = Field(default_factory=list, max_length=MAX_LINES)
    notes: str = Field(default="", max_length=MAX_NOTES)
    #: Each required file as it stood when the page was built. Compared
    #: against the tree at import, never trusted as the digest that merges.
    #: The value is shaped here so a file that carries something other than a
    #: digest refuses, rather than reading as a file that moved.
    opened_digests: dict[Line, Annotated[str, Field(pattern=_SHA256)]] = Field(
        default_factory=dict, max_length=MAX_LINES
    )


class Envelope(BaseModel):
    """One reader's session, however many days it took, as one file.

    ``submitted_by`` and ``submitted_for`` are stamped when the page is built,
    but they ride back inside a file the reader holds, so what arrives is a
    claim rather than a stamp. :func:`command_import` checks both against the
    account the operator names before anything is written, and ``submit
    sitting`` re-checks ``submitted_by`` against the authenticated login.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope: int
    submitted_by: str = Field(
        pattern=r"^[A-Za-z0-9](?:-?[A-Za-z0-9])*$", max_length=MAX_NAME
    )
    submitted_for: str = Field(pattern=SUBMITTED_FOR_PATTERN, max_length=MAX_NAME)
    #: The date the page was generated, which is what a stale envelope is
    #: dated by. It is not the sitting's date: the entry is dated when it is
    #: recorded, because that is when the digests are taken.
    generated: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cases: dict[str, CaseAnswers] = Field(max_length=MAX_CASES)


def read(path: Path) -> Envelope:
    """One envelope file, parsed and bounded, or a refusal naming the reason.

    Every failure here is a message to a person who is about to email a reader
    back, so each one says what is wrong with the file rather than what the
    parser thought of it.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EnvelopeError(f"{path}: cannot be read — {exc}") from exc
    if size > MAX_BYTES:
        raise EnvelopeError(
            f"{path} is {size} bytes and the ceiling is {MAX_BYTES}; a sitting"
            " envelope carries answers, never case text"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeError(f"{path}: not readable JSON — {exc}") from exc
    try:
        envelope = Envelope.model_validate(raw)
    except ValidationError as exc:
        raise EnvelopeError(f"{path}: not a sitting envelope — {exc}") from exc
    if envelope.envelope != VERSION:
        raise EnvelopeError(
            f"{path} is envelope version {envelope.envelope} and this tree"
            f" reads version {VERSION}; generate the page again and ask for a"
            " fresh read"
        )
    if not is_submitted_for(envelope.submitted_for):
        raise EnvelopeError(
            f"{envelope.submitted_for!r} is not a name a sitting can record"
        )
    return envelope


def _offered(corpus_dir: Path) -> set[str]:
    """Every case id the corpus holds.

    An envelope's keys are resolved against this rather than joined onto a
    path, so a key spelling a traversal names no case and refuses (A01).
    """
    return {case.meta.id for case in sittings.load_corpus(corpus_dir)}


def _refusals(
    case_id: str,
    answers: CaseAnswers,
    prepared: sittings.Prepared,
    case_dir: Path,
) -> list[str]:
    """Everything wrong with one case's answers, all of it at once.

    One case refusing must not hide the next one's problem: the reader is
    hours away by email, so a round trip has to carry every question at once.
    """
    problems = []
    if not sittings.own_list_is_written(answers.own_list):
        problems.append(
            f"{case_id}: the own list is shorter than {sittings.MIN_OWN_LIST}"
            " characters, so this case's sets should never have opened"
        )
    stale = sittings.moved(
        case_dir,
        {name: answers.opened_digests.get(name, "") for name in prepared.files},
    )
    if stale:
        problems.append(
            f"{case_id}: {', '.join(stale)} changed since the page was built,"
            " so this read answers words that are no longer there. Generate"
            " the page again and ask for this case to be re-read."
        )
    unknown = sorted(
        set(answers.marks) - {target.fingerprint for target in prepared.mark_targets}
    )
    if unknown:
        problems.append(
            f"{case_id}: {', '.join(unknown)} names no recorded finding of this"
            " case, so the mark answers nothing"
        )
    return problems


def apply(envelope: Envelope, root: Path, drafts: Path | None = None) -> list[str]:
    """Write every case in one envelope into the operator's tree.

    **Nothing is written until every case passes.** An envelope is one
    reader's session, and a half-applied one leaves the operator deciding
    which cases are real from a tree that no longer says. So the checks run
    over the whole file first, and the writes run only after.

    Returns the case ids written, in corpus order.
    """
    store = Store(
        root=root,
        submitted_by=envelope.submitted_by,
        submitted_for=envelope.submitted_for,
        drafts=drafts or sittings.draft_root(),
        held=HELD,
    )
    offered = _offered(store.corpus_dir)
    unknown = sorted(set(envelope.cases) - offered)
    if unknown:
        raise EnvelopeError(
            f"this envelope names cases the corpus does not hold: {unknown}"
        )

    ordered = [case_id for case_id in sorted(envelope.cases) if case_id in offered]
    prepared = {}
    problems: list[str] = []
    for case_id in ordered:
        case_dir = store.case_dir(case_id)
        try:
            prepared[case_id] = sittings.prepare(case_dir)
        except (SittingError, OSError) as exc:
            problems.append(f"{case_id}: will not prepare — {exc}")
            continue
        problems += _refusals(
            case_id, envelope.cases[case_id], prepared[case_id], case_dir
        )
    if problems:
        raise EnvelopeError("\n".join(problems))

    for case_id in ordered:
        answers = envelope.cases[case_id]
        draft = Draft(
            case=case_id,
            clone=str(root),
            own_list=[item.strip() for item in answers.own_list if item.strip()],
            opened_digests=dict(answers.opened_digests),
        )
        sittings.finish(
            store,
            prepared[case_id],
            draft,
            marks=answers.marks,
            missing=answers.missing,
            notes=answers.notes,
        )
    return ordered


def command_import(args: argparse.Namespace) -> int:
    """``run sitting-import <file> --submitted-by <login>``: one envelope in.

    The operator names the account, and the envelope has to agree with them.
    Both identity fields arrive inside a file a reader mailed back, so neither
    is a fact this command may take on the file's word: the reader is the one
    party here who is not the operator, and a sitting record says who read a
    case.

    Checked **before** :func:`apply`, because apply writes the corpus, the
    reading document, the unreviewed list and the draft store. A refusal that
    came after them would leave a tree only ``git checkout`` puts back.
    """
    root = Path(args.root).resolve() if args.root else sittings.REPO_ROOT
    submitted_for = args.submitted_for or args.submitted_by
    try:
        envelope = read(Path(args.envelope))
        if envelope.submitted_by != args.submitted_by:
            raise EnvelopeError(
                f"this envelope reads as {envelope.submitted_by!r} and you named"
                f" {args.submitted_by!r}. Import it under the account it was"
                " built for, or ask the reader for one built for this account."
            )
        if envelope.submitted_for != submitted_for:
            raise EnvelopeError(
                f"this envelope says it was read for {envelope.submitted_for!r}"
                f" and you named {submitted_for!r}. Pass --submitted-for to"
                " record a read somebody carried for another account."
            )
        written = apply(envelope, root)
    except EnvelopeError as exc:
        print(f"this envelope was not applied:\n{exc}")
        return 1

    names = sittings.naming(envelope.submitted_by, envelope.submitted_for)
    print(f"read by {names}, generated {envelope.generated}\n")
    for case_id in written:
        print(f"  {case_id}")
    print(
        f"\n{len(written)} case(s) recorded in this tree."
        "\nRun `python -m evals.harness.run submit sitting` to open the PR."
    )
    return 0


def import_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("envelope", help="the JSON file the reader sent back")
    parser.add_argument(
        "--submitted-by",
        required=True,
        help="the account this sitting binds to. The envelope must agree, and"
        " nothing is written if it does not.",
    )
    parser.add_argument(
        "--submitted-for",
        help="who the case was read for, where somebody carried the read for"
        " another account. Defaults to --submitted-by.",
    )
    parser.add_argument(
        "--root",
        help="the clone to write into. Defaults to this one; a test points it"
        " at a temporary tree.",
    )
