"""Call the engine from synchronous code — ``analyze_sync``.

Run it against the sample source::

    uv run python examples/embed_sync.py

Use this shape when you are slotting the engine behind an existing *synchronous*
interface and cannot make the call site ``async``. It is the same three-outcome
contract as ``embed.py``; only the call changes.

**The failure worth knowing about**: ``analyze_sync`` refuses to run inside an
already-running event loop. It cannot work there — it wraps ``asyncio.run``,
which raises on a running loop — so it raises a ``RuntimeError`` saying so
instead of deadlocking. That means the one place this goes wrong is the place
people most often try it: a sync helper called from async code, where it looks
fine until the first request. If you are anywhere inside an event loop, use
``embed.py``'s ``await engine.analyze(...)`` instead.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from stride_service import (
    EngineInputError,
    PipelineCompleted,
    PipelineRejected,
    Source,
    StrideEngine,
)

SAMPLE = Path(__file__).resolve().parent / "orders.md"


# docs-region: embed_sync
def analyze_orders(engine: StrideEngine) -> None:
    """The synchronous call, with the same three outcomes as the async one."""
    sources = [
        Source.description(SAMPLE.read_text(encoding="utf-8"), label="Orders note"),
    ]

    try:
        outcome = engine.analyze_sync(sources, system_name="Orders")
    except RuntimeError as exc:
        # analyze_sync was called from inside a running event loop. Await
        # engine.analyze(...) there instead — see examples/embed.py.
        print(f"wrong call for this context: {exc}", file=sys.stderr)
        raise
    except EngineInputError as exc:
        print(f"invalid submission: {exc}", file=sys.stderr)
        raise

    if isinstance(outcome, PipelineRejected):
        for issue in outcome.issues:
            print(f"rejected [{issue.code}] {issue.message}", file=sys.stderr)
        return

    assert isinstance(outcome, PipelineCompleted)
    print(f"{outcome.report.summary.threat_count} threats")


# docs-region-end: embed_sync


async def main(engine: StrideEngine) -> None:
    """The ``main(engine)`` contract every example follows, for the offline test.

    A blocking call reached from async code belongs on a worker thread, which
    is also the honest way to run this one: ``asyncio.to_thread`` gives it a
    thread with no running loop, so ``analyze_sync`` works there. Calling it
    directly from here would hit the ``RuntimeError`` above — that path is
    covered in ``tests/test_examples.py`` rather than demonstrated here, since
    the failure is the thing to avoid, not the thing to copy.
    """
    await asyncio.to_thread(analyze_orders, engine)


if __name__ == "__main__":
    analyze_orders(StrideEngine.from_config())
