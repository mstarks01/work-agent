"""Backward-compatible launcher for the reviewer-facing work review app.

The public command remains ``uv run python webapp/sitting.py`` while the
reviewer-facing implementation lives in :mod:`webapp.work_review`. Internal
``sitting`` names remain compatibility identifiers for the existing evaluation
and contribution contracts.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webapp import work_review as _impl

sittings = _impl.sittings
submit_spine = _impl.submit_spine
Session = _impl.Session
Line = _impl.Line
MIN_OWN_LIST = _impl.MIN_OWN_LIST
build_session = _impl.build_session
REPO_ROOT = _impl.REPO_ROOT
HOST = _impl.HOST
PORT = _impl.PORT
HELD = _impl.HELD
LOCAL_SUBMITTER = _impl.LOCAL_SUBMITTER
_PAGE = _impl._PAGE
_open = _impl._open
create_app = _impl.create_app
main = _impl.main


if __name__ == "__main__":
    raise SystemExit(main())
