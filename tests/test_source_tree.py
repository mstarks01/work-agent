"""The one enumerator every lint walks the tree with, held to the other gates.

Three tools decide which files in this checkout get judged, and they decide it
separately: ``ruff`` respects ``.gitignore``, ``mypy`` reads its own ``exclude``,
and the lints in this suite read :mod:`tests.source_tree`. One rule, three
readers, so the tests below check them against each other rather than each
against its own expectation.

The effect of a disagreement is concrete: ``evals/runs`` holds run artifacts the
repository does not track, a live run leaves its probe scripts beside them, and
a reader that judges that directory fails on a file the tree does not hold
(PR #944).

The named file sets are held the same way: each one against a source that no
lint reads, such as ``git ls-files`` and the code paths of
``.github/scripts/five_class_read.py``, or against the rule its comment states.
"""

from __future__ import annotations

import ast
import subprocess
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

from tests import source_tree
from tests.source_tree import (
    _SKIPPED,
    _SKIPPED_PATHS,
    CONFIG,
    HOOKS,
    LINT_MARKER,
    MODEL_TEXT,
    NOT_GUIDES,
    PROMPTS,
    PYTHON,
    REPO_ROOT,
    SHORTEST_REASON,
    TESTS,
    WORKFLOWS,
    guide_files,
    lint_modules,
    parse,
    prompt_files,
    source_files,
    without,
)
from tests.test_five_class_read import check

pytestmark = pytest.mark.lint


def test_no_skipped_path_is_one_git_tracks():
    """A skipped directory holds nothing the repository keeps.

    The other direction of the rule: skipping is for what the checkout
    accumulates, never for source the tree owns. Committing a file under one of
    these paths takes it out of every lint in silence, so this is what refuses
    it.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z", *_SKIPPED_PATHS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = [name for name in tracked.split("\0") if name]

    assert not found, (
        f"these tracked files sit under a path no lint reads: {found}. Either the"
        f" files belong somewhere the lints reach, or {_SKIPPED_PATHS} is wrong."
    )


def test_mypy_excludes_every_path_the_lints_skip():
    """``mypy`` walks the same directories, so it needs the same answer.

    It is the push gate, and its own ``files`` names ``evals``, so a path this
    module skips and ``mypy`` does not is a file that fails a push while no lint
    reads it.
    """
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mypy = manifest["tool"]["mypy"]

    missing = [
        path
        for path in _SKIPPED_PATHS
        if path.split("/")[0] in mypy["files"] and f"^{path}/" not in mypy["exclude"]
    ]

    assert not missing, (
        f"tests/source_tree.py skips {missing} and pyproject.toml's [tool.mypy]"
        " exclude does not, so the push gate judges a file no lint reads."
    )


def test_the_enumerator_reads_the_source_it_is_asked_for():
    """A positive control: the skip rules do not empty the scan."""
    found = {path.name for path in source_files("src")}

    assert {"fan_in.py", "claims.py", "graph.py"} <= found


def _tree(root: Path, *names: str) -> None:
    """Write an empty file at each repository-relative name under ``root``."""
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).touch()


def test_a_checkout_under_a_skipped_name_still_reads_its_source(tmp_path, monkeypatch):
    """A skip name is matched inside the repository, never in the folders above it."""
    root = tmp_path / "research" / ".venv" / "checkout"
    _tree(root, "src/kept.py")
    monkeypatch.setattr(source_tree, "REPO_ROOT", root)

    assert source_files("src") == [root / "src/kept.py"]


def test_no_walk_reads_the_run_archive_or_a_nested_worktree(tmp_path, monkeypatch):
    """A worktree under ``.claude/worktrees`` is a whole checkout at another
    revision, and ``evals/runs`` is untracked output. The skills beside them are
    tracked prose and stay in."""
    _tree(
        tmp_path,
        "src/kept.py",
        ".claude/skills/audit/SKILL.md",
        ".claude/worktrees/other/src/kept.py",
        ".claude/worktrees/other/README.md",
        "evals/runs/job/probe.py",
        "evals/runs/job/notes.md",
        "evals/harness/run.py",
    )
    monkeypatch.setattr(source_tree, "REPO_ROOT", tmp_path)

    found = {
        path.relative_to(tmp_path).as_posix()
        for path in source_files(".", suffixes=(".py", ".md"))
    }

    assert found == {
        "src/kept.py",
        ".claude/skills/audit/SKILL.md",
        "evals/harness/run.py",
    }


def test_every_directory_gitignore_names_is_skipped():
    """``.gitignore`` lists what the checkout accumulates, so every walk skips it.

    A directory there and not here is a directory a lint reads in one checkout
    and not in CI's: a nested worktree, the run archive, a coverage report.
    """
    patterns = [
        line.strip().strip("/")
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip().endswith("/") and not line.startswith("#")
    ]

    missing = [
        pattern
        for pattern in patterns
        if pattern not in (_SKIPPED_PATHS if "/" in pattern else _SKIPPED)
    ]

    assert not missing, (
        f".gitignore names {missing} and tests/source_tree.py walks into it."
    )


def _tracked(*patterns: str) -> list[Path]:
    """Every file git tracks that matches one of ``patterns``."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", *patterns],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / name for name in listed.split("\0") if name]


