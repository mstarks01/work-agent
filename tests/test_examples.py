"""Layer 2 of the examples' honesty mechanism: their logic actually runs.

The docs lint next door proves the prose shows *these* files. It cannot prove
the files are correct — a snippet that imports cleanly and mishandles a
rejection passes byte-equality perfectly happily — a quick start that tests
``isinstance(outcome, PipelineCompleted)`` and silently does nothing otherwise
is exactly the shape this catches.

So each example exposes ``async def main(engine)`` rather than building its own
engine, and this drives that seam with stub runners across all three outcomes.
No credentials, no models, no cost — which is what lets it run on every pull
request instead of weekly.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from stride_service import (
    PipelineRejected,
    SourceLimits,
    StrideEngine,
    StubPipelineRunner,
)
from stride_service.validation import ValidationIssue

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

# Every runnable example, by module name. sync_docs.py is tooling, not an
# example, and carries no main(engine) contract.
EXAMPLE_NAMES = ("embed", "embed_sync")


def load(name: str):
    """Import an example by path — examples/ is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RejectingRunner:
    """A runner whose input never survives the validity gate."""

    async def run(self, job, on_node):
        await on_node("extract")
        return PipelineRejected(
            issues=[
                ValidationIssue(
                    code="too-many-elements",
                    message="181 elements exceeds the 150 cap",
                )
            ]
        )


class ExplodingRunner:
    """A runner that fails internally, the way an exhausted retry would."""

    async def run(self, job, on_node):
        raise RuntimeError("the model gave up")


@pytest.fixture(params=EXAMPLE_NAMES)
def example(request):
    return load(request.param)


def test_every_example_exposes_the_injected_engine_contract(example):
    """``main(engine)`` is what makes the example testable without credentials."""
    assert asyncio.iscoroutinefunction(example.main)


# The shipped bounds; the examples are about the call shape, not the caps.
EXAMPLE_LIMITS = SourceLimits(max_total_bytes=100 * 1024, max_sources=10)

# Ample: no test here is exercising the deadline, and a tight one would make
# an unrelated slow run flake. The bound itself is covered in test_engine.py.
TEST_DEADLINE = 30.0


def test_the_example_reports_a_completed_run(example, capsys):
    engine = StrideEngine(
        StubPipelineRunner(), limits=EXAMPLE_LIMITS, deadline_seconds=TEST_DEADLINE
    )
    asyncio.run(example.main(engine))
    assert capsys.readouterr().out, "a completed run must print something"


def test_the_example_handles_a_rejection_without_raising(example, capsys):
    """The bug this whole mechanism exists to catch: silence on rejection."""
    engine = StrideEngine(
        RejectingRunner(), limits=EXAMPLE_LIMITS, deadline_seconds=TEST_DEADLINE
    )
    asyncio.run(example.main(engine))

    captured = capsys.readouterr()
    reported = captured.out + captured.err
    assert "too-many-elements" in reported, (
        "a rejection must be surfaced, not silently swallowed — the caller can "
        "act on it"
    )


def test_the_example_lets_an_internal_failure_propagate(example):
    """Fail closed: nothing partial is invented on the way out."""
    engine = StrideEngine(
        ExplodingRunner(), limits=EXAMPLE_LIMITS, deadline_seconds=TEST_DEADLINE
    )
    with pytest.raises(RuntimeError):
        asyncio.run(example.main(engine))


def test_the_sample_description_is_in_the_band_the_docs_promise():
    """``examples/orders.md`` is sized for the 8-20 element range.

    Element count needs a model, so it cannot be asserted here — the weekly live
    lane does that. What is decidable offline is the word budget the sample was
    written to, which is what keeps it from drifting into either a one-liner or
    a wall of text.
    """
    words = len((EXAMPLES / "orders.md").read_text(encoding="utf-8").split())
    assert 150 <= words <= 250, f"sample is {words} words, outside the 150-250 band"


def test_no_example_teaches_a_stub_runner():
    """#26 kept ``StubPipelineRunner`` an internal testing seam.

    It is fine here in ``tests/``. An ``examples/`` file injecting one would
    document a credential-free way to run the service through the side door,
    which is the ruling #26 made and #30 declined to reopen.
    """
    for name in EXAMPLE_NAMES:
        source = (EXAMPLES / f"{name}.py").read_text(encoding="utf-8")
        assert "StubPipelineRunner" not in source, name
