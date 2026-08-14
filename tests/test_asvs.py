"""The ASVS package: what it declares, what it refuses, and what it rules.

Four seams, and each is one the ticket names. The **package gate** covers all
nine members and the disk layout. The **catalog** is this package's own private
data, so its own module checks it and this checks the roster the lane skills
publish against it. The **block** carries ASVS's own scope answer and its own
completeness check. The **precondition** is the first one in this repo that can
answer no, and it is exercised over real corpus models rather than a fixture.

No test here reads a private helper. Every one states a behaviour an operator or
a maintainer would notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stride_service.engine import EngineInputError, StrideEngine
from stride_service.frameworks import (
    PACKAGES,
    SCHEMAS,
    SEVERITY_RUBRIC_DOC,
    run_precondition,
    selectable_without_options,
    validate_package,
)
from stride_service.frameworks.asvs.catalog import (
    ASVS_VERSION,
    CHAPTERS,
    LANES,
    REQUIREMENTS,
    AsvsLevel,
    requirements_for,
)
from stride_service.frameworks.asvs.record import (
    AsvsAnalysis,
    AsvsOptions,
    RequirementProposal,
    requirement_of,
)
from stride_service.markdown_loader import MarkdownLoader, split_sections
from stride_service.report import FrameworkSelection, Report
from stride_service.skills import lane_skill_doc
from stride_service.sources import SourceLimits
from stride_service.system_model import SystemModel
from tests.factories import PROJECT_ROOT

ASVS = PACKAGES["asvs"]
ASVS_ROOT = PROJECT_ROOT / "frameworks" / "asvs"
CORPUS_DIR = PROJECT_ROOT / "evals" / "corpus"

package_loader = MarkdownLoader(ASVS_ROOT)


def corpus_model(case_id: str) -> SystemModel:
    """One blessed corpus model, read the way the eval verifier reads it."""
    path = CORPUS_DIR / case_id / "model.json"
    return SystemModel.model_validate(json.loads(path.read_text(encoding="utf-8")))


# --- The package gate --------------------------------------------------------


def test_the_shipped_package_passes_the_gate():
    """A second package earns no exemption: the same gate, the same nine members."""
    validate_package(ASVS, ASVS_ROOT)


def test_the_two_registration_tables_agree():
    """``PACKAGES`` and ``SCHEMAS`` are filled in one edit, and the gate says so."""
    assert set(PACKAGES) == set(SCHEMAS)
    assert SCHEMAS["asvs"].key_field not in ASVS.record.model_fields


def test_the_record_grades_nothing_so_no_rubric_ships():
    """The gate refuses a rubric beside a record that grades nothing.

    The rule holds in both directions, and ASVS is what makes the second
    direction more than a hypothetical: no severity field, and no file for one.
    """
    assert not ASVS.carries_severity()
    assert not (ASVS_ROOT / f"{SEVERITY_RUBRIC_DOC}.md").exists()


def test_the_options_carry_a_required_level_and_no_default():
    """A job that omits the level rejects; nothing invents one."""
    assert AsvsOptions.model_fields["level"].is_required()
    with pytest.raises(ValidationError):
        AsvsOptions.model_validate({})
    with pytest.raises(ValidationError):
        AsvsOptions.model_validate({"level": 4})
    assert AsvsOptions.model_validate({"level": 2}).level == 2


def test_an_unattended_caller_cannot_select_a_framework_that_needs_an_option():
    """The two callers with nobody to ask offer STRIDE and say ASVS is left out.

    A smoke run asks whether the application works here and the first-run app
    offers "run what this install is configured for". Neither has an operator to
    ask what ASVS level this install wants, and a default would put a choice in
    the report that nobody made.
    """
    assert selectable_without_options(("asvs", "stride")) == ("stride",)
    assert selectable_without_options(("stride",)) == ("stride",)
    assert selectable_without_options(("asvs",)) == ()


def test_the_engine_refuses_a_selection_missing_an_option_it_needs():
    """The rung the HTTP route already applied, applied to every other caller.

    Refused before the graph rather than after it: the block a job cannot build
    would otherwise fail once every node had been paid for.
    """
    with pytest.raises(EngineInputError) as caught:
        StrideEngine(
            runner=None,
            limits=SourceLimits(max_total_bytes=1, max_sources=1),
            deadline_seconds=1.0,
            frameworks=[FrameworkSelection(name="asvs")],
        )

    assert "options for framework 'asvs' are invalid" in str(caught.value)


def test_every_rule_names_a_lane_this_package_declares():
    declared = set(ASVS.lanes)
    assert {rule.lane for rule in ASVS.rules} <= declared
    assert len({rule.rule_id for rule in ASVS.rules}) == len(ASVS.rules)


# --- The catalog, and the roster the lane skills publish from it --------------


def test_the_catalog_is_the_published_standard():
    assert ASVS.version == ASVS_VERSION == "5.0.0"
    assert len(REQUIREMENTS) == 345
    assert len(CHAPTERS) == 17
    assert len(requirements_for(1)) == 70
    assert len(requirements_for(2)) == 70 + 183
    assert len(requirements_for(3)) == 345


def test_one_lane_is_one_chapter_in_the_standards_own_order():
    assert ASVS.lanes == LANES
    assert [chapter.id for chapter in CHAPTERS] == [f"V{n}" for n in range(1, 18)]
    dirs = sorted(path.name for path in (ASVS_ROOT / "lanes").iterdir())
    assert dirs == sorted(LANES)


@pytest.mark.parametrize("lane", LANES)
def test_each_lane_skill_publishes_its_chapters_whole_requirement_set(lane):
    """The prompt's roster and the catalog are one list, checked rather than trusted.

    The requirements reach a ``strong``-tier prompt through the lane skill, and
    the catalog is what the block's own checks read. A skill that dropped one
    would ask an agent to rule on a set the block would then call incomplete.
    """
    skill = package_loader.load(lane_skill_doc(lane))
    for requirement in requirements_for(3, lane):
        assert f"**{requirement.id}** (L{requirement.level})" in skill


def test_the_claim_id_is_the_standards_own_version_safe_reference():
    """``v5.0.0-1.2.5``, composed from the lane and the agent's key alone."""
    assert ASVS.compose_id("encoding-and-sanitization", "2.5") == "v5.0.0-1.2.5"
    assert ASVS.compose_id("webrtc", "1.1") == "v5.0.0-17.1.1"
    assert requirement_of("v5.0.0-1.2.5") == "V1.2.5"
    assert requirement_of("S-01") == ""


