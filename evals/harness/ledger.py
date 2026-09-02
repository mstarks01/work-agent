"""The vote ledger: what a person decided about a finding, kept forever.

This is the only place in the repository where a human judgement is the datum.
Everything else under ``evals/`` is agent-authored, and ``evals/README.md`` says
so once for the whole directory. This is the directory that changes that, one
line at a time.

There is one file per voter, at ``evals/review/votes/<login>.jsonl``. The
filename is the GitHub login of the account that submits the votes, and the
loader refuses a row whose ``voter`` differs from its filename, so one voter's
history lives in one file. Two voters' pull requests then merge without
conflict, and two pull requests from one voter may still conflict, by design:
one person sequences their own. Order between voters carries no meaning.
:meth:`Ledger.current` keys by ``(fingerprint, voter)``, so position is
chronology only inside one file, which is exactly where the layout keeps it.

The ledger is append-only, and a correction is a new event. No row is ever
updated or removed. The current verdict for a ``(fingerprint, voter)`` pair is
its latest event by ``recorded``, which makes the ledger's history
reconstructible at any past date: a metric computed last month recomputes to the
same number today, by ignoring the events after it. A mutable store cannot offer
that, and the whole defensibility argument rests on it.

A vote stores its components rather than only its key. A fingerprint improves,
and improving it changes every key; see :mod:`evals.harness.fingerprint`.
Storing what the key was computed from makes a version bump a pure recompute
over these files rather than a re-vote, which is what stops the ledger expiring
the way a model-scored history does.

A vote carries one reason code, from a closed set, and it decides where the vote
lands. A reviewer who dislikes a finding's writing and a reviewer who says it is
not a threat are reporting two different facts, and averaging them would let
taste move a recall number. :data:`SUBSTANCE_REASONS` counts against the
analysis. :data:`STYLE_REASONS` never does, and lands in
:mod:`evals.harness.writing` instead. That split is the whole control for
personal preference, and it is mechanical rather than a request in a guide.

On security: these files are the supply chain of every quality number the tool
publishes. ``voter`` is therefore required, is never anonymous, and is recorded
on every event (A09). It must also hold the GitHub login shape, because the
login names this voter's file, and an unvalidated voter would be a path (A01).
Nothing here is deleted, so a bad actor's votes are identifiable and reversible
by appending rather than by rewriting (A08).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evals.harness.fingerprint import (
    Components,
    FingerprintError,
    fingerprint,
    version_for,
    version_of,
)

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = EVALS_ROOT / "review" / "votes"

#: The shape of a GitHub login: alphanumeric with single inner hyphens, at
#: most 39 characters. #320 makes the login the voter's one name, and the
#: login names the voter's file under :data:`DEFAULT_LEDGER_PATH` — so this
#: check is also what keeps a ``voter`` from carrying a path (A01: the value
#: reaches a filesystem join in :func:`append` and :func:`write_all`).
GITHUB_LOGIN = re.compile(r"(?=.{1,39}\Z)[A-Za-z0-9](?:-?[A-Za-z0-9])*\Z")

#: What a reviewer can answer. ``unsure`` is a real answer and is counted as
#: one: review sitting 01 answered ``unclear`` on 4 of 30 pairs, and that
#: finding is what fixed the specificity rule in ``BLESSING.md``. A two-valued
#: button would have thrown it away.
#:
#: ``needs-evidence`` is not a verdict about the finding at all — it says the
#: reviewer cannot answer from what they were shown. It routes to a re-ask
#: rather than to a score.
Verdict = Literal["up", "down", "unsure", "needs-evidence"]

#: Reasons that speak to whether the finding is *right*. A ``down`` carrying one
#: of these counts against the configuration that produced it.
SUBSTANCE_REASONS: frozenset[str] = frozenset(
    {
        "not-a-threat",
        "unsupported-by-the-model",
        "duplicate",
        "wrong-lane",
        "out-of-scope",
    }
)

#: Reasons that speak to how the finding is *written*. A ``down`` carrying one
#: of these leaves every analysis metric untouched and is counted by
#: :mod:`evals.harness.writing` instead. This is what stops a reviewer's taste
#: moving recall.
STYLE_REASONS: frozenset[str] = frozenset(
    {
        "too-vague",
        "poorly-written",
        "wrong-severity",
        "unhelpful-mitigation",
    }
)

REASONS: frozenset[str] = SUBSTANCE_REASONS | STYLE_REASONS

#: One-line gloss per reason, shown as the button label. Required for every
#: reason — ``test_evals_ledger.py`` checks the two sets match, because a reason
#: with no gloss is a reason two reviewers will read two ways.
REASON_GLOSS: dict[str, str] = {
    "not-a-threat": "this is not a threat to this system",
    "unsupported-by-the-model": "it asserts something the description does not say",
    "duplicate": "another finding in this report already says it",
    "wrong-lane": "real, but filed under the wrong category",
    "out-of-scope": "real, but outside what this system is responsible for",
    "too-vague": "real, but too unspecific to act on",
    "poorly-written": "real, but the wording is wrong or confusing",
    "wrong-severity": "real, but rated too high or too low",
    "unhelpful-mitigation": "real, but the suggested fix does not help",
}


class LedgerError(ValueError):
    """A vote is malformed, or the ledger cannot be read."""


@dataclass(frozen=True)
class Vote:
    """One reviewer's answer on one finding, at one moment.

    ``case`` and ``claim_text`` are carried for a reader, never for a
    comparison: a person auditing this file needs to see what was voted on
    without resolving a hash against an artifact that may no longer exist.

    ``config`` records which configuration produced the finding the reviewer
    saw. It is written **after** the vote and is never shown to the reviewer —
    that is what makes the vote blind, and it is why the field lives here rather
    than being asked for.
    """

    fingerprint: str
    components: Components
    case: str
    verdict: Verdict
    voter: str
    recorded: str
    reason: str | None = None
    claim_text: str = ""
    config: str = ""
    note: str = ""
    sitting: str = ""

    def __post_init__(self) -> None:
        """Refuse a vote no metric could read, at the moment it is built.

        On the type rather than in a helper a caller has to remember: a ledger
        is worth what its worst row is, and a row nobody can interpret is worse
        than a missing one because it still counts in a denominator.
        """
        if self.verdict not in ("up", "down", "unsure", "needs-evidence"):
            raise LedgerError(f"{self.verdict!r} is not a verdict")
        if not self.voter.strip():
            raise LedgerError("a vote carries no voter; this ledger is never anonymous")
        if not GITHUB_LOGIN.match(self.voter):
            raise LedgerError(
                f"{self.voter!r} is not a GitHub login; the voter is the"
                " account that opens the PR (#320), and the login names this"
                " voter's file in the ledger directory"
            )
        if self.verdict == "down" and self.reason is None:
            raise LedgerError(
                "a down-vote needs a reason, because the reason is what decides"
                " whether it moves an analysis number or a writing one"
            )
        if self.reason is not None and self.reason not in REASONS:
            raise LedgerError(
                f"{self.reason!r} is not a reason code; the set is"
                f" {', '.join(sorted(REASONS))}"
            )

    @property
    def counts_against_analysis(self) -> bool:
        """Does this vote move a recall or precision number?

        Only a ``down`` carrying a substance reason does. A style ``down``
        leaves the finding in the reference pool and moves the writing
        instrument's numbers instead; ``unsure`` and ``needs-evidence`` move
        nothing at all.
        """
        return self.verdict == "down" and self.reason in SUBSTANCE_REASONS

    @property
    def joins_the_pool(self) -> bool:
        """Does this vote put the finding into the reference pool?

        An ``up`` does. So does a ``down`` for a style reason: the reviewer
        confirmed the finding is real and objected to the writing, and dropping
        it from the pool would score a later run as wrong for finding it.
        """
        if self.verdict == "up":
            return True
        return self.verdict == "down" and self.reason in STYLE_REASONS

    def to_json(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "components": self.components.to_json(),
            "case": self.case,
            "verdict": self.verdict,
            "voter": self.voter,
            "recorded": self.recorded,
            "reason": self.reason,
            "claim_text": self.claim_text,
            "config": self.config,
            "note": self.note,
            "sitting": self.sitting,
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Vote:
        try:
            vote = cls(
                fingerprint=raw["fingerprint"],
                components=Components.from_json(raw["components"]),
                case=raw["case"],
                verdict=raw["verdict"],
                voter=raw["voter"],
                recorded=raw["recorded"],
                reason=raw.get("reason"),
                claim_text=raw.get("claim_text", ""),
                config=raw.get("config", ""),
                note=raw.get("note", ""),
                sitting=raw.get("sitting", ""),
            )
        except (KeyError, TypeError) as exc:
            raise LedgerError(f"malformed vote: {exc}") from exc
        except FingerprintError as exc:
            raise LedgerError(f"malformed vote components: {exc}") from exc
        return vote


def cast(
    components: Components,
    case: str,
    verdict: Verdict,
    voter: str,
    reason: str | None = None,
    claim_text: str = "",
    config: str = "",
    note: str = "",
    sitting: str = "",
    version: int | None = None,
) -> Vote:
    """Build one validated vote, stamping its fingerprint and the time.

    The version comes from :data:`VERSION_FOR` keyed by the claim's own
    framework, never from a default. A default is a single rule for a table with
    one row per package: it keyed an ASVS claim under STRIDE's rule, which reads
    an action verb an ASVS claim does not carry, so every ASVS vote raised
    instead of recording. ``version`` stays overridable for a caller re-keying a
    row deliberately.
    """
    if version is None:
        version = version_for(components.framework)
    return Vote(
        fingerprint=fingerprint(components, version=version),
        components=components,
        case=case,
        verdict=verdict,
        voter=voter,
        recorded=datetime.now(UTC).isoformat(timespec="seconds"),
        reason=reason,
        claim_text=claim_text,
        config=config,
        note=note,
        sitting=sitting,
    )


@dataclass
class Ledger:
    """Every vote ever cast, in the order it was cast.

    Loaded whole. At a corpus of thirteen cases and a few hundred findings the
    files are small enough that streaming would buy nothing and cost the
    ability to answer a question about the whole history in one pass. When
    they stop being small the answer is a different store, not a lazier
    reader.
    """

    votes: list[Vote] = field(default_factory=list)
    path: Path | None = None

    def __len__(self) -> int:
        return len(self.votes)

    def __iter__(self) -> Iterator[Vote]:
        return iter(self.votes)

    def current(self) -> dict[tuple[str, str], Vote]:
        """The live verdict per ``(fingerprint, voter)``: the latest event.

        Latest by position rather than by timestamp. The file is append-only, so
        position *is* chronology, and two events written inside one second would
        otherwise tie on a value recorded to the second.
        """
        live: dict[tuple[str, str], Vote] = {}
        for vote in self.votes:
            live[(vote.fingerprint, vote.voter)] = vote
        return live

    def current_by_finding(self) -> dict[str, list[Vote]]:
        """Every finding's live verdicts, keyed by fingerprint, in one pass.

        What a reader of many findings wants: filtering :meth:`current` per
        finding walks the whole ledger once per question, and both the scorer
        and the writing instrument ask it of every claim in a sweep.
        """
        by_finding: dict[str, list[Vote]] = {}
        for (value, _), vote in self.current().items():
            by_finding.setdefault(value, []).append(vote)
        return by_finding

    def voted_fingerprints(self) -> frozenset[str]:
        """Every fingerprint anybody has answered, for the queue to skip."""
        return frozenset(vote.fingerprint for vote in self.votes)

    def pool(self) -> frozenset[str]:
        """The reference pool: every fingerprint a live verdict puts in it.

        Derived, never stored. A pool file kept beside the ledger would be a
        second source of truth that could disagree with it, and the disagreement
        would be silent. Recomputing costs one pass over a small file.
        """
        return frozenset(
            key[0] for key, vote in self.current().items() if vote.joins_the_pool
        )

    def for_fingerprint(self, value: str) -> tuple[Vote, ...]:
        """Every vote on one finding, oldest first, including superseded ones."""
        return tuple(vote for vote in self.votes if vote.fingerprint == value)

    def voters(self) -> tuple[str, ...]:
        """Everyone who has ever voted, sorted, for the agreement measures."""
        return tuple(sorted({vote.voter for vote in self.votes}))

    def double_voted(self) -> tuple[str, ...]:
        """Fingerprints two or more people answered independently.

        The sample that measures reviewer agreement, which is the ceiling on
        what any single vote can mean. Nothing else in this repository has ever
        had one.
        """
        by_finding: dict[str, set[str]] = {}
        for vote in self.votes:
            by_finding.setdefault(vote.fingerprint, set()).add(vote.voter)
        return tuple(sorted(key for key, who in by_finding.items() if len(who) > 1))


def load(path: Path | str = DEFAULT_LEDGER_PATH) -> Ledger:
    """Read every voter's file in filename order, failing closed on any row.

    ``path`` is the ledger *directory*, one ``<login>.jsonl`` per voter. A
    missing directory is an empty ledger rather than an error: before the
    first sitting there are no votes, and that is a starting state and not a
    fault. A single ledger file is refused outright — the one-file shape was
    dropped by #322, and reading it as empty would silently discard votes.
    """
    path = Path(path)
    if path.is_file():
        raise LedgerError(
            f"{path}: the ledger is a directory of <login>.jsonl files, one"
            " per voter; a single ledger file is not a shape this loader reads"
        )
    if not path.exists():
        return Ledger(votes=[], path=path)

    votes: list[Vote] = []
    for file in sorted(path.glob("*.jsonl")):
        votes.extend(_read_voter_file(file))
    return Ledger(votes=votes, path=path)


def _read_voter_file(path: Path) -> list[Vote]:
    """One voter's history, every row checked against the filename it lives in."""
    login = path.stem
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"{path}: cannot be read: {exc}") from exc

    votes: list[Vote] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path}:{number}: invalid JSON: {exc}") from exc
        try:
            vote = Vote.from_json(raw)
        except LedgerError as exc:
            raise LedgerError(f"{path}:{number}: {exc}") from exc
        if vote.voter != login:
            raise LedgerError(
                f"{path}:{number}: names voter {vote.voter!r}, but this file"
                f" is {login!r}'s; one voter's history lives in one file"
            )
        votes.append(vote)
    return votes