def _relative(paths) -> set[str]:
    return {path.relative_to(REPO_ROOT).as_posix() for path in paths}


def test_every_tracked_python_file_is_in_a_named_set():
    """The Python the project runs and the tests hold every tracked Python file.

    The frozen research probes are the one exception, and the walk skips them by
    name. A tracked file outside both sets is a file every Python lint misses.
    """
    named = _relative(source_files(*PYTHON, *TESTS))
    missing = sorted(
        name
        for name in _relative(_tracked("*.py"))
        if name not in named and "research" not in name.split("/")
    )

    assert not missing, (
        f"no named set holds {missing}. Add the root to PYTHON or TESTS in"
        " tests/source_tree.py."
    )


def test_the_python_set_holds_every_python_code_path():
    """The CI check's code paths and the PYTHON set name the same Python.

    ``five_class_read.CODE_PATHS`` decides which change needs a five-class
    read, and nothing in the lints reads it. A Python file on a code path
    that PYTHON leaves out is code a reviewer must read and no lint reads.
    """
    named = _relative(source_files(*PYTHON))
    on_a_code_path = {
        name
        for name in _relative(_tracked("*.py"))
        if check.changes_code([name]) and not fnmatchcase(name, "tests/*")
    }

    assert on_a_code_path, "no tracked Python file is on a code path"
    assert sorted(on_a_code_path - named) == []


def test_each_named_root_holds_a_tracked_file():
    """A root that holds nothing is a set that reads less than its name says."""
    empty = [
        root
        for root in (*PYTHON, *TESTS, *CONFIG, *WORKFLOWS, *HOOKS, *MODEL_TEXT)
        if not _tracked(root)
    ]

    assert not empty


def test_the_python_set_holds_the_eval_scripts_examples_and_ci_script():
    """A positive control: the scripts at the top of ``evals/``, the examples and
    the CI script are in the set the one-reader lints walk."""
    named = _relative(source_files(*PYTHON))

    assert {
        "evals/verify_corpus.py",
        "examples/embed.py",
        ".github/scripts/five_class_read.py",
    } <= named


def test_every_tracked_markdown_file_is_a_guide_or_says_why_not():
    """A Markdown file is a guide unless :data:`NOT_GUIDES` names it."""
    guides = _relative(guide_files())
    missing = sorted(
        name
        for name in _relative(_tracked("*.md"))
        if name not in guides
        and source_tree._under(REPO_ROOT / name, NOT_GUIDES) is None
    )

    assert not missing, f"these are neither guides nor in NOT_GUIDES: {missing}"


