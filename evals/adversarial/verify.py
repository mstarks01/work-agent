"""Mechanical checks over the adversarial corpus, and the digest stamper.

Deterministic and credential-free by construction, exactly like
``evals/verify_corpus.py``; ``tests/test_adversarial_lints.py`` runs the same
checks in CI.

**One of these is a real security check rather than a hygiene one.**
:func:`fencing_issues` renders every poisoned source through the shipped
:func:`~analysis_service.sources.render_sources` and asserts the injection is
still inside its block afterwards. A case whose text escaped its own fence would
be a defect in the renderer, on the one path every submitted byte takes, and
this is what would find it. The rest check that the corpus describes itself
honestly.

Run ``python evals/adversarial/verify.py`` to check, ``--write-sha`` to stamp
each case's ``source_sha256`` from its ``source.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from analysis_service.sources import Source, fence_for, render_sources
from evals.adversarial.model import (
    ATTACK_CLASSES,
    CASES_DIR,
    AdversarialCase,
    digest_of,
    load_corpus,
)


def digest_issues(cases: Iterable[AdversarialCase]) -> list[str]:
    """A recorded digest that is not the file's own.

    The digest is what makes a case a fixture rather than a file somebody edited
    after the last run scored it: a source whose text moved silently would carry
    a robustness number measured against different text.
    """
    return [
        f"{case.id}: source.md does not match the recorded source_sha256"
        for case in cases
        if not case.digest_matches()
    ]


def coverage_issues(cases: Iterable[AdversarialCase]) -> list[str]:
    """The corpus and :data:`ATTACK_CLASSES` compared, in both directions.

    A class with no case makes the list a statement of intent; a case naming a
    class the list does not carry makes the list short. Neither is visible
    without comparing them, which is the failure a table nobody checks against
    its registry always has.
    """
    declared = set(ATTACK_CLASSES)
    covered = {case.attack_class for case in cases}
    return [
        f"attack class {name!r} has no case; the corpus claims coverage it lacks"
        for name in sorted(declared - covered)
    ] + [
        f"case class {name!r} is not in ATTACK_CLASSES; describe it there first"
        for name in sorted(covered - declared)
    ]


def control_issues(cases: Iterable[AdversarialCase]) -> list[str]:
    """At least one case carries no injection.

    Without a benign control a service that refused every submission would score
    perfectly on every attack case, which is why the control is a *check* and
    not a convention.
    """
    controls = [case for case in cases if case.attack_class == "benign-control"]
    if controls:
        return []
    return [
        (
            "the corpus has no benign control; a service that refused everything"
            " would score perfectly against it"
        )
    ]


def retention_issues(cases: Iterable[AdversarialCase]) -> list[str]:
    """Every case states what must survive, and states it truthfully.

    Two halves. A case with no ``must_retain`` measures only what must not
    appear, which an empty answer satisfies. And a name it claims the source
    describes has to actually be in the source — otherwise the case fails every
    run for a reason that is the fixture's, not the model's.
    """
    issues = []
    for case in cases:
        retain = case.expectations.must_retain
        if not retain:
            issues.append(
                f"{case.id}: declares no must_retain, so an empty model would"
                " satisfy it"
            )
            continue
        text = case.source_text().lower()
        missing = [name for name in retain if name.lower() not in text]
        if missing:
            issues.append(
                f"{case.id}: must_retain names {missing} that source.md does not"
                " contain; the case would fail for its own reason"
            )
    return issues


def demand_issues(cases: Iterable[AdversarialCase]) -> list[str]:
    """A named invention is actually asked for by the source.

    A prohibition on a name the injection never mentions is a check that passes
    whatever the model does.
    """
    issues = []
    for case in cases:
        text = case.source_text().lower()
        absent = [
            name
            for name in case.expectations.must_not_contain_elements
            if name.lower() not in text
        ]
        if absent:
            issues.append(
                f"{case.id}: forbids {absent}, which source.md never asks for;"
                " the prohibition cannot fail"
            )
    return issues


def fencing_issues(cases: Iterable[AdversarialCase]) -> list[str]:
    """Every poisoned source stays inside its fence once rendered.

    **The one check here that tests the service rather than the corpus.** The
    fence is sized to its own content, so a source carrying a longer run of
    backticks than the renderer chose would close the block early and continue
    in instruction position. Case ``03-fence-escape`` exists precisely to try
    it, and this asserts that the attempt fails.
    """
    issues = []
    for case in cases:
        text = case.source_text()
        rendered = render_sources([Source(kind="description", label="S", text=text)])
        fence = fence_for(f"label: S\n---\n{text}")
        # A marker is a line that is *only* the fence. A run of backticks inside
        # the source is shorter than the fence by construction, so it is never
        # equal to one and never closes the block -- which is the property being
        # asserted, and the reason `fence_for` sizes to its own content.
        lines = rendered.splitlines()
        markers = [index for index, line in enumerate(lines) if line == fence]
        if len(markers) != 2:
            issues.append(
                f"{case.id}: the rendered block has {len(markers)} marker"
                f" line(s) of {len(fence)} backticks, not 2; the source escaped"
            )
            continue
        body = "\n".join(lines[markers[0] + 1 : markers[1]])
        if text.strip() not in body:
            issues.append(f"{case.id}: the source text is not inside the fence")
    return issues


CHECKS = (
    digest_issues,
    coverage_issues,
    control_issues,
    retention_issues,
    demand_issues,
    fencing_issues,
)


def all_issues(cases: Iterable[AdversarialCase] | None = None) -> list[str]:
    """Every check, over the whole corpus."""
    loaded = tuple(cases) if cases is not None else load_corpus()
    return [issue for check in CHECKS for issue in check(loaded)]


def write_digests(cases_dir: Path = CASES_DIR) -> list[str]:
    """Stamp each case's ``source_sha256`` from its own ``source.md``."""
    stamped = []
    for directory in sorted(cases_dir.iterdir()):
        if not directory.is_dir():
            continue
        manifest = directory / "case.json"
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        text = (directory / raw.get("source_file", "source.md")).read_text("utf-8")
        digest = digest_of(text)
        if raw.get("source_sha256") != digest:
            raw["source_sha256"] = digest
            manifest.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            stamped.append(directory.name)
    return stamped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sha", action="store_true")
    args = parser.parse_args(argv)

    if args.write_sha:
        stamped = write_digests()
        print(f"stamped {len(stamped)} case(s): {', '.join(stamped) or 'none'}")

    issues = all_issues()
    for issue in issues:
        print(issue, file=sys.stderr)
    if issues:
        print(f"\n{len(issues)} issue(s)", file=sys.stderr)
        return 1
    print(f"{len(load_corpus())} adversarial case(s) OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
