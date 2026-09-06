"""Offline baseline for the deterministic layer, as issue #627 asked for.

**What this is for.** Three merged changes carry timings in their commit
messages and in docstrings beside the code. This is the harness those figures
came from, so the next person can re-derive them rather than trust them.

**It measures one tree.** A before/after is two runs: check out the earlier
commit, run this, check out the later one, run it again, compare. The script
cannot do that for you, because the code it measures is the code it imports.

**No model runs and no credential is read.** Sources are the corpus's own text
plus a seeded synthetic generator; model output is synthesised. That is
deliberate, and it is what makes a deterministic benchmark repeatable: an LLM's
latency would swamp every figure here and cost money to collect.

Every timing is :func:`time.thread_time`, which is the clock the bounds in
:mod:`analysis_service.grounding` are written against — node bodies run on a
worker pool, and wall-clock on one of eight threads measures the other seven.

**What it does not cover**, so nobody reads more into a green run than is there:
worker-queue wait, report serialization, and the per-framework split between a
STRIDE-only, an ASVS-only and a combined job. Those are named in #627's
measurement section and are not measured here.

Run it::

    uv run python -m evals.bench.deterministic              # every case
    uv run python -m evals.bench.deterministic repair       # one case
    uv run python -m evals.bench.deterministic --spans out.json

``--spans`` writes every repaired span the corpus sources produce, as JSON, for
diffing between two trees. That is the check #627 asks for by name: a
timing-bounded scan given back time can inspect more candidates and land on a
different span, so a change that means to be behaviour-preserving has to show
the file is identical rather than assume it.
"""

from __future__ import annotations

import argparse
import functools
import json
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path

from analysis_service import evidence
from analysis_service.critic import (
    _bound_element_references,
    _verify_quotes,
    duplicate_groups,
)
from analysis_service.grounding import PreparedSource, prepare_source
from analysis_service.report import Ground
from analysis_service.system_model import ModelIndex, SystemModel

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "evals" / "corpus"

# The synthetic source's vocabulary. A small closed set on purpose: windows that
# share the quote's character multiset are what defeat `quick_ratio`'s prune, so
# this shape exercises the repair rung's expensive path rather than its cheap
# one. Ordinary English prunes almost every window and measures almost nothing.
_VOCABULARY = (
    "the service writes every order to the accounts database over a shared"
    " connection and that account has full read write on table orders and on"
    " table customers with no separate role for reporting queries"
)


def synthetic_source(word_count: int, seed: int = 7) -> str:
    """A seeded source of ``word_count`` words. Same seed, same bytes."""
    rng = random.Random(seed)
    words = _VOCABULARY.split()
    return " ".join(rng.choice(words) for _ in range(word_count))


def corpus_sources() -> dict[str, str]:
    """Every corpus case's submitted text, by case id."""
    return {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in sorted(CORPUS.glob("*/source.md"))
    }


def _draft(claim_id: str, **overrides):
    """One draft, from the shared test factories.

    Which package's record type carries it does not matter here. Everything
    measured below reads a claim's grounds and its element references, and every
    package's record carries both — so these figures are one package's only in
    the sense that a fixture has to build something concrete.

    Imported late so this module loads without the tests on the path.
    """
    from tests.factories import sample_draft

    return sample_draft(claim_id, **overrides)


def dropped_char(span: str) -> str:
    """A real span with one character gone.

    The shape a model produces when it tidies while quoting: the ladder refuses
    it and the rung finds the span it came from, so this is what exercises a
    repair that succeeds.
    """
    return span[:8] + span[9:]


def trailing_junk(span: str) -> str:
    """A real span with a word appended that the source does not contain.

    One word wider than the window it was cut from, so the scan walks the whole
    ``_REPAIR_WIDTH_SLACK`` range rather than settling in the first. **This is
    the shape the timings recorded in PR #628 were taken against**, and the
    timing case below uses it for that reason: a benchmark whose numbers cannot
    be compared with the ones already written down is a new baseline pretending
    to be the old one.
    """
    return f"{span} zzzqqq"