@pytest.mark.parametrize("place", sorted(NOT_GUIDES))
def test_each_entry_that_is_not_a_guide_is_live_and_says_why(place):
    assert _tracked(place), f"{place} holds no tracked file, so drop it"
    assert len(NOT_GUIDES[place]) >= 40


@pytest.mark.parametrize("pattern", PROMPTS)
def test_each_prompt_pattern_matches_a_text_a_model_reads(pattern):
    """A pattern that matches nothing is a prompt the set lost in silence."""
    matched = _relative(REPO_ROOT.glob(pattern))
    model_text = _relative(source_files(*MODEL_TEXT, suffixes=(".md",)))

    assert matched
    assert matched <= model_text
    assert matched <= _relative(prompt_files())


def _defined(tree: ast.Module) -> set[str]:
    """Every public name a module defines at its top level."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {target.id for target in targets if isinstance(target, ast.Name)}
    return {name for name in names if not name.startswith("_")}


#: Every name :mod:`tests.source_tree` defines but ``REPO_ROOT``: each one reads
#: the tree or names a part of it.
_TREE_READERS = frozenset(_defined(parse(Path(source_tree.__file__)))) - {"REPO_ROOT"}


def _reads_the_tree(tree: ast.Module) -> bool:
    """Whether a module imports a file set or the walk from the source tree."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "tests.source_tree"
        and any(alias.name in _TREE_READERS for alias in node.names)
        or isinstance(node, ast.ImportFrom)
        and node.module == "tests"
        and any(alias.name == "source_tree" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_every_module_that_reads_the_tree_declares_itself_a_lint():
    """The lint set is declared by a marker, and held to what a module reads.

    A test module that takes a file set or the walk from
    :mod:`tests.source_tree` asserts a property of the repository's own files,
    so it is a lint. The marker, not the file name, puts it in
    :func:`~tests.source_tree.lint_modules`.
    """
    lints = set(lint_modules())
    undeclared = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in source_files(*TESTS)
        if _reads_the_tree(parse(path)) and path not in lints
    )

    assert not undeclared, (
        f"{undeclared} read the tree and do not declare `pytestmark = {LINT_MARKER}`."
    )


#: The named sets of roots a lint may pass to the walk.
_ROOT_SETS = frozenset(
    {"PYTHON", "TESTS", "CONFIG", "WORKFLOWS", "HOOKS", "MODEL_TEXT", "WHOLE_TREE"}
)

#: The modules that may pass the walk a root of their own: the walk itself, and
#: this module, whose tests build a tree of their own to walk.
_THE_WALK = frozenset({"tests/source_tree.py", "tests/test_source_tree.py"})

