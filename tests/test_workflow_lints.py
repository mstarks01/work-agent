"""The live sweep's path filter, kept honest against what ``evals/`` imports.

``.github/workflows/evals-live.yml`` sweeps ``src/stride_service/**`` and
subtracts the modules the eval harness cannot reach. The subtraction is the
part that rots: a module that acquires a job on the eval path stays excluded,
and the sweep silently stops covering it. So the closure is recomputed here
from the imports themselves and compared to the filter.

A text scan rather than a YAML parse, matching
``tests/test_conformance.py``'s reasoning: PyYAML is not a declared dependency
of this project, and the ``paths:`` block is a flat list of quoted strings that
does not need one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "stride_service"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "evals-live.yml"
PACKAGE_GLOB = "src/stride_service/**"

#: Every workflow whose ``pull_request`` trigger is path-filtered to the agentic
#: surface. Both name the same text trees, and both went wrong the same way, so
#: the lint below reads them as a list rather than naming one.
FILTERED_WORKFLOWS = (
    WORKFLOW,
    REPO_ROOT / ".github" / "workflows" / "provider-smoke.yml",
)


def _package_modules() -> set[str]:
    return {path.stem for path in PACKAGE.glob("*.py")}


def _imported_modules(source: Path, modules: set[str]) -> set[str]:
    """Which package modules ``source`` imports, however it spells the import."""
    found = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("stride_service"):
                head = module.split(".")[1:2]
                found |= set(head) & modules
                if not head:
                    found |= {alias.name for alias in node.names} & modules
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found |= set(alias.name.split(".")[1:2]) & modules
    return found


def _reachable_from_evals() -> set[str]:
    """Every package module the eval harness reaches, directly or transitively."""
    modules = _package_modules()
    pending = set()
    for source in (REPO_ROOT / "evals").rglob("*.py"):
        pending |= _imported_modules(source, modules)

    reached: set[str] = set()
    while pending:
        module = pending.pop()
        reached.add(module)
        pending |= _imported_modules(PACKAGE / f"{module}.py", modules) - reached
    return reached


def _filter_paths(workflow: Path = WORKFLOW) -> list[str]:
    """The ``paths:`` entries of the ``pull_request`` trigger, in order."""
    entries = []
    in_block = False
    for line in workflow.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "paths:":
            in_block = True
        elif in_block and stripped.startswith('- "'):
            entries.append(stripped.removeprefix('- "').removesuffix('"'))
        elif in_block and stripped and not stripped.startswith("#"):
            break
    return entries


def test_the_filter_sweeps_the_package_before_subtracting_from_it():
    """A negation only means anything after the glob it carves out of."""
    entries = _filter_paths()
    assert PACKAGE_GLOB in entries, (
        f"the filter no longer names {PACKAGE_GLOB!r}. Listing the package's "
        f"files individually is what this lint exists to prevent — the list "
        f"goes short whenever a responsibility moves to a new module."
    )
    excluded = [entry for entry in entries if entry.startswith("!")]
    assert all(entries.index(entry) > entries.index(PACKAGE_GLOB) for entry in excluded)


def test_the_excluded_modules_are_exactly_those_evals_cannot_reach():
    """The subtraction, recomputed rather than trusted."""
    unreachable = _package_modules() - _reachable_from_evals() - {"__init__"}
    excluded = {Path(entry).stem for entry in _filter_paths() if entry.startswith("!")}

    assert excluded == unreachable, (
        f"the live sweep's path filter and evals/'s import closure disagree. "
        f"Excluded but reachable (a change here can change an eval outcome and "
        f"would not fire the sweep): {sorted(excluded - unreachable)}. "
        f"Unreachable but swept (harmless, but the exclusion list is stale): "
        f"{sorted(unreachable - excluded)}."
    )


def test_the_closure_is_not_vacuously_empty():
    """Guards the guard: an import walk that finds nothing would agree with anything."""
    reachable = _reachable_from_evals()
    assert {"graph", "report", "sampling", "grounding"} <= reachable


@pytest.mark.parametrize("workflow", FILTERED_WORKFLOWS, ids=lambda path: path.name)
def test_every_swept_path_still_exists(workflow):
    """A filter naming a directory the repo no longer has sweeps nothing.

    **This is what the frameworks cutover left behind.** Both filters named
    ``skills/**`` until #280. That tree became ``frameworks/<name>/lanes/`` and
    ``domains/`` (ADR 0011), so every lane skill, exemplar file, output
    contract, critic text, severity rubric, note and case stopped matching any
    path — the text an agent actually reasons from, on a lane whose entire job
    is to check what happens when that text changes.

    Nothing was missed, because no commit has yet touched those trees without
    also touching ``src/``, which matched on its own. A glob that resolves to
    nothing does not fail; it quietly narrows what fires, and it stays quiet
    until the one PR that needed it.

    The negations are not checked. ``!src/stride_service/token_caps.py``
    subtracts a real file today, and the test above already holds the exclusion
    list to ``evals/``'s import closure.
    """
    missing = [
        entry
        for entry in _filter_paths(workflow)
        if not entry.startswith("!") and not any(REPO_ROOT.glob(entry))
    ]

    assert not missing, (
        f"{workflow.name} filters on paths that match nothing: {missing}. "
        f"A glob over a tree that moved silently narrows what the sweep fires on."
    )


CONTRIBUTION = REPO_ROOT / ".github" / "workflows" / "contribution.yml"


def test_the_contribution_filter_covers_every_submission_kind():
    """The author bindings must fire on every kind, including a future one.

    ``contribution.yml`` is path-filtered, so a kind whose tree no filter
    entry covers is a submission whose author nobody checks — and it fails
    the way an unfiltered path always fails, by being silently absent rather
    than red. So the filter is held to :data:`~evals.harness.submit.KINDS`
    itself: add a kind, and this fails until the workflow sees it.
    """
    from evals.harness.submit import KINDS

    entries = _filter_paths(CONTRIBUTION)
    uncovered = sorted(
        f"{name} ({kind.prefix})"
        for name, kind in KINDS.items()
        if not any(
            kind.prefix.startswith(entry.removesuffix("**").removesuffix("/") + "/")
            for entry in entries
        )
    )
    assert not uncovered, (
        f"{CONTRIBUTION.name}'s paths filter covers no tree for: {uncovered}."
        " A submission kind the filter misses is one whose author binding"
        " never runs."
    )


def test_the_contribution_filter_covers_the_roster():
    """The roster carries standing, and a self-raise is dangerous alone."""
    entries = _filter_paths(CONTRIBUTION)
    roster = "evals/review/voters.toml"
    assert any(
        roster.startswith(entry.removesuffix("**").removesuffix("/") + "/")
        for entry in entries
    ), f"{CONTRIBUTION.name} does not fire on {roster}"
