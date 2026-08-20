"""The vote ledger: what a person decided about a finding, kept forever.

This is the only place in the repository where a **human** judgement is the
datum. Everything else under ``evals/`` is agent-authored — ``evals/README.md``
says so once for the whole directory — and this is the file that changes that,
one line at a time.

**Append-only, and a correction is a new event.** No row is ever updated or
removed. The current verdict for a ``(fingerprint, voter)`` pair is its latest
event by ``recorded``, which makes the ledger's history reconstructible at any
past date: a metric computed last month can be recomputed to the same number
today by ignoring the events after it. A mutable store cannot offer that, and
the whole defensibility argument rests on it.

**A vote stores its components, not just its key.** A fingerprint improves, and
improving it changes every key — see :mod:`evals.harness.fingerprint`. Storing
what the key was computed *from* makes a version bump a pure recompute over this
file rather than a re-vote, which is what stops the ledger expiring the way a
model-scored history does.

**One reason code, from a closed set, and it decides where the vote lands.** A
reviewer who dislikes a finding's writing and a reviewer who says it is not a
threat are reporting two different facts, and averaging them would let taste
move a recall number. :data:`SUBSTANCE_REASONS` counts against the analysis;
:data:`STYLE_REASONS` never does. That split is the whole control for personal
preference, and it is mechanical rather than a request in a guide.

Security: this file is the supply chain of every quality number the tool
publishes, so ``voter`` is required, is never anonymous, and is recorded on
every event (A09). Nothing here is deleted, so a bad actor's votes are
identifiable and reversible by appending, never by rewriting (A08).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from evals.harness.fingerprint import (
    DEFAULT_VERSION,
    Components,
    FingerprintError,
    fingerprint,
)

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = EVALS_ROOT / "review" / "votes.jsonl"

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
#: of these leaves every analysis metric untouched and lands in the writing
#: score instead. This is what stops a reviewer's taste moving recall.
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
        leaves the finding in the reference pool and moves the writing score
        instead; ``unsure`` and ``needs-evidence`` move nothing at all.
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
    version: int = DEFAULT_VERSION,
) -> Vote:
    """Build one validated vote, stamping its fingerprint and the time."""
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
    file is small enough that streaming would buy nothing and cost the ability
    to answer a question about the whole history in one pass. When it stops
    being small the answer is a different store, not a lazier reader.
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
    """Read the ledger, failing closed on any row that will not parse.

    A missing file is an empty ledger rather than an error: before the first
    sitting there are no votes, and that is a starting state and not a fault.
    """
    path = Path(path)
    if not path.exists():
        return Ledger(votes=[], path=path)

    votes = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"{path}: cannot be read: {exc}") from exc

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path}:{number}: invalid JSON: {exc}") from exc
        try:
            votes.append(Vote.from_json(raw))
        except LedgerError as exc:
            raise LedgerError(f"{path}:{number}: {exc}") from exc
    return Ledger(votes=votes, path=path)


def append(vote: Vote, path: Path | str = DEFAULT_LEDGER_PATH) -> Vote:
    """Add one vote to the end of the ledger, and never touch what is there.

    Opened in append mode with one ``write`` of one line, so a crash mid-sitting
    truncates at a row boundary rather than corrupting the row before it. The
    parent directory is created because a first sitting should not fail on a
    missing folder.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(vote.to_json(), ensure_ascii=False, sort_keys=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise LedgerError(f"{path}: cannot be written: {exc}") from exc
    return vote


def write_all(votes: Sequence[Vote], path: Path | str) -> None:
    """Write a whole ledger atomically. For tests and for a re-key, never for a vote.

    A re-key under a new fingerprint version rewrites every row from its stored
    components. That is the one legitimate whole-file write, and it goes through
    a temporary file and a rename so an interrupted re-key leaves the old ledger
    intact rather than half of a new one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(vote.to_json(), ensure_ascii=False, sort_keys=True) for vote in votes
    ]
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


def rekey(votes: Iterable[Vote], version: int) -> list[Vote]:
    """Recompute every fingerprint under ``version``, from stored components.

    The operation that makes a better recogniser affordable: no re-vote, no
    provider, no credentials, and the answer is a pure function of this file.
    A vote whose components cannot satisfy the new version — no verb, under
    version 2 — raises, so a partial re-key is impossible.
    """
    return [
        replace(vote, fingerprint=fingerprint(vote.components, version=version))
        for vote in votes
    ]
