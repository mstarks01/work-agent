"""Backward-compatible launcher for the reviewer-facing work review app.

The public command remains ``uv run python webapp/sitting.py`` while the
reviewer-facing implementation lives in :mod:`webapp.work_review`. Internal
``sitting`` names remain compatibility identifiers for the existing evaluation
and contribution contracts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

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

# The legacy base page used reviewer/submitting-account placeholders in visible
# copy. The work-review surface deliberately removes those identities until the
# optional contribution step, while the base renderer still supplies the old
# values. Filter only placeholders the work-review template no longer carries;
# the renderer remains strict for every placeholder the template does declare.
_base_render = _impl.base.render


def _render_work_review(template: str, grants: Any, **values: Any) -> str:
    if template == _impl._PAGE:
        values = {
            name: value
            for name, value in values.items()
            if f"<!--{name}-->" in template
        }
    return _base_render(template, grants, **values)


_impl.base.render = _render_work_review


def create_app(session: Session):
    return _impl.create_app(session)


def main(argv: list[str] | None = None) -> int:
    # Preserve the old module's test/embedding seam: callers that override the
    # launcher root still affect the implementation behind the shim.
    _impl.REPO_ROOT = REPO_ROOT
    _impl.base.REPO_ROOT = REPO_ROOT
    return _impl.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
