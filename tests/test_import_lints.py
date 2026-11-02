"""Which modules may import a module, read over the source tree.

Each check reads the named set :data:`tests.source_tree.PYTHON`, so a module
added under a subpackage is read like a module at the top of the package.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions

import pytest

from analysis_service.frameworks import PACKAGES
from tests.source_tree import PYTHON, REPO_ROOT, parse, source_files, without

pytestmark = pytest.mark.lint

#: The parts of :data:`~tests.source_tree.PYTHON` that the wheel does not ship.
NOT_THE_WHEEL: dict[str, str] = dict.fromkeys(
    ("webapp", "evals", "examples", ".github/scripts"),
    "the wheel ships only src/analysis_service, so its dependencies declare"
    " only what that package imports",
)

#: The parts of :data:`~tests.source_tree.PYTHON` that are no code path of a
#: job, with the reason.
NOT_A_JOB: dict[str, str] = {
    "evals": (
        "an eval tool counts what the rules fire to measure a job, and writes"
        " no finding of the job it measures"
    ),
}


def _importers(needle: str, left_out: dict[str, str]) -> set[str]:
    """Each file of the set, less ``left_out``, whose text holds ``needle``,
    relative to the root."""
    return {
        path.relative_to(REPO_ROOT).as_posix()
        for path in without(source_files(*PYTHON), left_out)
        if needle in path.read_text(encoding="utf-8")
    }


def test_every_distribution_the_package_imports_is_declared():
    """A dependency you rely on but do not declare is one a bump can remove.

    `pyproject.toml` records this case for `cryptography`. `anyio`,
    `starlette` and `google-genai` are each imported by name, so each is
    declared rather than left to arrive transitively.

    The import name is not the distribution name -- `jwt` is PyJWT, `google` is
    three separate distributions -- so this asks `packages_distributions()`
    rather than guessing, and accepts a top-level name when ANY distribution
    providing it is declared.
    """
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    # An extra declares its packages too: the `sql` store imports SQLAlchemy
    # only where a deployment installs the `sql` extra (ADR 0023).
    extras = project["project"]["optional-dependencies"].values()

    def normalized(name: str) -> str:
        """A distribution name as PEP 503 compares it: `typing-extensions`
        and `typing_extensions` are one name."""
        return re.sub(r"[-_.]+", "-", name).lower()

    declared = {
        normalized(re.split(r"[<>=\[]", spec)[0].strip())
        for spec in [
            *project["project"]["dependencies"],
            *(spec for extra in extras for spec in extra),
        ]
    }
    provides = packages_distributions()

    imported: set[str] = set()
    for source in without(source_files(*PYTHON), NOT_THE_WHEEL):
        for node in ast.walk(parse(source)):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])

    assert "litellm" in imported, "the import scan found nothing; it is broken"
    assert "litellm" in declared, "the dependency read found nothing; it is broken"

    undeclared = sorted(
        name
        for name in imported - sys.stdlib_module_names - {"analysis_service"}
        if name in provides
        and not any(normalized(dist) in declared for dist in provides[name])
    )

    assert not undeclared, (
        f"imported but not declared in pyproject.toml: {undeclared}"
        " — a transitive bump can remove these from under the code that calls them"
    )


def test_no_document_is_reachable_from_the_evidence_seam():
    """Retrieval feeds the prompt and stops there.

    The importers of ``knowledge`` are the graph (which composes the prompt
    blocks) and nothing else — in particular not ``evidence.py``, which
    builds the closed set of citable facts, and not ``critic.py``, which
    resolves what a finding rests on. There is no code path by which a
    retrieved document could become a Ground.
    """
    assert _importers("from analysis_service.knowledge import", {}) == {
        "src/analysis_service/graph.py"
    }


def test_nothing_outside_the_prompt_seam_reads_a_candidate():
    """Candidates reach the agents and stop there.

    The importers of ``candidates`` are the seam that renders the prompt
    (``graph``), the fan-in that regenerates the same candidates for the
    coverage account (``fan_in``), the accounting itself (``coverage``), the
    ranking of the questions a paused job asks (``early_questions``), which
    orders questions and writes no finding, and the rule definitions: the
    framework registry and each package's rules module, which build a
    candidate's type and never read one. Neither the critic nor the report can
    see one, so no code path exists that turns a fired rule into a threat.
    """
    package = "src/analysis_service"
    assert _importers("from analysis_service.candidates import", NOT_A_JOB) == {
        f"{package}/coverage.py",
        f"{package}/early_questions.py",
        f"{package}/fan_in.py",
        f"{package}/graph.py",
        f"{package}/frameworks/__init__.py",
        *(f"{package}/frameworks/{name}/rules.py" for name in PACKAGES),
    }


def test_nothing_turns_the_context_into_evidence():
    """A pack informed the analysis; it did not ground anything.

    The report's own importers are what keep that true — ``AnalysisContext``
    is built in the graph and read by nobody, so there is no path from a pack
    name or a rule ID to a ``Ground``.
    """
    assert _importers("AnalysisContext", {}) == {
        "src/analysis_service/graph.py",
        "src/analysis_service/report.py",
    }
