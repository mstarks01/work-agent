"""Two STRIDE drafts at one verb and one place, each failing its own control.

``frameworks/stride/output.md`` allows two drafts that share a verb and an
element set where each cites its own control and needs its own fix, and it
tells the lane to fold a near-duplicate into the first draft. The spoofing
exemplars show both: drafts 4 and 5 are the two ends of one flow, and the
paragraph after draft 5 is the near-duplicate. ADR 0060 is the identity rule
the instruction follows.

These tests drive the two exemplar drafts through the scripted graph: the lane
instruction it assembles, the fan-in, the critic's input, the report, and the
identity and follow-up readers that read the report.
"""

import json

from analysis_service import graph
from analysis_service.claim_identity import (
    distinct_mechanisms,
    finding_key,
    mechanism_of,
    report_rows,
)
from analysis_service.fan_in import fan_in
from analysis_service.frameworks import PACKAGES, schemas_for
from analysis_service.jobs import JobRecord, PipelineCompleted
from analysis_service.prompts import lane_exemplars_doc
from analysis_service.report_changes import report_changes
from analysis_service.sources import Source
from analysis_service.system_model import ModelIndex
from tests.factories import claims_json, emitted, sample_ruling, sample_selection
from tests.factories import scripted_pipeline as build
from tests.test_exemplar_fan_in import fixture_model, sources_of
from tests.test_pipeline import CRITIC, block, run
from tests.test_prompt_lints import (
    PACKAGE_LOADERS,
    exemplar_sections,
    exemplar_systems,
    json_blocks,
    source_block,
)

SPOOFING = graph.Lane("stride", "spoofing").node_name

#: The exemplar drafts that share ``impersonate`` and one flow's two ends.
CALLER_DRAFT, SERVER_DRAFT = 4, 5

#: The heading of the section that holds the second draft and the near-duplicate.
SERVER_SECTION = (
    "Same verb, same place, another control: a fake ledger the web API never checks"
)
NEAR_DUPLICATE = "**A near-duplicate is one draft, not two.**"

#: The contract's own words for the rule, as the lane must read them.
CONTRACT_PHRASES = (
    "**One threat per attacker action at one place, and one per failed control there**",
    (
        "a fake server that the client never checks and a fake client that the"
        " server never checks are two threats."
    ),
    (
        "it is the first draft again, so add its other attacker or starting point"
        " to the first draft's `description`."
    ),
)


def spoofing_drafts():
    """The spoofing exemplar drafts as emitted JSON, by their ``sequence``."""
    text = PACKAGE_LOADERS["stride"].load(lane_exemplars_doc("spoofing"))
    drafts = (json.loads(body) for body in json_blocks(text))
    return {draft["sequence"]: draft for draft in drafts}


def pair():
    drafts = spoofing_drafts()
    return [drafts[CALLER_DRAFT], drafts[SERVER_DRAFT]]


def near_duplicate():
    """Draft 4 again, from another starting point: the paragraph's candidate."""
    return {
        **spoofing_drafts()[CALLER_DRAFT],
        "sequence": 6,
        "title": "A compromised core host can send transfers to the ledger as the"
        " web API",
    }


def system_a_job() -> JobRecord:
    label, text = source_block(exemplar_systems()["A"])
    record = JobRecord.create(
        assertion_pass=True,
        owner_id="idp|user-1",
        sources=[Source.description(text, label=label)],
        system_name="Payments platform",
        frameworks=sample_selection(),
    )
    record.transition("running")
    return record


def run_pair():
    """One scripted job on system A whose spoofing lane emits drafts 4 and 5."""
    replies = {
        "extract": emitted(fixture_model("A")),
        SPOOFING: json.dumps({"claims": pair()}),
        CRITIC: claims_json(sample_ruling("S-04"), sample_ruling("S-05")),
    }
    pipeline, models = build(replies)
    outcome, _ = run(pipeline, system_a_job())
    assert isinstance(outcome, PipelineCompleted)
    return outcome.report, models


def merged(emitted_drafts):
    """The drafts as the fan-in returns them for system A."""
    batch = schemas_for("stride").proposals.model_validate({"claims": emitted_drafts})
    return fan_in(
        {"spoofing": batch},
        PACKAGES["stride"],
        fixture_model("A"),
        sources_of("A"),
        {},
        None,
        {},
    )


def test_the_lane_reads_the_revised_contract_and_both_exemplars():
    _, models = run_pair()
    (instruction,) = models[SPOOFING].seen

    for phrase in CONTRACT_PHRASES:
        assert phrase in instruction
    for draft in pair():
        assert draft["title"] in instruction
    assert NEAR_DUPLICATE in instruction


def test_the_near_duplicate_is_taught_as_no_draft_of_its_own():
    section = exemplar_sections("stride", "spoofing")[SERVER_SECTION]
    after_draft = section.split("```")[-1]

    assert len(json_blocks(section)) == 1
    assert NEAR_DUPLICATE in after_draft
    assert "It is draft 4 again." in after_draft


def test_both_drafts_survive_the_fan_in_with_their_own_controls():
    result = merged(pair())
    flows = ModelIndex.of(fixture_model("A")).flow_endpoints
    caller, server = result.drafts

    assert [draft.id for draft in result.drafts] == ["S-04", "S-05"]
    assert finding_key(caller, flows) == finding_key(server, flows)
    assert mechanism_of(caller, flows, {}) == {"authentication"}
    assert mechanism_of(server, flows, {}) == {"encryption_in_transit"}
    assert distinct_mechanisms(
        mechanism_of(caller, flows, {}), mechanism_of(server, flows, {})
    )


def test_the_near_duplicate_is_one_finding_with_the_first_draft():
    result = merged([spoofing_drafts()[CALLER_DRAFT], near_duplicate()])
    flows = ModelIndex.of(fixture_model("A")).flow_endpoints
    first, again = result.drafts

    assert finding_key(first, flows) == finding_key(again, flows)
    assert not distinct_mechanisms(
        mechanism_of(first, flows, {}), mechanism_of(again, flows, {})
    )


def test_the_critic_reads_both_drafts_and_the_report_keeps_both():
    report, models = run_pair()
    (critic_instruction,) = models[CRITIC].seen

    assert critic_instruction.count('"id": "S-04"') == 1
    assert critic_instruction.count('"id": "S-05"') == 1
    assert [claim.id for claim in block(report).claims] == ["S-04", "S-05"]
    flows = ModelIndex.of(report.system_model).flow_endpoints
    rows = report_rows(report)
    caller, server = block(report).claims
    assert finding_key(caller, flows) == finding_key(server, flows)
    assert distinct_mechanisms(
        mechanism_of(caller, flows, rows), mechanism_of(server, flows, rows)
    )


def test_a_follow_up_that_loses_one_end_reports_it_gone():
    report, _ = run_pair()
    (analysis,) = report.analyses
    caller_only = report.model_copy(
        update={
            "analyses": [analysis.model_copy(update={"claims": analysis.claims[:1]})]
        }
    )

    changes = {
        change.claim_id: change.change for change in report_changes(report, caller_only)
    }

    assert changes == {"S-04": "unchanged", "S-05": "gone"}