#: The walks in tests/ over a tree that holds no source file, keyed
#: ``path:scope``, with the reason. Every other test reads a named set through
#: :mod:`tests.source_tree`.
DATA_WALKS: dict[str, str] = {
    "tests/test_framework_units.py:<module>": (
        "replays the archived reports of the Baselines, which are run data"
    ),
    **dict.fromkeys(
        (
            "tests/test_asvs.py:TestNothingIsRuledOutByVocabulary.test_no_corpus_model_loses_a_requirement_to_code",
            "tests/test_compact.py:<module>",
            "tests/test_corpus_lints.py:TestTheTwoReadersOfTheIdRule.test_both_readers_choose_one_builder_for_every_blessed_element",
            "tests/test_early_questions.py:<module>",
            "tests/test_early_questions.py:TestTheFrameworkGates.test_a_framework_whose_precondition_is_total_asks_no_gate_question",
            "tests/test_evals_alignment.py:case",
            "tests/test_evals_flow_ids.py:<module>",
            "tests/test_evals_modes.py:TestTheHarnessSeedsFrameworkOptions.test_every_declared_framework_gets_an_entry",
            "tests/test_evals_modes.py:TestTheHarnessSeedsFrameworkOptions.test_the_options_satisfy_every_packages_own_model",
            "tests/test_evals_modes.py:TestTheInitiatorReadingOfAnExtraction.test_every_corpus_case_has_an_initiator_to_measure",
            "tests/test_evals_reference.py:_copy_case",
            "tests/test_evals_replay.py:TestAnUnsignedReferenceGradesNothing.test_a_case_without_a_facts_file_is_none",
            "tests/test_evals_replay.py:TestAnUnsignedReferenceGradesNothing.test_an_unsigned_row_withholds_the_catalog",
            "tests/test_evals_replay.py:TestEveryReferenceRowTakesOneFate.test_an_unsigned_alias_rewrites_nothing",
            "tests/test_frameworks.py:_undecidable_models",
            "tests/test_license_lints.py:test_every_corpus_case_from_an_external_source_names_its_license",
            "tests/test_rule_coverage.py:TestEveryCrossingKeyedRuleNamesItsInference.leads",
            "tests/test_rule_coverage.py:models",
            "tests/test_sitting.py:TestASittingReadsEverySource.test_the_reading_document_pins_the_files_the_app_pins",
            "tests/test_sitting_app.py:build_tree",
            "tests/test_stride_lanes_of_verb.py:test_no_blessed_reference_is_misfiled_by_the_table",
            "tests/test_webapp_questions.py:test_the_page_opens_the_list_with_the_questions_the_service_counts",
        ),
        "reads the eval corpus, which holds case data and no source file",
    ),
    **dict.fromkeys(
        (
            "tests/test_corpus_diagrams.py:test_each_diagram_is_what_the_generator_writes",
            "tests/test_sources.py:TestTheDiagramType.test_each_corpus_diagram_passes",
        ),
        "reads the corpus diagrams that a generator writes, which are case data",
    ),
    **dict.fromkeys(
        (
            "tests/test_browser.py:test_the_diagram_s_flows_and_labels_stay_clear_on_every_archived_model",
            "tests/test_early_question_tripwires.py:<module>",
            "tests/test_evals_baseline.py:test_every_submitter_has_a_roster_line",
            "tests/test_evals_baseline.py:test_the_repo_wide_walk_holds_every_merged_baseline",
            "tests/test_evals_early_answerers.py:<module>",
            "tests/test_evals_early_policies.py:<module>",
            "tests/test_evals_schema_paths.py:<module>",
            "tests/test_fixes.py:<module>",
            "tests/test_open_facts.py:<module>",
            "tests/test_published_holdout_split.py:_sealed",
        ),
        "reads the archived reports of a sealed Baseline, which are run data",
    ),
    **dict.fromkeys(
        (
            "tests/test_evals_flow_ids.py:TestTheArchiveKeepsItsOwnVersion",
            "tests/test_evals_replay.py:<module>",
            "tests/test_evals_replay.py:TestTheArchivedEmissionsReplay.test_every_artifact_carries_its_emissions_beside_it",
            "tests/test_evals_replay.py:TestTheArchivedEmissionsReplay.test_every_extraction_emission_replays",
        ),
        "reads the archived emissions of past runs, which are run data",
    ),
    **dict.fromkeys(
        (
            "tests/test_capability_contrast.py:<module>",
            "tests/test_evals_rule_applicability.py:test_no_fixture_here_is_signed_by_its_drafter",
            "tests/test_evals_withheld.py:test_every_answers_file_applies_once_signed",
            "tests/test_exemplar_fan_in.py:test_every_exemplar_system_has_a_fixture",
        ),
        "reads a fixture set of the tests, which holds data and no source file",
    ),
    **dict.fromkeys(
        (
            "tests/test_evals_history.py:test_every_map_is_two_full_ids_a_line",
            "tests/test_ledger_text_lints.py:test_no_experiment_row_lost_an_amount_to_the_shell",
        ),
        "reads records that the repository keeps, such as ledger rows, and no source file",
    ),
    **dict.fromkeys(
        (
            "tests/test_browser.py:test_the_offline_sitting_page_shows_a_restored_mark_keyed_by_an_alias",
            "tests/test_envelope.py:TestTheRoundTripWritesOneSubmission.test_it_writes_nothing_into_the_case_directory",
            "tests/test_envelope.py:TestTheRoundTripWritesOneSubmission.test_the_file_is_named_for_its_own_bytes",
            "tests/test_envelope.py:submitted",
            "tests/test_envelope.py:written",
            "tests/test_evals_baseline.py:TestAssembleAndVerify.test_a_refused_later_sweep_copies_nothing",
            "tests/test_evals_baseline.py:TestAssembleAndVerify.test_an_edited_report_moves_a_digest",
            "tests/test_evals_leakage.py:test_the_corpus_lint_reports_a_planted_leak",
            "tests/test_evals_run_reports.py:test_a_case_that_died_leaves_no_report",
            "tests/test_evals_run_reports.py:test_every_finished_case_keeps_a_report",
            "tests/test_evals_score.py:TestItRefusesRatherThanScoringHalf.test_an_artifact_with_no_reports_directory_is_refused",
            "tests/test_evals_submit.py:TestTheCommand.test_a_baseline_runs_end_to_end",
            "tests/test_object_stores.py:TestTheFilesystemFollowsNoLink.files",
            "tests/test_offline_sitting.py:TestThePageCarriesTheWholeMethod.test_every_case_rides_in_one_file",
            "tests/test_settings_env.py:TestTheWriter.test_the_first_write_makes_a_private_directory",
            "tests/test_settings_save.py:TestASaveThatPasses.test_the_first_save_makes_the_directory_and_the_file_private",
            "tests/test_sitting.py:snapshot",
            "tests/test_sitting_app.py:TestDroppingACase.snapshot",
            "tests/test_sitting_app.py:TestTheDraftStore.test_two_tabs_saving_one_draft_lose_no_save",
            "tests/test_sitting_app.py:TestTheRail.test_a_drifted_digest_puts_the_case_back_on_the_rail",
            "tests/test_sitting_app.py:TestTheRailAndTheGateAgree.test_neither_end_reads_the_case_directory",
            "tests/test_sitting_app.py:TestTheWalkStaysBlind.claims",
            "tests/test_sitting_app.py:store_bytes",
        ),
        "walks a tree that the test builds under its temporary directory",
    ),
    "tests/test_asvs.py:test_the_package_root_carries_no_stray_markdown": "reads the package root the way the package gate reads it when it loads a package, so the test and the gate see one tree",
    "tests/test_model_gate.py:test_every_local_only_switch_litellm_defines_is_set": "reads the source of the installed litellm package, which the repository does not hold",
}