def append(vote: Vote, path: Path | str = DEFAULT_LEDGER_PATH) -> Vote:
    """Add one vote to the end of its voter's file, and never touch what is there.

    ``path`` is the ledger directory; the vote routes to
    ``<path>/<voter>.jsonl``, which is safe because :class:`Vote` refused any
    voter that does not hold the GitHub login shape. Opened in append mode
    with one ``write`` of one line, so a crash mid-sitting truncates at a row
    boundary rather than corrupting the row before it. The directory is
    created because a first sitting should not fail on a missing folder.
    """
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{vote.voter}.jsonl"
    line = json.dumps(vote.to_json(), ensure_ascii=False, sort_keys=True)
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LedgerError(f"{target}: cannot be written: {exc}") from exc
    return vote


def write_all(votes: Sequence[Vote], path: Path | str) -> None:
    """Write a whole ledger atomically per file. For a re-key and for tests, never for a vote.

    A re-key under a new fingerprint version rewrites every row from its
    stored components. That is the one legitimate whole-ledger write. Every
    line serialises before the first byte lands, so a row that cannot
    serialise raises while the old files are still whole; each file then goes
    through a temporary sibling and a rename, so an interruption between two
    files leaves every file whole — old or new — and git covers that window.
    """
    directory = Path(path)
    lines_by_voter: dict[str, list[str]] = {}
    for vote in votes:
        line = json.dumps(vote.to_json(), ensure_ascii=False, sort_keys=True)
        lines_by_voter.setdefault(vote.voter, []).append(line)
    directory.mkdir(parents=True, exist_ok=True)
    for login, lines in sorted(lines_by_voter.items()):
        _replace_voter_file(directory / f"{login}.jsonl", lines)


