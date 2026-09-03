"""The live sweep's path filter, kept honest against what ``evals/`` imports.

``.github/workflows/evals-live.yml`` sweeps ``src/analysis_service/**`` and
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
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "analysis_service"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "evals-live.yml"
PACKAGE_GLOB = "src/analysis_service/**"

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
            if module.startswith("analysis_service"):
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

    The negations are not checked. ``!src/analysis_service/token_caps.py``
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


# --------------------------------------------------------------------------
# The credential boundary (#508)
#
# No credential-bearing job may execute mutable PR-head code. The rule below
# reads *every* workflow rather than the two that hold credentials today, so a
# workflow added later is covered without anyone remembering to add it here —
# the same reason the path-filter lints above recompute a closure instead of
# checking a list.

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: A trigger that runs code from a ref a contributor controls.
UNTRUSTED_TRIGGERS = frozenset({"pull_request", "pull_request_target"})

#: The condition a credential-bearing job carries so that `workflow_dispatch`,
#: which can name any ref, cannot reach unreviewed code.
TRUSTED_REF_GUARD = "if: github.ref == 'refs/heads/main'"

#: Grants that make a job worth attacking. ``secrets.GITHUB_TOKEN`` is not one:
#: it is scoped by the workflow's own ``permissions:`` block and every job gets
#: one whether it asks or not.
_OIDC_GRANT = "id-token: write"
#: Both ways a workflow names a secret. ``secrets.NAME`` is the common one and
#: ``secrets[expr]`` is how a matrix leg reads its own, which is the form a
#: workflow reaches for precisely when it is being careful -- so a scan that saw
#: only the first went blind on the file that had just been tightened.
_SECRET_REFERENCES = ("secrets.", "secrets[")


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml"))


def _code_lines(workflow: Path) -> list[str]:
    """The workflow's lines with whole-line comments dropped.

    Whole-line only, deliberately. These files argue with themselves at length
    about which job may hold which credential, so a scan that counted prose
    would find `id-token` in a paragraph explaining why a job must not have one.
    A trailing comment cannot introduce either marker below, because both are
    spelled the same way in prose and in code and prose about them lives on its
    own lines.
    """
    return [
        line
        for line in workflow.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def _triggers(workflow: Path) -> set[str]:
    """The top-level keys of the workflow's ``on:`` block."""
    found = set()
    in_block = False
    for line in _code_lines(workflow):
        if line.startswith("on:"):
            in_block = True
        elif in_block and line.startswith("  ") and line.strip().endswith(":"):
            if not line.startswith("    "):
                found.add(line.strip().removesuffix(":"))
        elif in_block and line and not line.startswith(" "):
            break
    return found


def _credential_grants(workflow: Path) -> set[str]:
    """Which credential a workflow hands a job, if any."""
    grants = set()
    for line in _code_lines(workflow):
        if _OIDC_GRANT in line:
            grants.add(_OIDC_GRANT)
        names_a_secret = any(form in line for form in _SECRET_REFERENCES)
        if names_a_secret and "secrets.GITHUB_TOKEN" not in line:
            grants.add(line.strip())
    return grants


def _credential_bearing() -> list[Path]:
    return [path for path in _workflows() if _credential_grants(path)]


@pytest.mark.parametrize("workflow", _workflows(), ids=lambda path: path.name)
def test_no_credential_bearing_workflow_runs_on_a_contributor_ref(workflow):
    """The invariant #508 was filed for, checked against every workflow.

    ``pull_request`` skips forks, which is what made it look sufficient. It does
    not skip a collaborator: someone who can push a branch and open a pull
    request can edit the application code a live lane imports, or edit the
    workflow file itself, and have the edit execute while the job holds an OIDC
    identity or a provider key. Repository-scoped federation cannot tell that
    token from one minted on main, because the repository claim is the same.

    ``pull_request_target`` is worse and is prohibited repository-wide; it is
    named here so the prohibition is enforced rather than remembered.
    """
    grants = _credential_grants(workflow)
    if not grants:
        return
    reachable = _triggers(workflow) & UNTRUSTED_TRIGGERS
    assert not reachable, (
        f"{workflow.name} grants {sorted(grants)} and triggers on "
        f"{sorted(reachable)}. A job holding a credential must run only code "
        f"that has already passed review: use `push` on the trusted branch, or "
        f"`workflow_dispatch` with the ref guard, and leave the pull request to "
        f"ci.yml's offline suite."
    )


