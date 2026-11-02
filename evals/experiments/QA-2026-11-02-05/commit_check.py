"""Hold the commit E4's run records name against the audit's revision.

E4's calls ran on a commit of this audit's branch, which the squash merge
leaves out of ``main``. This script digests every tracked file under
``read_capabilities.REQUEST_ROOTS`` and under ``WIDER``, at both commits, and
writes the digests and whether they are equal into ``runs/commit-check.json``.
It also writes whether the digests that ``capture_turn.py`` wrote into
``read_capabilities.REQUEST_CHECK`` under ``REQUEST_ROOTS`` equal the
revision's.

Usage, from the repository root, while both commits are reachable:

    commit_check.py <revision> <run commit>
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from read_capabilities import REQUEST_CHECK, REQUEST_ROOTS, tree_digests

#: The package, configuration and domain trees, beside the request roots.
WIDER = ("frameworks", "config", "domains")
OUT = Path(__file__).parent / "runs" / "commit-check.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def digests(commit: str, roots: tuple[str, ...]) -> dict[str, str]:
    """Each tracked file under ``roots`` at ``commit``, with its digest."""
    paths = _git("ls-tree", "-r", "-z", "--name-only", commit, "--", *roots)
    return tree_digests(
        paths.split("\0"), lambda path: _git("show", f"{commit}:{path}")
    )


def main(revision: str, run_commit: str) -> None:
    roots = (*REQUEST_ROOTS, *WIDER)
    at_revision, at_run = digests(revision, roots), digests(run_commit, roots)
    checked = json.loads(REQUEST_CHECK.read_text())["inputs"]
    OUT.write_text(
        json.dumps(
            {
                "revision": revision,
                "run_commit": run_commit,
                "roots": list(roots),
                "files": len(at_revision),
                "equal": at_revision == at_run,
                "differ": sorted(
                    path
                    for path in at_revision.keys() | at_run.keys()
                    if at_revision.get(path) != at_run.get(path)
                ),
                "request_check_equal": {
                    path: digest
                    for path, digest in checked.items()
                    if path.split("/")[0] in REQUEST_ROOTS
                }
                == digests(revision, REQUEST_ROOTS),
            },
            indent=1,
        )
        + "\n"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
