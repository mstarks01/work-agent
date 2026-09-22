"""What a corpus addition inherits from a ruling nobody made about it.

**A reference claim's identity outlives the claim that holds it.** When a
blessing pass drops a claim its fingerprint is free again, and a later addition
can land on it. A **Case Sitting** mark stores only a key, so
:func:`~evals.harness.sitting.current_marks` re-keys the reader's ruling onto
the new text, and the gate reads as affirmed by a person who never saw it.

**No check over the tree alone can separate that from an earned mark.** The
discriminator is which claims are new, and the tree does not carry it: where
every case is unread, each claim legitimately carrying its own mark looks
identical to one that inherited another claim's. So this reads a base revision
and is a preflight a person runs, rather than a gate CI can make.

**Run it before the addition lands, because the evidence expires.** Once a
sitting covering the addition merges, every claim carries a mark and an
inherited one cannot be told from an earned one.

Framework-neutral by construction: the identities come from
:func:`~evals.harness.sitting.mark_targets`, which keys each package's claims
through that package's own rule, so a package composing its identity from a
catalog requirement is read here exactly as one composing it from an action.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evals.harness.reference import load_case
from evals.harness.sitting import current_marks, mark_targets, prepare
from evals.review_submission import iter_submissions

__all__ = [
    "Inherited",
    "PreflightError",
    "arguments",
    "command_preflight",
    "inherited_rulings",
    "keys_of",
]


class PreflightError(RuntimeError):
    """A base revision this command cannot read, naming the path and the reason."""


@dataclass(frozen=True)
class Inherited:
    """One added claim that lands where a ruling already sat, and whose ruling."""

    case: str
    fingerprint: str
    claim: str
    #: The mark a merged sitting recorded against this identity, before this
    #: claim held it. A ruling for the maintainer, never automatically a
    #: duplicate: the reader's reasoning may be about a mechanism this claim
    #: does not use.
    mark: str


def keys_of(case_dir: Path) -> dict[str, str]:
    """Every recorded finding of one case, as fingerprint to claim sentence.

    Read through :func:`~evals.harness.sitting.mark_targets` rather than off the
    claim files, so this states no rule about which package keys a claim how.
    """
    return {
        target.fingerprint: " || ".join(target.claims)
        for target in mark_targets(load_case(case_dir))
    }


def inherited_rulings(
    case: str,
    current: Mapping[str, str],
    base: Mapping[str, str],
    marks: Mapping[str, str],
) -> list[Inherited]:
    """The added findings that land on an identity a sitting already marked.

    Added means present now and absent at the base. A claim the base also
    carried is not an addition whatever its text says, because its identity did
    not move — which is what makes a reword lossless here as well as for the
    matcher.
    """
    return [
        Inherited(case, fingerprint, claim, str(marks[fingerprint]))
        for fingerprint, claim in current.items()
        if fingerprint not in base and fingerprint in marks
    ]


def _case_at(revision: str, case: str, root: Path, into: Path) -> Path | None:
    """One case's tree at ``revision``, materialised so a case reader can open it.

    Returns ``None`` where the revision carried no such case, which is the
    answer for a case the change adds outright.
    """
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", revision, f"evals/corpus/{case}/"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    names = [line for line in listing.stdout.splitlines() if line.strip()]
    if not names:
        return None
    case_dir = into / case
    for name in names:
        blob = subprocess.run(
            ["git", "show", f"{revision}:{name}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if blob.returncode:
            # `git ls-tree` just listed this path, so a failure here is a
            # broken read rather than an absent file. Skipping it would drop
            # claims out of the base and report every one of them as an
            # addition, which is the answer this command exists to give
            # correctly.
            raise PreflightError(
                f"{revision}:{name} is listed at that revision and cannot be"
                f" read: {blob.stderr.decode('utf-8', 'replace').strip()}"
            )
        target = case_dir / Path(name).relative_to(f"evals/corpus/{case}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.stdout)
    return case_dir


def _merged_marks(root: Path, case_dir: Path) -> dict[str, str]:
    """Every merged sitting's marks on one case, re-keyed to today's identities.

    Read through :func:`~evals.review_submission.iter_submissions`, which is
    the loader every other consumer of these files already uses. A submission
    arrives by pull request, so what a hand-edited one may hold is the
    producer's question rather than the shape the merged files happen to carry:
    a top level that is not a table has no ``cases``, and a ``cases`` entry that
    is not one has no ``marks``. The loader answers for both and refuses with a
    message naming the file, where a second reader spelled here would answer for
    whichever shape its author thought of.
    """
    prepared = prepare(case_dir)
    marks: dict[str, str] = {}
    for _, envelope in iter_submissions(root):
        answers = envelope.cases.get(case_dir.name)
        if answers is not None:
            marks.update(current_marks(prepared, answers.marks))
    return marks


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base",
        required=True,
        help="the revision to read the corpus as it stood before this change",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="the repository root (default: this checkout)",
    )


def command_preflight(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    corpus = root / "evals" / "corpus"
    found: list[Inherited] = []
    added = 0
    with tempfile.TemporaryDirectory() as raw:
        scratch = Path(raw)
        for case_dir in sorted(p for p in corpus.iterdir() if p.is_dir()):
            current = keys_of(case_dir)
            was = _case_at(args.base, case_dir.name, root, scratch)
            base = keys_of(was) if was is not None else {}
            added += len(set(current) - set(base))
            found.extend(
                inherited_rulings(
                    case_dir.name, current, base, _merged_marks(root, case_dir)
                )
            )
    print(f"{added} finding(s) added since {args.base}")
    for one in found:
        print(f"\n{one.case}: {one.fingerprint}")
        print(f"  the claim:  {one.claim}")
        print(f"  a merged sitting already marked this identity: {one.mark!r}")
    if found:
        print(
            f"\n{len(found)} addition(s) land where a ruling already sat. None is"
            " automatically a duplicate — the reader's reasoning may be about a"
            " mechanism the new claim does not use — but each is a ruling for the"
            " maintainer before it lands."
        )
        return 1
    print("no addition lands on an identity a merged sitting already marked")
    return 0
