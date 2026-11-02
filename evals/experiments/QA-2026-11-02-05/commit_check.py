"""Hold the commit E4's run records name against the audit's revision.

E4's calls ran on a commit of this audit's branch, which the squash merge
leaves out of ``main``. This script digests every tracked file under
``read_capabilities.REQUEST_ROOTS`` and under ``WIDER``, at both commits, and
writes the digests and whether they are equal into ``runs/commit-check.json``.

Usage, from the repository root, while both commits are reachable:

    commit_check.py <revision> <run commit>
"""

import json
import subprocess
import sys
from pathlib import Path

from analysis_service.sources import text_digest

sys.path.insert(0, str(Path(__file__).parent))
from read_capabilities import REQUEST_ROOTS

#: The package, configuration and domain trees, beside the request roots.
WIDER = ("frameworks", "config", "domains")
OUT = Path(__file__).parent / "runs" / "commit-check.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def digests(commit: str) -> dict[str, str]:
    """Each tracked file under the roots at ``commit``, with its digest."""
    paths = _git(
        "ls-tree", "-r", "-z", "--name-only", commit, "--", *REQUEST_ROOTS, *WIDER
    )
    return {
        path: text_digest(_git("show", f"{commit}:{path}"))
        for path in paths.split("\0")
        if path
    }


def main(revision: str, run_commit: str) -> None:
    at_revision, at_run = digests(revision), digests(run_commit)
    OUT.write_text(
        json.dumps(
            {
                "revision": revision,
                "run_commit": run_commit,
                "roots": [*REQUEST_ROOTS, *WIDER],
                "files": len(at_revision),
                "equal": at_revision == at_run,
                "differ": sorted(
                    path
                    for path in at_revision.keys() | at_run.keys()
                    if at_revision.get(path) != at_run.get(path)
                ),
            },
            indent=1,
        )
        + "\n"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
