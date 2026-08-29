"""Embed :class:`Engine` in your own application — route step 5.

Run it against the sample source::

    uv run python examples/embed.py

This is the shape every caller should copy, and it is deliberately **not** split
into a "simple" snippet plus a separate error-handling one. ``analyze`` has three
outcomes and a correct caller handles all three; a snippet that tests only for
:class:`PipelineCompleted` and silently does nothing on rejection is the bug this
file exists to stop being copied.

``main`` takes the engine rather than building one, which is what makes the
example's own logic testable without credentials — ``tests/test_examples.py``
calls it with a stub runner and walks all three branches. Building the engine is
the ``__main__`` block's job, so the file still runs standalone.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from analysis_service import (
    Engine,
    EngineInputError,
    PipelineCompleted,
    PipelineRejected,
    Report,
    Source,
)

SAMPLE = Path(__file__).resolve().parent / "orders.md"


# docs-region: embed
async def main(engine: Engine) -> None:
    """Analyze one system, handling every outcome the run can have."""
    # A job takes an ordered list of sources. One written description is the
    # simplest case; add Source.transcript(...) for a recorded call, and give
    # each a label you will recognise when you read it back in the report.
    sources = [
        Source.description(SAMPLE.read_text(encoding="utf-8"), label="Orders note"),
    ]

    try:
        outcome = await engine.analyze(sources, system_name="Orders")
    except EngineInputError as exc:
        # Raised before any model runs: no sources, too many, more bytes than
        # the deployment allows, or an over-long system_name. Your caller's
        # mistake, not the service's — surface it as a validation error.
        print(f"invalid submission: {exc}", file=sys.stderr)
        raise
    except Exception:
        # An internal failure: a model error that exhausted its retries, or a
        # fail-closed check tripping. Nothing partial comes back — the engine
        # never returns a best-effort report. Log it, surface a generic error.
        print("analysis failed", file=sys.stderr)
        raise

    if isinstance(outcome, PipelineRejected):
        # The sources could not be turned into a valid system model. This is
        # actionable by whoever wrote them: each issue names what to fix.
        for issue in outcome.issues:
            print(f"rejected [{issue.code}] {issue.message}", file=sys.stderr)
        return

    assert isinstance(outcome, PipelineCompleted)
    summarise(outcome.report)


# docs-region-end: embed


def summarise(report: Report) -> None:
    """Print the headline numbers. Your application does something useful here."""
    print(f"system:   {report.input.system_name}")
    print(f"elements: {report.elements_analyzed}")
    # One block per framework the job selected, in that order. A consumer that
    # knows only the neutral shape reads exactly these fields; a package's own
    # block carries more, and `getattr` is how you ask without assuming.
    for block in report.analyses:
        summary = block.summary
        print(
            f"{block.framework}: {summary.claim_count} claims"
            f" ({summary.needs_info_count} need info,"
            f" {summary.rejected_count} rejected)"
        )
        for level, count in sorted(getattr(summary, "by_severity", {}).items()):
            print(f"  {level}: {count}")


if __name__ == "__main__":
    # A selection is required: this service ships no default set, so an
    # embedder names the frameworks it wants analysed under.
    asyncio.run(main(Engine.from_config(["stride"])))
