"""The one enumerator every lint walks the tree with, held to the other gates.

Three tools decide which files in this checkout get judged, and they decide it
separately: ``ruff`` respects ``.gitignore``, ``mypy`` reads its own ``exclude``,
and the lints in this suite read :mod:`tests.source_tree`. One rule, three
readers, so the tests below check them against each other rather than each
against its own expectation.

The cost of a disagreement is concrete: ``evals/runs`` holds run artifacts the
repository does not track, a live run leaves its probe scripts beside them, and
a reader that judges that directory fails on a file the tree does not hold
(#942).
"""

from __future__ import annotations

import subprocess
import tomllib

from tests.source_tree import _SKIPPED_PATHS, REPO_ROOT, source_files


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
    excluded = manifest["tool"]["mypy"]["exclude"]

    missing = [path for path in _SKIPPED_PATHS if f"^{path}/" not in excluded]

    assert not missing, (
        f"tests/source_tree.py skips {missing} and pyproject.toml's [tool.mypy]"
        " exclude does not, so the push gate judges a file no lint reads."
    )


def test_the_enumerator_reads_the_source_it_is_asked_for():
    """A positive control: the skip rules do not empty the scan."""
    found = {path.name for path in source_files("src")}

    assert {"fan_in.py", "claims.py", "graph.py"} <= found
