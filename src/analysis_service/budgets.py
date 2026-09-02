"""Per-subject and global consumption budgets over a rolling window.

The concurrency ceiling bounds how many jobs a caller may run **at once**. It is
self-clearing by design — finishing a job buys the next one — which is exactly
why it bounds no spend at all: a caller who submits serially, letting each job
finish before sending the next, stays inside the ceiling forever while running
an unbounded number of paid jobs. That is the unbounded-consumption half of
OWASP LLM10, and ADR 0007 named it as the integrator's to close. This module is
where the service closes it instead.

**Two bounds, one window.** A subject gets a job count and a token budget per
rolling window; the deployment gets a token budget of its own across every
subject. The count is what stops a burst of trivial submissions; the token
budget is what stops one large submission doing the same damage in a single job.
Neither substitutes for the other, and the global one is not the sum of the
per-subject ones — it is the provider quota's share, which a deployment with ten
subjects has to divide rather than multiply.

**Tokens, not dollars, and the difference is not a shortcut.** A price is vendor
data with an expiry date. `evals/` carries a price table because a sweep reports
what it *spent*, after the fact, and can be re-priced when the table moves; a
gate that refuses a submission cannot be re-priced after it has refused one.
Pinning prices in the service would put a number that drifts into a decision
that is final. Tokens are the thing this service can count exactly, and a
deployment that wants a dollar bound converts once, at the knob, where it can
see the rate it used. The provider's own spend limit is the backstop behind
both, and `docs/Configuration.md` says so.

**The estimate is deliberately coarse and deliberately high.** :func:`estimate`
multiplies the submission's own token count by every LLM call the selection
implies, which over-counts: not every call carries the whole input, and a repair
node often does not run. Over-counting is the right direction for a bound that
must hold before anything is spent — the alternative is a gate that admits a job
it cannot afford and discovers this at node nine. The reservation is replaced by
the measured usage the moment the job reaches a terminal state, so a window's
accounting converges on what actually happened rather than on what was feared.

**A job nothing measured keeps its reservation, and that is the direction that
matters.** A completed run settles from its report and a rejected one from the
nodes that ran before the validity gate refused their output. A job that failed
mid-graph returned no measurement — and it is the one case where getting this
wrong is unbounded: it had already paid for every node it reached, so freeing
its estimate would make this whole module a bound that any failing job clears,
and a caller whose submissions outrun the deadline would spend without limit
while their window read empty.

**Reconciliation is a scan, not a ledger.** :func:`spent_tokens` reads the
records: a terminal job contributes what it measured, a live one — or one
nothing measured — contributes what it reserved. That is the same choice the concurrency count makes and for the same
reason — a maintained counter is a second copy of the truth, and the path that
forgets to decrement leaks budget until the process restarts. It also makes
double-credit and negative-credit unrepresentable rather than merely tested for,
because nothing is ever added to or subtracted from anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.frameworks import PACKAGES
from analysis_service.markdown_loader import estimate_tokens
from analysis_service.report import FrameworkSelection, TokenUsage
from analysis_service.sources import Source

# Calls every job makes whatever it selects: one extraction, and the repair that
# may follow it. Counted whether or not repair fires, because a bound has to
# hold for the job that needs it.
_SHARED_LLM_CALLS = 2

# Calls one framework adds beyond its lanes: its critic and the bounded re-ask.
_PER_FRAMEWORK_REVIEW_CALLS = 2


def llm_calls_for(frameworks: Sequence[FrameworkSelection]) -> int:
    """How many LLM calls a selection implies, at most.

    Derived from ``PACKAGES`` rather than written down, for the reason
    :func:`~analysis_service.frameworks.widest_fan_out` exists: a lane count in
    prose went stale in four modules when ASVS took the fan-out from 6 to 23. A
    package registered tomorrow moves this number with no edit here.
    """
    return _SHARED_LLM_CALLS + sum(
        len(PACKAGES[selection.name].lanes) + _PER_FRAMEWORK_REVIEW_CALLS
        for selection in frameworks
    )


def estimate(
    sources: Iterable[Source], frameworks: Sequence[FrameworkSelection]
) -> int:
    """The tokens a submission reserves before anything is spent.

    The submission's own size times every call the selection implies. It ignores
    the instruction text each node also carries, which pulls the estimate down,
    and assumes every call sees the whole input, which pulls it up much harder —
    so it over-counts, on purpose. See the module docstring.
    """
    submitted = sum(estimate_tokens(source.text) for source in sources)
    return submitted * llm_calls_for(frameworks)


class BudgetPolicy(BaseModel):
    """What one deployment allows per rolling window.

    ``window_seconds`` is the width of the window every bound here is measured
    over. It is rolling rather than aligned to a clock boundary: a fixed hourly
    window lets a caller spend a full allowance at 10:59 and another at 11:00,
    which is the burst the bound exists to stop.

    Every value is required and none may be zero. A budget nobody has chosen is
    the state this module exists to end, and a deployment that admits no jobs
    should not be running — the same argument ``max_active_jobs`` already makes
    from both directions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    window_seconds: int = Field(gt=0)
    max_jobs_per_window: int = Field(ge=1)
    max_tokens_per_window: int = Field(ge=1)
    #: Across every subject, not the sum of their allowances. This is the
    #: provider quota's share, which a deployment with ten subjects divides
    #: rather than multiplies.
    global_max_tokens_per_window: int = Field(ge=1)

    def window_start(self, now: datetime | None = None) -> datetime:
        """The oldest instant still inside the window."""
        return (now or datetime.now(UTC)) - timedelta(seconds=self.window_seconds)


def spent_tokens(charges: Iterable[tuple[int, int | None]]) -> int:
    """What a set of jobs has committed: measured where known, reserved where not.

    Each entry is one job's ``(reserved, measured)``. A terminal job contributes
    what it measured — the reconciliation — and a live one contributes what it
    reserved. Nothing accumulates, so a window's total cannot double-count a job
    or fall below zero however a job ended.
    """
    return sum(
        reserved if measured is None else measured for reserved, measured in charges
    )


def measured_tokens(usages: Iterable[TokenUsage | None]) -> int:
    """What a finished job's node runs actually cost, or 0 if nothing metered.

    Zero and not ``None`` for an unmetered run, because this is the number that
    *replaces* a reservation: leaving the reservation standing would charge a
    caller for a job whose cost nobody knows, and forever, since a terminal job
    never reports again. Under-charging a run the provider declined to meter is
    the safer error, and the unmetered call is visible in the report either way.

    Typed against :class:`~analysis_service.report.TokenUsage` rather than read
    off any object that happens to carry the attribute. A ``getattr`` with a
    zero default answers 0 for a usage record whose field was renamed, which
    silently undercharges every window and no type checker can see — the number
    that decides a budget must not have a quiet default behind it.
    """
    return sum(usage.total_tokens for usage in usages if usage is not None)
