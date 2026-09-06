"""Every command the prose prints, checked against the parser that would run it.

``tests/test_doc_reference_lints.py`` draws the line this sits on: a document is
right about the code in two ways, it **names** things that exist and it **says
true things** about them, and only the first is decidable. A command line is the
first kind. It names a subcommand and some flags, and whether those exist is a
question ``argparse`` already answers.

It is worth answering because these documents are procedures. ``evals/BLESSING.md``
and ``evals/VOTING.md`` are not prose about the harness, they are the steps a
person follows with a terminal open, so a flag that no longer exists is a broken
instrument rather than a typo. That is not hypothetical: removing
``rekey --to-version`` left it printed in two documents, one of them the
documented way to move the ledger after a rule change, and nothing failed.

**Flags, not whole invocations.** The lint checks that each subcommand exists and
that each ``--flag`` beside it is one that subcommand accepts. It deliberately
does not run ``parse_args``: a documented line carries placeholders rather than
real values, and some are fragments shown to make a point. Requiring a complete,
valid invocation would fail on prose that is doing its job, and a lint an author
learns to override is worse than none.

**Parsers, not a second list.** The option strings come from the real parser, by
building it exactly as ``main`` does. A lint holding its own copy of the flags
would be one more thing to forget.
"""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

import pytest

from evals.harness.run import COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where a reader finds a command to type. The harness's own guides first,
#: because those are the procedures.
SEARCHED = ("evals", "docs", "README.md")

#: The module whose commands this lint knows how to check. Others are named in
#: the prose too -- ``python -m analysis_service.smoke`` and friends -- and are
#: left alone rather than half-checked: they take their arguments from their own
#: parsers, and a lint that guessed would be the second list this avoids.
MODULE = "evals.harness.run"

#: An invocation runs to the end of its line, or to the backtick that closes an
#: inline span. Both shapes are read: a command in running prose is one a reader
#: types just as readily as one in a fenced block, and most of them are inline.
_INVOCATION = re.compile(rf"python -m {re.escape(MODULE)}\s+([^\n`]*)")


def _parsers() -> dict[str, argparse.ArgumentParser]:
    """Each subcommand's parser, built the way ``main`` builds it."""
    built = {}
    for name, command in COMMANDS.items():
        sub = argparse.ArgumentParser(prog=name)
        if command.arguments is not None:
            command.arguments(sub)
        built[name] = sub
    return built


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def _documented() -> list[tuple[Path, int, str]]:
    """Every ``python -m evals.harness.run`` invocation the prose prints."""
    found = []
    for entry in SEARCHED:
        base = REPO_ROOT / entry
        paths = [base] if base.is_file() else sorted(base.rglob("*.md"))
        for path in paths:
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                match = _INVOCATION.search(line)
                if match:
                    found.append((path, number, match.group(1)))
    return found


def _tokens(rest: str) -> list[str]:
    """The command's words, with prose placeholders made safe to split.

    ``shlex`` chokes on an unbalanced quote, which a placeholder can carry, and
    the values are not what this reads anyway.
    """
    try:
        return shlex.split(rest, comments=True)
    except ValueError:
        return rest.split()


def test_every_documented_command_exists():
    wrong = []
    for path, line, rest in _documented():
        tokens = _tokens(rest)
        if tokens and tokens[0] not in COMMANDS:
            wrong.append(
                f"{path.relative_to(REPO_ROOT)}:{line} names {tokens[0]!r},"
                f" which is not a command"
            )

    assert not wrong, (
        f"the prose prints commands that do not exist: {wrong}. A document that"
        " tells somebody what to type is a procedure, so this fails where they"
        " would."
    )


def unaccepted_flags(rest: str) -> list[str]:
    """The flags in one invocation that its subcommand does not accept."""
    tokens = _tokens(rest)
    parsers = _parsers()
    if not tokens or tokens[0] not in parsers:
        return []
    accepted = _options(parsers[tokens[0]])
    return [
        token.split("=", 1)[0]
        for token in tokens[1:]
        if token.startswith("--") and token.split("=", 1)[0] not in accepted
    ]


def test_every_documented_flag_is_one_its_command_accepts():
    wrong = [
        f"{path.relative_to(REPO_ROOT)}:{line} passes {flag} to"
        f" {_tokens(rest)[0]!r}, which does not accept it"
        for path, line, rest in _documented()
        for flag in unaccepted_flags(rest)
    ]

    assert not wrong, (
        f"the prose prints flags that no longer exist: {wrong}. Removing a flag"
        " is half the change; the document that tells somebody to type it is"
        " the other half."
    )


def test_the_lint_reads_a_real_population():
    """Guards the guard: a regex that matched nothing would pass vacuously."""
    documented = _documented()

    # 37 measured on 2026-09-06. The bound was 40 while the twelve generated
    # reading documents each carried a `submit sitting` line; that command
    # went with the case-local sitting format (ADR 24), and the documents no
    # longer name one.
    assert len(documented) > 30, (
        f"only {len(documented)} documented invocations found -- the"
        " invocation pattern has stopped matching"
    )


@pytest.mark.parametrize(
    ("invocation", "caught"),
    [
        ("rekey --to-version 3 --yes", ["--to-version"]),
        ("rekey --yes", []),
        ("sitting-import x.json --submitted-by ada", []),
        ("sitting-import x.json --invented", ["--invented"]),
        ("score artifact.json --roster evals/review/voters.toml", []),
    ],
)
def test_the_check_catches_a_flag_a_command_does_not_accept(invocation, caught):
    """The lint's own teeth, led by the flag whose removal it was written for.

    Asserted on the check rather than on the tree, so it still means something
    once the tree is clean -- which is the state a passing lint leaves it in.
    """
    assert unaccepted_flags(invocation) == caught