@pytest.mark.parametrize("workflow", _credential_bearing(), ids=lambda path: path.name)
def test_every_credential_bearing_job_carries_the_ref_guard(workflow):
    """`workflow_dispatch` names its own ref, so the trigger list is not enough.

    Anyone with write access can dispatch a workflow against an unreviewed
    branch. The guard is written on the job rather than inferred from the
    triggers so that adding a trigger cannot quietly widen what runs with the
    job's identity.
    """
    assert TRUSTED_REF_GUARD in workflow.read_text(encoding="utf-8"), (
        f"{workflow.name} hands a job a credential but carries no "
        f"`{TRUSTED_REF_GUARD}`. Without it a workflow_dispatch against any "
        f"branch runs unreviewed code with that credential."
    )


def test_the_credential_scan_is_not_vacuously_empty():
    """Guards the guard: a scan that finds nothing would pass on anything."""
    bearing = {path.name for path in _credential_bearing()}
    assert {
        "evals-live.yml",
        "evals-live-api-key.yml",
        "provider-smoke.yml",
    } <= bearing, f"the credential scan stopped seeing known live lanes: {bearing}"


def test_the_offline_suite_still_covers_pull_requests():
    """The other half of the trade, and it is what makes the first half safe.

    Moving the live lanes to `push` only holds if a pull request still gets
    provider-adapter coverage. It does: `ci.yml` runs the conformance suite for
    every vendor on every pull request, and it needs no credentials at all.
    """
    ci = WORKFLOW_DIR / "ci.yml"
    assert "pull_request" in _triggers(ci)
    assert not _credential_grants(ci), (
        "ci.yml acquired a credential. It is the lane that runs on pull "
        "requests, so it is the one lane that must never hold one."
    )


SETUP_SCRIPT = REPO_ROOT / ".github" / "scripts" / "setup-workload-identity.sh"


def test_the_federation_pins_the_ref_claim_and_not_only_a_suffix():
    """Layer 2 has to fail for a different reason than layer 1 does.

    The condition read `job_workflow_ref.endsWith('@refs/heads/main')`, and the
    `@` in that claim separates a prefix from a ref the attacker names. Git
    accepts a branch called `pwn@refs/heads/main`, whose claim ends
    `@refs/heads/pwn@refs/heads/main` and satisfies the suffix. Layer 1 is a
    line in a file that branch can edit, so one push carried both.

    `ref` is the run's ref exactly, with no attacker-chosen tail.
    """
    condition = next(
        line
        for line in SETUP_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.startswith("ATTRIBUTE_CONDITION=")
    )

    assert "assertion.ref == '${TRUSTED_REF}'" in condition, (
        "the ref claim must be compared, not matched as a suffix of another claim"
    )


def test_a_branch_name_cannot_satisfy_the_federation_condition():
    """The exploit input, asked of the shipped condition rather than of a memo.

    `git check-ref-format` decides what a legal branch is, so the test asks it
    rather than asserting what it believes.
    """
    hostile = "pwn@refs/heads/main"
    legal = (
        subprocess.run(
            ["git", "check-ref-format", f"refs/heads/{hostile}"], check=False
        ).returncode
        == 0
    )
    assert legal, "the premise: git accepts this branch name"

    trusted = "refs/heads/main"
    claim = f"owner/repo/.github/workflows/x.yml@refs/heads/{hostile}"

    assert claim.endswith(f"@{trusted}"), "the suffix check passes, which is the bug"
    assert f"refs/heads/{hostile}" != trusted, (
        "the ref check refuses it, which is the fix"
    )