def _quote_claims(
    source: str,
    count: int,
    width: int,
    label: str,
    mangle: Callable[[str], str],
) -> list:
    """``count`` claims whose quotes the ladder refuses, each cut from ``source``.

    ``mangle`` decides how a real span stops being verbatim. It is a parameter
    rather than a constant because the two shapes measure different things and
    both are used here.
    """
    words = source.split()
    return [
        _draft(
            f"S-{index:03d}",
            grounds=[
                Ground(
                    kind="quote",
                    text=mangle(" ".join(words[index * width : index * width + width])),
                    source_label=label,
                )
            ],
        )
        for index in range(count)
    ]


def _timed(label: str, call: Callable[[], object], repeats: int = 1) -> None:
    start = time.thread_time()
    for _ in range(repeats):
        call()
    elapsed = time.thread_time() - start
    per = f"  ({elapsed / repeats * 1000:.3f} ms each)" if repeats > 1 else ""
    print(f"  {label:<54} {elapsed:8.3f}s{per}")


# --- Cases -------------------------------------------------------------------


def case_repair() -> None:
    """The fuzzy repair rung, which dominates a node body that repairs at all.

    Recorded in PR #628: 2.590 s -> 2.428 s at 4,000 words and 12.972 s ->
    12.142 s at 20,000, for 40 refused quotes against one source. The scan
    dominates both, so the saving is the per-source folding the change removed.
    """
    for words in (4_000, 20_000):
        source = synthetic_source(words)
        claims = _quote_claims(
            source, count=40, width=6, label="description", mangle=trailing_junk
        )
        _timed(
            f"_verify_quotes: 40 refused quotes, {words:,}-word source",
            functools.partial(_verify_quotes, claims, {"description": source}),
        )


def case_spent_deadline() -> None:
    """A body past its deadline, through the path the fan-in actually runs.

    The fan-in never calls ``repair_quote``; it prepares each source itself.
    Recorded in PR #628: 0.227 s on main, 0.228 s after the first commit — which
    is what said the guard was on the wrong entry point — and 0.069 s after the
    second. What is left is the exact-verification fold, which #627 puts out of
    scope.
    """
    from analysis_service import critic

    sources = {f"src-{n}": synthetic_source(20_000, seed=n) for n in range(10)}
    claims = [
        _draft(
            f"S-{index:02d}",
            grounds=[Ground(kind="quote", text="zzz qqq not here", source_label=label)],
        )
        for index, label in enumerate(sources)
    ]
    spent = critic.repair_deadline
    critic.repair_deadline = lambda: time.thread_time() - 1.0
    try:
        _timed(
            "_verify_quotes: spent deadline, 10 x 20,000-word sources",
            lambda: _verify_quotes(claims, sources),
        )
    finally:
        critic.repair_deadline = spent


def case_prepare() -> None:
    """One source fold, which is what the body-level cache pays once instead of
    once per refused quote."""
    for words in (4_000, 20_000):
        source = synthetic_source(words)
        _timed(
            f"prepare_source: {words:,}-word source",
            functools.partial(prepare_source, source),
            repeats=20,
        )


def case_retention() -> None:
    """What the prepared-source cache holds, recorded in PR #630 as 18x the
    source bytes and flat across three orders of magnitude."""
    for words in (1_000, 10_000, 50_000):
        source = synthetic_source(words)
        held = _deep_size(prepare_source(source))
        source_bytes = len(source.encode())
        print(
            f"  {f'PreparedSource: {words:,} words':<54}"
            f" {held / 1024:8.1f} KiB   {held / source_bytes:.1f}x source"
        )


def _deep_size(obj: object, seen: set[int] | None = None) -> int:
    """Bytes ``obj`` holds, following the tuples that carry the words.

    ``sys.getsizeof`` on a tuple counts its pointers and not the strings they
    point at, which for this value is almost all of the answer.
    """
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    total = sys.getsizeof(obj)
    if isinstance(obj, PreparedSource):
        total += _deep_size(obj.words, seen) + _deep_size(obj.folded, seen)
    elif isinstance(obj, tuple):
        total += sum(_deep_size(item, seen) for item in obj)
    return total