def _own_roots(tree: ast.Module) -> list[int]:
    """Each line that calls the walk with a root other than a named set."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        in {"source_files", "held_files"}
        and not all(
            isinstance(argument, ast.Starred)
            and isinstance(argument.value, ast.Name)
            and argument.value.id in _ROOT_SETS
            for argument in node.args
        )
    )


def test_every_walk_reads_a_named_set():
    """No lint states its own roots: each one names the set it reads.

    A lint that passes the walk its own roots is a second reader of "which
    files count", and the narrowest copy wins with no warning. A lint that
    reads less than a set says so through :func:`~tests.source_tree.without`.
    """
    found = sorted(
        f"{relative}:{line}"
        for path in source_files(*TESTS)
        if (relative := path.relative_to(REPO_ROOT).as_posix()) not in _THE_WALK
        for line in _own_roots(parse(path))
    )

    assert not found, (
        f"these walk roots of their own: {found}. Read a named set from"
        " tests/source_tree.py, and leave out a part of it with `without`."
    )


#: The calls that list a directory: :class:`~pathlib.Path`'s ``glob``,
#: ``rglob``, ``iterdir`` and ``walk``, ``os.walk``, and ``glob.glob`` and
#: ``glob.iglob``.
_WALKS = frozenset({"glob", "rglob", "iterdir", "walk", "iglob"})

#: The modules a bare walk name comes from, for ``from os import walk``.
_WALK_MODULES = frozenset({"os", "glob"})


def _scope_names(tree: ast.Module) -> dict[ast.AST, str]:
    """Each node of ``tree`` keyed to the qualified name of the innermost
    function or class that holds it, or ``<module>``."""
    names: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
            else:
                inner = scope
            names[child] = inner
            visit(child, inner)

    visit(tree, "<module>")
    return names


def _walks(tree: ast.Module) -> list[tuple[str, int]]:
    """Each call in ``tree`` that lists a directory itself, as its scope and line.

    ``ast.walk`` walks a syntax tree, not a directory, so it is no walk. A
    receiver that names ``tmp_path`` walks the tree that the test builds.
    """
    bare = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in _WALK_MODULES
        for alias in node.names
        if alias.name in _WALKS
    }
    scopes = _scope_names(tree)
    return sorted(
        (scopes[node], node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in _WALKS
            and not (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "ast"
            )
            and not any(
                isinstance(name, ast.Name) and name.id == "tmp_path"
                for name in ast.walk(node.func.value)
            )
            or isinstance(node.func, ast.Name)
            and node.func.id in bare
        )
    )


def _own_walks() -> dict[str, list[int]]:
    """Each test scope that lists a directory itself, keyed ``path:scope``."""
    found: dict[str, list[int]] = {}
    for path in source_files(*TESTS):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _THE_WALK:
            continue
        for scope, line in _walks(parse(path)):
            found.setdefault(f"{relative}:{scope}", []).append(line)
    return found


def test_no_test_lists_a_source_tree_itself():
    """A test that lists a directory itself is a second reader of "which files
    count", and it reads what a checkout accumulates beside what the
    repository holds. A walk over a data tree, such as the eval corpus or a
    fixture set, chooses no source file, so it is declared in
    :data:`DATA_WALKS` with the reason.
    """
    found = _own_walks()
    undeclared = sorted(
        f"{key}:{line}"
        for key, lines in found.items()
        if key not in DATA_WALKS
        for line in lines
    )
    stale = sorted(set(DATA_WALKS) - set(found))

    assert not undeclared, (
        f"these list a directory themselves: {undeclared}. Read a named set"
        " from tests/source_tree.py, or declare a walk over a data tree in"
        " DATA_WALKS with the reason."
    )
    assert not stale, f"{stale} walk nothing, so drop them from DATA_WALKS"


@pytest.mark.parametrize("key", sorted(DATA_WALKS))
def test_each_data_walk_says_why(key):
    assert len(DATA_WALKS[key]) >= SHORTEST_REASON


def test_the_walk_lint_sees_each_walk_of_its_own():
    """A positive control on :func:`_walks`."""
    tree = ast.parse(
        "import os\n"
        "from glob import iglob\n"
        "def read():\n"
        "    ROOT.rglob('*.py')\n"
        "    os.walk(ROOT)\n"
        "    iglob('src/*')\n"
        "    ast.walk(tree)\n"
        "def build(tmp_path):\n"
        "    (tmp_path / 'a').iterdir()\n"
        "class Page:\n"
        "    SCRIPTS = STATIC.glob('*.js')\n"
    )

    assert _walks(tree) == [("Page", 11), ("read", 4), ("read", 5), ("read", 6)]


def test_the_walk_lint_sees_a_root_of_its_own():
    """A positive control on the lint above."""
    tree = ast.parse('source_files("src")\nsource_files(*PYTHON, *TESTS)\n')

    assert _own_roots(tree) == [1]


def test_a_subset_names_what_it_leaves_out_and_why():
    files = source_files(*PYTHON)
    reason = "a reason long enough to say what a reader can check here"

    kept = without(files, {"examples": reason})

    assert kept and not any(
        path.is_relative_to(REPO_ROOT / "examples") for path in kept
    )
    with pytest.raises(ValueError, match="no file"):
        without(files, {"no-such-tree": reason})
    with pytest.raises(ValueError, match="reason"):
        without(files, {"examples": "short"})
