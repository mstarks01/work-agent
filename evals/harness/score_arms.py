"""Turn a set of archived head-only sweeps into #1003's runs file.

The step between a pilot and a comparison. Each sweep is one arm's runs over
the cases it covered; this replays every case in it against the signed
reference and records what the primary endpoint counts, so
:func:`~evals.harness.arms.report` can be recomputed from the file without
touching a provider again.

**It grades nothing of its own.**
:func:`~evals.harness.replay.replay_assertions` decides which reference rows a
run answered, under the aliases a reviewer signed, and
:meth:`~evals.harness.arms.ArmRun.of` narrows those fates to the stated rows the
denominator counts. A case whose reference nobody has signed is skipped and
named, because a run graded against a draft is graded against nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from evals.harness import replay
from evals.harness.arms import ARMS, ArmRun, write_runs
from evals.harness.artifact import load_artifact
from evals.harness.bundle import heads_from_reports
from evals.harness.reference import GoldenCase, load_corpus

#: How an artifact is named on the command line: ``<arm>=<path>``, optionally
#: ``<arm>:<repeat>=<path>``. The arm cannot be read off the artifact — two
#: arms can run one prompt digest under one model — so the caller states it.
SPEC = "arm[:repeat]=path"


def parse_spec(spec: str) -> tuple[str, int, Path]:
    """One ``arm[:repeat]=path`` argument, or a refusal naming what is wrong."""
    head, separator, path = spec.partition("=")
    if not separator or not path:
        raise ValueError(f"{spec!r} is not {SPEC}")
    arm, _, repeat = head.partition(":")
    if arm not in ARMS:
        raise ValueError(f"{spec!r} names arm {arm!r}, not one of {sorted(ARMS)}")
    if repeat and not repeat.isdigit():
        raise ValueError(f"{spec!r} names repeat {repeat!r}, which is not a number")
    return arm, int(repeat or 0), Path(path)


def score(
    specs: Sequence[tuple[str, int, Path]],
    cases: Sequence[GoldenCase],
    corpus_dir: Path,
) -> tuple[list[ArmRun], dict[str, str]]:
    """Every run these sweeps hold, and every case that nothing grades.

    **A case the sweep ran and did not archive is a failed run, not an absent
    one.** The artifact names every case the sweep attempted, so the two are
    distinguishable, and the difference decides whether an arm's failure shows
    up as a lower recall or as no recall at all.
    """
    runs: list[ArmRun] = []
    skipped: dict[str, str] = {}
    for arm, repeat, path in specs:
        loaded = load_artifact(path)
        if loaded.mode != "heads":
            raise ValueError(
                f"{path}: a {loaded.mode} sweep is not one arm's head;"
                " this reads the heads mode"
            )
        held = [case for case in cases if case.id in loaded.cases]
        produced = heads_from_reports(path, held)
        for case in held:
            reference = replay.signed_reference(corpus_dir, case)
            if reference is None:
                unsigned = replay.unsigned_rows(corpus_dir, case)
                skipped[case.id] = (
                    "no facts file, so nothing grades it"
                    if unsigned is None
                    else f"{unsigned} unsigned reference row(s)"
                )
                continue
            if case.id not in produced:
                # The sweep ran this case and archived no catalog for it, which
                # is a job that produced nothing usable. #1003 asks that such a
                # run recover none of the case's required facts rather than
                # leave the denominator: an arm that fails half the time must
                # not score as if it had only run the half it finished.
                runs.append(ArmRun.unusable(case.id, reference, arm=arm, repeat=repeat))
                continue
            graded = replay.replay_assertions(case, reference, produced[case.id])
            runs.append(ArmRun.of(graded, reference, arm=arm, repeat=repeat))
    return runs, skipped


def arguments(parser: argparse.ArgumentParser) -> None:
    """The sweeps, the corpus they are graded against, and where the file goes."""
    parser.add_argument(
        "artifact",
        nargs="+",
        metavar=SPEC,
        help="each arm's archived head-only sweep, with its .reports/ beside it",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("evals") / "corpus",
        help="corpus root: the blessed models and the signed reference facts",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="where to write the runs file `compare-arms` reads",
    )


def command_score_arms(args: argparse.Namespace) -> int:
    """Replay every arm's sweeps into the file the comparison reads.

    Credential-free: the sweeps are on disk and the corpus holds the signed
    references, so a reference correction is answered by running this again
    rather than by paying for the pilot twice.
    """
    try:
        specs = [parse_spec(spec) for spec in args.artifact]
        cases = load_corpus(args.corpus)
        runs, skipped = score(specs, cases, args.corpus)
    except (ValueError, OSError) as error:
        print(f"cannot score: {error}", file=sys.stderr)
        return 1
    if not runs:
        print("no sweep held a case with a signed reference", file=sys.stderr)
        return 1
    write_runs(args.out, runs)
    for case_id, why in sorted(skipped.items()):
        print(f"skipped {case_id}: {why}", file=sys.stderr)
    arms = sorted({run.arm for run in runs})
    print(f"{len(runs)} run(s) over arms {arms} written to {args.out}")
    return 0