def _replace_voter_file(path: Path, lines: list[str]) -> None:
    # A temporary file in the *same* directory, so the rename below is atomic:
    # ``os.replace`` is only atomic within one filesystem, and the system
    # temporary directory is often a different one.
    scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with scratch.open("w", encoding="utf-8") as handle:
            handle.write("".join(line + "\n" for line in lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(scratch, path)
    except OSError as exc:
        scratch.unlink(missing_ok=True)
        raise LedgerError(f"{path}: cannot be written: {exc}") from exc


def rekey(votes: Iterable[Vote]) -> list[Vote]:
    """Recompute every fingerprint from stored components, under the table.

    The operation that makes a better recogniser affordable: no re-vote, no
    provider, no credentials, and the answer is a pure function of the ledger.
    A vote whose components cannot satisfy its framework's version raises, so a
    partial re-key is impossible.

    Each row is keyed under :data:`VERSION_FOR` for **its own** framework rather
    than under one version for the file. One version for the file cannot be
    right once the table holds two: a ledger carrying a STRIDE row and an ASVS
    row had no value that re-keyed it, because either choice raised on the other
    package's rows. So a rule improves by editing its package's entry in the
    table, and this recomputes what that moved.
    """
    return [
        replace(
            vote,
            fingerprint=fingerprint(
                vote.components, version=version_for(vote.components.framework)
            ),
        )
        for vote in votes
    ]


def command_rekey(args: argparse.Namespace) -> int:
    """Recompute every vote's fingerprint under a new rule, in place.

    The operation the whole versioning argument rests on: a better recogniser
    changes every key, and a vote stores its **components** rather than its
    hash, so moving the ledger is arithmetic over a file. No provider, no
    credentials, no re-vote: the ledger stores each vote's components, so a
    version bump is a pure recomputation over the file.

    Every row is keyed under its own framework's entry in ``VERSION_FOR``, so
    the way to move a rule is to edit that entry and run this. There is no
    target version to pass: one version for the whole file stopped being a
    coherent request when the table grew its second row.

    Refuses to write anything unless ``--yes`` is given, like ``promote``: this
    rewrites the only human record in the repository, and a preview that also
    edited would be a preview nobody could trust. Each voter's file is replaced
    by an atomic rename, so an interrupted re-key leaves every file whole —
    old or new — rather than half of a new one.
    """
    path = Path(args.ledger)
    current = load(path)
    if not current:
        print(f"{path}: no votes to re-key")
        return 0

    try:
        moved = rekey(current.votes)
    except FingerprintError as exc:
        print(f"cannot re-key: {exc}")
        return 1

    changed = sum(
        1
        for before, after in zip(current.votes, moved, strict=True)
        if before.fingerprint != after.fingerprint
    )
    was = sorted({version_of(v.fingerprint) for v in current.votes})
    now = sorted({version_of(v.fingerprint) for v in moved})
    print(f"{len(moved)} votes at version {was} -> {now}")
    print(f"{changed} fingerprints move, {len(moved) - changed} unchanged")
    print(f"{len(current.pool())} findings in the pool, before and after")

    if not args.yes:
        print("\npreview only; nothing written. Re-run with --yes to apply.")
        return 0

    write_all(moved, path)
    print(f"\n{path} rewritten")
    return 0
