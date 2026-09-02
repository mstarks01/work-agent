"""What a **Standing** does to a number, and how a maintainer judges a promotion.

Two jobs, one subject, so one module.

Standing selects which votes a series reads, and does nothing else (#326). It
never weighs a vote: inside any series a substance rejection still wins over a
pooled finding, whoever cast it. A series is therefore a filter over the ledger,
and :data:`SERIES` is the table of them, holding a key and the standings it
includes. Every sweep computes every series in one pass, and each one names its
standings, so no flag can quietly move a published number.

A promotion is a roster edit, and code never makes it. An agreement threshold
promotes on noise when the overlap is small, and a time rule measures patience
rather than judgement. :func:`agreement` is an instrument for a maintainer to
read before they decide. For each pair of voters it takes the findings both
answered, maps each live vote to a substance class, and reports the share where
the two agree. It prints the sample size beside every figure and enforces no
floor, because a printed sample size lets a maintainer weigh a thin number where
a code gate would only hide it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import Literal

from evals.harness import ledger, roster
from evals.harness.ledger import Ledger, Vote
from evals.harness.roster import Roster, Standing

#: Each published series, and the standings whose votes it reads. The primary
#: series is maintainer-only: a contributor's votes stay a second series until
#: somebody promotes them, and until then they cannot move the headline
#: number without a roster diff saying so (#320).
#:
#: A table rather than a flag, so adding a series is an entry here and every
#: sweep starts computing it. The first key is the primary one.
SERIES: dict[str, tuple[Standing, ...]] = {
    "maintainer": ("maintainer",),
    "all": ("maintainer", "contributor"),
}

#: The published series, whose numbers the artifact's scored blocks carry.
PRIMARY = next(iter(SERIES))

#: What a live vote says about the finding's substance. ``open`` is a real
#: answer that moves nothing — ``unsure`` and ``needs-evidence`` both land
#: here — and it is what a pair comparison excludes rather than counts as a
#: disagreement.
SubstanceClass = Literal["rejected", "pooled", "open"]


def classify(vote: Vote) -> SubstanceClass:
    """One vote's substance class. A style rejection is ``pooled``.

    The finding stays in the reference pool when a reviewer objects only to
    how it reads, so two reviewers who agree it is real and disagree about its
    wording agree *here* — which is what keeps taste out of an agreement
    number, exactly as it is kept out of recall.
    """
    if vote.counts_against_analysis:
        return "rejected"
    if vote.joins_the_pool:
        return "pooled"
    return "open"


def narrow(votes: Ledger, roster: Roster, standings: tuple[Standing, ...]) -> Ledger:
    """The ledger as one series reads it: only these standings' votes.

    A voter with no roster line is read by no series. That is deny-by-default
    and it is deliberate: an unrostered voter has no standing, so no series
    can state which standings its number includes while counting them.
    """
    wanted = set(standings)
    return replace(
        votes,
        votes=[
            vote
            for vote in votes
            if vote.voter in roster and roster.standing_of(vote.voter) in wanted
        ],
    )


@dataclass(frozen=True)
class PairAgreement:
    """How two voters compared on the findings they both answered."""

    voters: tuple[str, str]
    standings: tuple[Standing, Standing]
    compared: int
    excluded: int
    agreed: int

    @property
    def rate(self) -> float | None:
        """The share compared where both classes matched, or None at zero."""
        return self.agreed / self.compared if self.compared else None

    @property
    def crosses_the_line(self) -> bool:
        """Whether this pair spans the standing boundary — what a promotion reads."""
        return self.standings[0] != self.standings[1]


#: What the spec recommends before a maintainer reads an agreement figure as
#: signal — the size of the first review sitting. **Not a floor.** Nothing
#: refuses a thinner number; the report prints the sample size so the
#: maintainer weighs it themselves.
RECOMMENDED_SAMPLE = 30


def agreement(votes: Ledger, roster: Roster) -> list[PairAgreement]:
    """Every voter pair's agreement over the findings both of them answered.

    Read-only, and it decides nothing: a promotion is a roster edit a
    maintainer makes (#326). Pairs come back sorted by voter name, and a pair
    with no overlap at all is dropped rather than reported as zero agreement.
    """
    live = votes.current()
    by_voter: dict[str, dict[str, Vote]] = {}
    for (value, voter), vote in live.items():
        by_voter.setdefault(voter, {})[value] = vote

    pairs = []
    for first, second in combinations(sorted(by_voter), 2):
        if first not in roster or second not in roster:
            continue
        shared = sorted(set(by_voter[first]) & set(by_voter[second]))
        if not shared:
            continue
        compared = agreed = excluded = 0
        for value in shared:
            one = classify(by_voter[first][value])
            other = classify(by_voter[second][value])
            if one == "open" or other == "open":
                excluded += 1
                continue
            compared += 1
            agreed += one == other
        pairs.append(
            PairAgreement(
                voters=(first, second),
                standings=(roster.standing_of(first), roster.standing_of(second)),
                compared=compared,
                excluded=excluded,
                agreed=agreed,
            )
        )
    return pairs


def render(pairs: list[PairAgreement]) -> str:
    """The report a maintainer reads before deciding a promotion."""
    if not pairs:
        return (
            "No two rostered people have answered the same finding, so there"
            " is nothing to compare. Agreement needs an overlap: point a"
            " second reviewer at an artifact somebody has already voted on."
        )
    lines = [
        "Pairwise agreement over the findings both voters answered.",
        "",
        "| Voters | Standings | Compared | Excluded (open) | Agreement |",
        "| --- | --- | --- | --- | --- |",
    ]
    for pair in pairs:
        rate = "—" if pair.rate is None else f"{pair.rate:.0%}"
        crossing = " *" if pair.crosses_the_line else ""
        lines.append(
            f"| {pair.voters[0]} / {pair.voters[1]}{crossing}"
            f" | {pair.standings[0]} / {pair.standings[1]}"
            f" | {pair.compared} | {pair.excluded} | {rate} |"
        )
    thin = [pair for pair in pairs if pair.compared < RECOMMENDED_SAMPLE]
    lines += [
        "",
        (
            "`*` marks a pair that crosses the standing line — the comparison"
            " a promotion rests on."
        ),
        (
            "An answer is excluded when either voter left it open (`unsure` or"
            " `needs-evidence`); an excluded finding is not a disagreement."
        ),
    ]
    if thin:
        lines.append(
            f"{len(thin)} pair(s) compared fewer than {RECOMMENDED_SAMPLE}"
            " findings. Nothing refuses a thin figure — weigh it yourself."
        )
    lines.append(
        "\nNothing here promotes anybody. A promotion is a line in"
        " `evals/review/voters.toml` flipped to `maintainer`, in a PR a"
        " maintainer merges; it re-classes that voter's whole history at once."
    )
    return "\n".join(lines)


def command_agreement(args: argparse.Namespace) -> int:
    """How far two voters agree — what a maintainer reads before promoting.

    Read-only and credential-free, like ``review`` beside it. It decides
    nothing: a promotion is a line in the roster flipped to ``maintainer``, in
    a PR a maintainer merges (#326). An agreement threshold in code would
    promote on noise whenever the overlap is thin, which is exactly when the
    figure needs a person's judgement rather than a comparison.
    """
    votes = ledger.load(Path(args.ledger))
    try:
        table = roster.load(Path(args.roster))
    except roster.RosterError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(render(agreement(votes, table)))
    return 0
