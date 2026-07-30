"""Guard the sample payload embedded in ``docs/example-report.html`` (#34).

``docs/example-report.html`` is the route's payoff: the report a first-run reader
opens at step 4. Its viewer chrome is fine and stays untouched (#29) — what rots
is the ``<script type="application/json" id="report">`` block, which is a
checked-in artifact no test has ever read.

These checks exist because the block is **not** covered by #30's docs lint. That
tool is a ``--write``/``--check`` pair over one-way includes; this artifact has no
offline writer, because regenerating it needs live models. A check with no writer
is a test, so it lives here — in the credential-free lane that runs on every PR.

**Regenerating the payload** is route steps 3-4, run by a maintainer who has
provider credentials configured:

1. Start the web app: ``uv run python webapp/main.py``.
2. Click **Load example** — it loads ``examples/orders.md``.
3. Analyze, and wait for the run to finish.
4. Save the run's report JSON into the ``<script id="report">`` block below,
   replacing it wholesale. Nothing else in the file changes.

Timings, job id, and fingerprints differ on every regeneration; that is expected
and nothing here may pin them. This procedure belongs in ``docs/Web-App.md``
once that page exists (#31) — it is written here meanwhile so a failure is
self-routing rather than a puzzle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from stride_service.model_tiers import LLM_NODES, TIER_NAMES
from stride_service.report import SCHEMA_VERSION, StrideReport

EXAMPLE_REPORT = Path(__file__).resolve().parents[1] / "docs" / "example-report.html"

# The viewer parses this same block at docs/example-report.html:230. Matching it
# by id rather than by position keeps the test indifferent to the chrome moving.
_PAYLOAD_BLOCK = re.compile(
    r'<script type="application/json" id="report">(?P<payload>.*?)</script>',
    re.DOTALL,
)

_REGENERATE = (
    "Regenerate it by running route steps 3-4 against live models — see this "
    "module's docstring for the procedure."
)


@pytest.fixture(scope="module")
def payload() -> dict:
    """The report JSON the viewer renders, parsed out of the page."""
    match = _PAYLOAD_BLOCK.search(EXAMPLE_REPORT.read_text(encoding="utf-8"))
    assert match is not None, (
        f"{EXAMPLE_REPORT.name} has no <script id='report'> block. The viewer "
        "reads its data from that block, so the page renders empty without it."
    )
    return json.loads(match.group("payload"))


def test_payload_validates_as_a_stride_report(payload):
    """The sample must be a shape the engine can actually emit."""
    try:
        StrideReport.model_validate(payload)
    except ValidationError as exc:
        failures = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        pytest.fail(
            f"The sample report is not a valid StrideReport:\n{failures}\n"
            f"The docs are showing a report shape the code would reject on "
            f"input. {_REGENERATE}"
        )


def test_schema_version_matches_the_code(payload):
    assert payload["schema_version"] == SCHEMA_VERSION, (
        f"Sample is schema {payload['schema_version']}, code emits "
        f"{SCHEMA_VERSION}. {_REGENERATE}"
    )


def test_sampling_covers_exactly_the_configured_tiers(payload):
    """The check the schema cannot make for itself.

    ``StrideReport.sampling`` is typed ``dict[str, dict[str, float | int | None]]``,
    so a payload keyed by long-renamed tiers validates perfectly happily. Only an
    explicit comparison against ``TIER_NAMES`` catches it.
    """
    assert set(payload["sampling"]) == set(TIER_NAMES), (
        f"Sample's sampling block is keyed {sorted(payload['sampling'])}, but "
        f"the configured tiers are {sorted(TIER_NAMES)}. {_REGENERATE}"
    )


def test_every_llm_node_carries_full_provenance(payload):
    """No provenance-stripped or synthetic payload may be pasted in as a shortcut.

    Deterministic FunctionNodes legitimately carry no model, so this asserts over
    ``LLM_NODES`` rather than over every node in the run.
    """
    incomplete = {}
    for node in payload["nodes"]:
        if node["node"] not in LLM_NODES:
            continue
        missing = [
            field
            for field in ("model", "requested_model", "sampling_fingerprint")
            if not node.get(field)
        ]
        if missing:
            incomplete[node["node"]] = missing

    assert not incomplete, (
        f"LLM nodes are missing provenance: {incomplete}. Every LLM node records "
        f"its served build, its configured route, and its generation identity — "
        f"the drift signal the report exists to carry. {_REGENERATE}"
    )