def wide_model(processes: int = 200, flows: int = 400) -> SystemModel:
    """A model whose flow count is what the pre-index reach scan walked per
    cited place. The corpus models are too small to show it."""
    from tests.factories import valid_model

    element = valid_model().processes[0].model_dump(mode="json")
    flow = valid_model().data_flows[0].model_dump(mode="json")
    return SystemModel.model_validate(
        {
            "processes": [
                {**element, "id": f"process:svc-{i:03d}", "name": f"svc {i:03d}"}
                for i in range(processes)
            ],
            "data_flows": [
                {
                    **flow,
                    "id": f"flow:call-{i:03d}",
                    "name": f"call {i:03d}",
                    "source": f"process:svc-{i % processes:03d}",
                    "destination": f"process:svc-{(i + 1) % processes:03d}",
                }
                for i in range(flows)
            ],
        }
    )


def case_index() -> None:
    """The model lookups the critic repeats per claim.

    Recorded in PR #628: 0.104 s -> 0.003 s for 2,000 claims on a model of 200
    processes and 400 flows.
    """
    model = wide_model()
    index = ModelIndex.of(model)
    claims = [
        _draft(
            f"S-{index_:04d}",
            grounds=[
                Ground(
                    kind="unknown-attribute",
                    element_id=f"process:svc-{index_ % 200:03d}",
                    attribute="authentication",
                )
            ],
            affected_element_ids=[f"process:svc-{index_ % 200:03d}"],
        )
        for index_ in range(2_000)
    ]
    _timed("ModelIndex.of: 200 processes, 400 flows", lambda: ModelIndex.of(model), 100)
    _timed(
        "_bound_element_references: 2,000 claims",
        lambda: _bound_element_references(claims, index),
    )
    _timed(
        "duplicate_groups: 2,000 claims",
        lambda: duplicate_groups(claims, model),
    )


def case_catalog() -> None:
    """The evidence catalog, derived once in ``prepare`` and again per framework
    merge. #627 allowed that to be deferred with a measurement, and this is it:
    recorded as 0.048 ms corpus-sized and 3.7 ms on 600 elements."""
    from tests.factories import valid_model

    for label, model in (
        ("corpus-sized", valid_model()),
        ("600-element", wide_model()),
    ):
        _timed(
            f"evidence_catalog: {label} model",
            functools.partial(evidence.evidence_catalog, model),
            repeats=200,
        )


def repaired_spans() -> list[list]:
    """Every repaired span the corpus sources produce, as comparable rows.

    One row per claim: the source, the claim id, the span the rung handed back
    (or ``UNVERIFIED``) and the similarity that earned it. Two trees producing
    an identical file agree about every repair.
    """
    rows: list[list] = []
    sources = dict(corpus_sources())
    sources["synthetic-4000"] = synthetic_source(4_000)
    for label, text in sources.items():
        claims = _quote_claims(
            text, count=10, width=9, label=label, mangle=dropped_char
        )
        checked = _verify_quotes(claims, {label: text})
        rows += [
            [label, repair.claim_id, repair.written, repair.similarity]
            for repair in checked.repaired
        ]
        rows += [
            [label, mark.claim_id, "UNVERIFIED", None] for mark in checked.unverified
        ]
        rows += [
            [label, dropped.claim_id, "DROPPED", None] for dropped in checked.groundless
        ]
    return sorted(rows, key=lambda row: (row[0], row[1]))


#: Every case, by the name the command line takes. A table rather than a
#: dispatch chain, so a case added here is runnable without a second edit.
CASES: dict[str, Callable[[], None]] = {
    "repair": case_repair,
    "spent-deadline": case_spent_deadline,
    "prepare": case_prepare,
    "retention": case_retention,
    "index": case_index,
    "catalog": case_catalog,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "case", nargs="?", choices=sorted(CASES), help="one case; default is all"
    )
    parser.add_argument(
        "--spans",
        type=Path,
        help="write the repaired spans to this JSON file and run no timings",
    )
    args = parser.parse_args(argv)

    if args.spans is not None:
        rows = repaired_spans()
        args.spans.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"{len(rows)} rows -> {args.spans}")
        return 0

    for name in [args.case] if args.case else sorted(CASES):
        print(f"\n{name}")
        CASES[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