def test_the_agent_supplies_a_key_and_never_a_chapter():
    """The chapter is the graph's fact, so a proposal cannot spell one."""
    assert "chapter" not in RequirementProposal.model_fields
    assert ASVS.lane_fields("cryptography") == {"chapter": "cryptography"}
    with pytest.raises(ValidationError):
        RequirementProposal.model_validate(
            {
                "requirement": "V11.2.5",
                "title": "t",
                "description": "d",
                "quotes": [{"text": "q", "source_label": "l"}],
            }
        )


# --- The precondition, over real models --------------------------------------


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("01-payments-checkout", "satisfied"),
        ("10-cookbook-generic-cms", "satisfied"),
        ("03-batch-data-pipeline", "undecidable"),
        ("07-cicd-store-deploy", "undecidable"),
    ],
)
def test_the_precondition_answers_the_corpus_as_measured(case_id, expected):
    """ASVS applies cleanly to fewer than half of this repo's own corpus.

    That is the measurement the corpus split respects rather than works around,
    and it is the first time a precondition in this repo answers anything but
    ``satisfied`` against a real model.
    """
    assert run_precondition(ASVS, corpus_model(case_id)) == expected


def test_a_system_whose_protocols_are_all_stated_and_none_web_is_refuted():
    """The third state, and the one the corpus has no case for.

    ``refuted`` says do not name this framework for this system. It needs every
    flow to state a protocol, which is what separates it from ``undecidable``.
    """
    model = corpus_model("03-batch-data-pipeline")
    stated = model.model_copy(
        update={
            "data_flows": [
                flow.model_copy(update={"protocol": "SFTP"})
                for flow in model.data_flows
            ]
        }
    )
    assert run_precondition(ASVS, stated) == "refuted"


