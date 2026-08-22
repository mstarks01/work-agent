"""Nothing outside a package states the fan-out as a fixed number.

## What went wrong

How wide a job fans out stopped being a constant the day a second package
registered. It is one ``strong``-tier request per lane of every framework the
job names — :func:`~stride_service.frameworks.widest_fan_out`, 23 today — and it
was 6 for as long as STRIDE was the only package.

[#199](https://github.com/mstarks01/work-agent/issues/199) corrected the
arithmetic behind the concurrency ceiling and stopped at the file it was looking
at. Nine other places went on reasoning from six: the retry budget, the jitter
policy, the per-tier budget argument, the admission cap, and the module
docstrings above three of them. None of it was arithmetic a computer ran — no
bound in the package is *computed* from six — so nothing failed. It was the
reasoning a maintainer reads before turning one of those knobs, and it said
sixty where the truth was 230.

## Why a lint rather than care

The prose is right today because #286 rewrote it. The lint is what stops the
third package putting it back: **the fan-out has no correct fixed number**, so
outside a package's own tree, any fixed count of lane agents is wrong on its
face and needs no judgement to reject.

## Two exclusions, and the reason for each

``frameworks/<name>/`` is a package talking about itself, where "six categories"
is a fact rather than a fan-out claim. ``docs/adr/`` is a dated record of what
was decided when it was decided; ADR 0007 was written when six was the whole
fan-out, and it carries an amendment note rather than a rewrite, the way ADRs
0004, 0006, 0008 and 0012 do.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stride_service.frameworks import PACKAGES, widest_fan_out

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where a fan-out claim can be made, and the suffixes worth reading.
SEARCHED = ("src", "config", "evals", "docs")
SUFFIXES = (".py", ".toml", ".md")

#: A fixed count of the agents a job fans out. Words as well as digits, because
#: every instance #286 found was spelled "six" rather than "6".
#:
#: **"one" and "two" are left out, and they are the interesting exclusion.** In
#: this codebase they read as quantifiers rather than counts — ``join_drafts``
#: "fails closed if two category agents reuse one ID" says *any two*, and is
#: true whatever the fan-out is. A fan-out claim asserts how many there are, and
#: no package ships a lane count those two words could plausibly state.
FIXED_FAN_OUT = re.compile(
    r"\b(?:three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
    r"|\d+)\s+(?:category|lane)\s+agents\b",
    re.IGNORECASE,
)

#: A claim about the registry: "STRIDE declares 6". Narrow on purpose. A general
#: "any number near a package name" rule reads ``the other 16 lanes' work`` in
#: ``asvs/record.py`` as a defect, and that sentence is correct — it is about
#: what one failing lane costs the rest, not about how many ship.
DECLARES = re.compile(
    r"\b(" + "|".join(PACKAGES) + r")\s+declares\s+(\d+)\b", re.IGNORECASE
)


def _searched_files() -> list[Path]:
    return [
        path
        for root in SEARCHED
        for path in sorted((REPO_ROOT / root).rglob("*"))
        if path.suffix in SUFFIXES
        and path.is_file()
        and not any(f"/frameworks/{name}/" in path.as_posix() for name in PACKAGES)
        and "/docs/adr/" not in path.as_posix()
    ]


def test_there_are_files_to_search():
    """Guards the guard: an empty file list agrees with anything."""
    assert len(_searched_files()) >= 40


def test_no_fixed_count_of_lane_agents_outside_a_package():
    """The fan-out is a function of the registry, so no prose may fix it.

    Failing here does not mean deleting the sentence. State the rule instead —
    "one request per lane of every framework the job names", or point at
    :func:`~stride_service.frameworks.widest_fan_out` — because that stays true
    for a package nobody has written yet.
    """
    hits = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {match.group(0)}"
        for path in _searched_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if (match := FIXED_FAN_OUT.search(line))
    ]

    assert not hits, (
        "these state the fan-out as a fixed number, which stopped being true "
        f"when a second package registered: {hits}"
    )


def test_every_stated_lane_count_matches_the_registry():
    """A claim about what a package declares has to be what it declares.

    The concurrency ceiling's own comment does the arithmetic in prose because
    the reader turning that knob needs to see it. This is what keeps the prose
    and ``PACKAGES`` from disagreeing, which is the half of #199 that a comment
    cannot check about itself.
    """
    wrong = [
        f"{path.relative_to(REPO_ROOT)}:{number}: says {name} declares {count},"
        f" registry says {len(PACKAGES[name.lower()].lanes)}"
        for path in _searched_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for name, count in DECLARES.findall(line)
        if int(count) != len(PACKAGES[name.lower()].lanes)
    ]

    assert not wrong, wrong


def test_the_widest_fan_out_is_every_packages_lanes():
    """The function, against the registry it reads.

    Sums every package rather than taking the widest one, because a job may name
    them all and the ceiling has to hold for the job a caller may submit.
    """
    assert widest_fan_out() == sum(len(p.lanes) for p in PACKAGES.values())
    assert widest_fan_out() > max(len(p.lanes) for p in PACKAGES.values())


@pytest.mark.parametrize("framework", sorted(PACKAGES))
def test_no_single_package_accounts_for_the_fan_out(framework):
    """The property that makes a per-package number wrong.

    One package's lane count was the whole fan-out once. This is the assertion
    that says it is not any more, so a number sized against any single package
    understates the burst — which is what #199 found and #286 finished.
    """
    assert len(PACKAGES[framework].lanes) < widest_fan_out()