# --- The block ---------------------------------------------------------------


def _block(level: AsvsLevel, **overrides) -> AsvsAnalysis:
    """One ASVS block with no claims, built the way ``assemble`` builds it."""
    scope = AsvsAnalysis.scope_entries(
        lanes=ASVS.lanes,
        claims=(),
        options={"level": level},
        refusal_reason=overrides.pop("refusal_reason", ""),
    )
    return AsvsAnalysis(
        framework="asvs",
        framework_version=ASVS.version,
        disclaimer=(ASVS_ROOT / "disclaimer.md").read_text(encoding="utf-8").strip(),
        level=level,
        scope=scope,
        summary=AsvsAnalysis.summarize([], []),
        **overrides,
    )


def test_every_requirement_in_the_level_appears_exactly_once():
    """The standard's own rule, made mechanical by the block's own check."""
    block = _block(1)

    assert len(block.scope) == 70
    assert block.block_issues(known_element_ids=()) == []


def test_a_refused_framework_lists_the_level_as_not_applicable_with_the_reason():
    """A refusal is an answer, and the unit it answers in is the standard's.

    The neutral base answers in lanes because that is the only unit the service
    knows. ASVS owns a catalog, so it answers in requirements — which is what the
    standard asks of a report.
    """
    block = _block(1, refusal_reason="the asvs precondition refutes this system")

    assert len(block.scope) == 70
    assert all(entry.state == "not-applicable" for entry in block.scope)
    assert all("refutes this system" in entry.reason for entry in block.scope)


def test_a_level_that_moves_moves_the_scope_list_with_it():
    """The level the operator asked for is what the block rules against."""
    assert [len(_block(level).scope) for level in (1, 2, 3)] == [70, 253, 345]


def test_a_block_missing_a_requirement_of_its_level_says_so():
    """The check bites, and it names what it could not find."""
    block = _block(1)
    trimmed = block.model_copy(update={"scope": block.scope[:-1]})

    (issue,) = trimmed.block_issues(known_element_ids=())
    assert "appear in neither the claims nor scope" in issue


def test_the_block_survives_the_report_round_trip_as_its_own_type():
    """``SerializeAsAny`` is what keeps a narrowed block from flattening.

    The property belongs to the validator and the serializer as a pair, so it is
    tested as a round trip rather than as a validation.
    """
    from tests.factories import sample_report

    report = sample_report()
    payload = report.model_dump(mode="json")
    payload["job"]["frameworks"].append({"name": "asvs", "options": {"level": 1}})
    payload["analyses"].append(_block(1).model_dump(mode="json"))

    restored = Report.model_validate(payload)
    (_, block) = restored.analyses

    assert isinstance(block, AsvsAnalysis)
    assert block.level == 1
    assert (
        Report.model_validate(restored.model_dump(mode="json")).analyses[1].level == 1
    )


def test_the_disclaimer_states_the_compliance_boundary():
    """The package says what its claims assert, and the report carries it."""
    disclaimer = (ASVS_ROOT / "disclaimer.md").read_text(encoding="utf-8")

    assert "not a compliance result" in disclaimer
    assert "reports no pass" in disclaimer


@pytest.mark.parametrize("lane", LANES)
def test_no_lane_skill_calls_a_run_compliance(lane):
    """A level-filtered run is a fork of the standard rather than the standard."""
    skill = package_loader.load(lane_skill_doc(lane))
    body = split_sections(skill)["Guardrails"]

    assert "Never use the word compliance" in body


def test_the_package_root_carries_no_stray_markdown():
    """The gate's own rule, stated where a maintainer adding a file will see it."""
    expected = {
        Path("critic.md"),
        Path("disclaimer.md"),
        Path("output.md"),
        *(
            Path("lanes") / lane / f"{doc}.md"
            for lane in LANES
            for doc in ("skill", "exemplars")
        ),
    }
    found = {path.relative_to(ASVS_ROOT) for path in ASVS_ROOT.rglob("*.md")}

    assert found == expected
