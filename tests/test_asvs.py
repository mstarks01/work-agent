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
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from stride_service.engine import EngineInputError, StrideEngine
from stride_service.evidence import evidence_catalog, resolve_proposals
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
    AsvsChapter,
    AsvsOptions,
    RequirementProposal,
    RequirementRuling,
    requirement_of,
)
from stride_service.markdown_loader import MarkdownLoader, split_sections
from stride_service.report import (
    FrameworkSelection,
    Ground,
    Report,
    ScopeEntry,
    Verdict,
)
from stride_service.skills import lane_skill_doc
from stride_service.sources import SourceLimits
from stride_service.system_model import SystemModel
from tests.factories import PROJECT_ROOT, valid_model

ASVS = PACKAGES["asvs"]
STRIDE = PACKAGES["stride"]
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


@pytest.mark.parametrize("lane", LANES)
def test_each_lane_skill_quotes_the_catalogs_own_requirement_text(lane):
    """The roster is the catalog's words, not a paraphrase of them.

    The identifier and the level were already checked. The *text* was not, and
    it is the part the agent actually rules against: a skill carrying the right
    number beside drifted wording asks for a ruling on something the standard
    does not say, and every catalog-derived check downstream still passes,
    because they all key on the identifier.

    One copy of the standard's prose would be better than two. Until the skills
    are generated from the catalog, this is what keeps the second copy honest.
    """
    skill = package_loader.load(lane_skill_doc(lane))
    for requirement in requirements_for(3, lane):
        line = f"- **{requirement.id}** (L{requirement.level}) — {requirement.text}"
        assert line in skill, (
            f"{lane}/skill.md states {requirement.id} with text that is not the"
            " catalog's. Copy the catalog's wording, or regenerate the roster."
        )


def test_each_level_carries_the_share_the_standard_states():
    """70 / 183 / 92. The standard states L1's share as "20%" of 345 in prose.

    ``test_the_catalog_is_the_published_standard`` checks the cumulative totals.
    This checks the split behind them, which is what a catalog from another
    release would get wrong while still summing to 345.
    """
    assert dict(Counter(req.level for req in REQUIREMENTS)) == {1: 70, 2: 183, 3: 92}


def test_the_chapters_are_5_0s_own_and_carry_no_4_x_survivor():
    """V1..V17 as 5.0 numbers them, which is not how 4.x did.

    5.0 removed the 4.x ``V1 Architecture, Design & Threat Modeling`` chapter
    and renumbered the rest, so a catalog carrying it — or carrying 4.x names
    like ``Stored Cryptography`` — is a previous release wearing this one's
    version string.
    """
    assert [chapter.name for chapter in CHAPTERS] == [
        "Encoding and Sanitization",
        "Validation and Business Logic",
        "Web Frontend Security",
        "API and Web Service",
        "File Handling",
        "Authentication",
        "Session Management",
        "Authorization",
        "Self-contained Tokens",
        "OAuth and OIDC",
        "Cryptography",
        "Secure Communication",
        "Configuration",
        "Data Protection",
        "Secure Coding and Architecture",
        "Security Logging and Error Handling",
        "WebRTC",
    ]


def test_no_claim_id_of_another_asvs_release_resolves():
    """The version in a claim ID is a gate, not decoration.

    ``requirement_of`` returns the empty string for anything not prefixed with
    this build's version, so a 4.x citation and a future 5.x one both fail to
    resolve rather than being read as this release's.
    """
    assert requirement_of("v5.0.0-6.2.1") == "V6.2.1"
    for foreign in ("v4.0.3-2.1.1", "v5.1.0-6.2.1", "V6.2.1", "6.2.1", ""):
        assert requirement_of(foreign) == "", foreign


CORPUS_DOCUMENTS = [
    (kind, name)
    for kind, table in (
        ("notes", ASVS.knowledge.notes),
        ("cases", ASVS.knowledge.cases),
    )
    for name in table
]


def _shingles(text: str, length: int = 8) -> set[str]:
    """Every ``length``-word lowercase run in ``text``, for overlap detection."""
    words = re.findall(r"[a-z]+", text.lower())
    return {
        " ".join(words[index : index + length])
        for index in range(len(words) - length + 1)
    }


@pytest.mark.parametrize("kind,name", CORPUS_DOCUMENTS, ids=lambda v: str(v))
def test_no_corpus_document_restates_the_catalog(kind, name):
    """A note teaches how to rule. It does not carry a second roster.

    This is the reason ASVS shipped no corpus for as long as it did, kept as a
    check now that it ships one. Its lane skills already state every requirement
    of their chapter verbatim, so a note repeating one would put the standard in
    a second place — and the copy nobody generates is the copy that drifts.

    Two things are refused. A **requirement identifier**, because a citation is
    something the service composes onto a claim, never something a prompt
    document spells. And a **long verbatim span** of published text, which is
    the same duplication wearing different words.

    A document needs neither. The whole roster is already in the prompt directly
    beside it, so what is left for a note to add is which question the chapter
    asks and how to tell whether it reaches this system.
    """
    text = package_loader.load(f"{kind}/{name}")

    cited = re.findall(r"\bV\d+\.\d+\.\d+\b", text)
    assert not cited, (
        f"{kind}/{name} cites {sorted(set(cited))}. The chapter's roster carries"
        " the requirements; naming one here is a second copy of the catalog."
    )

    published = {
        shingle
        for requirement in REQUIREMENTS
        for shingle in _shingles(requirement.text)
    }
    shared = _shingles(text) & published
    assert not shared, (
        f"{kind}/{name} repeats published requirement text: {sorted(shared)[:2]}"
    )


def test_the_corpus_covers_every_rule_and_names_no_other():
    """Leaving the empty corpus put all 17 rules under the retrieval check.

    ``test_every_rule_can_retrieve_something`` in the neutral lints already says
    this for whichever packages have a corpus. Asserted here too because the
    number is the point: ASVS declares 17 rules, and a corpus that covered 16 of
    them would leave one lane with a lead and nothing behind it.
    """
    rule_ids = {rule.rule_id for rule in ASVS.rules}
    covered = {
        rule_id
        for table in (ASVS.knowledge.notes, ASVS.knowledge.cases)
        for rules in table.values()
        for rule_id in rules
    }

    assert covered == rule_ids, (
        f"uncovered: {sorted(rule_ids - covered)};"
        f" unknown: {sorted(covered - rule_ids)}"
    )


def test_which_lanes_carry_no_candidate_rule_is_pinned():
    """Every lane carries a rule, and losing one is a decision, not a drift.

    Six lanes had none while the rules were authored against the level 1
    requirements first. #193 settled that this was allowed — a **Candidate** is
    a lead and not a gate, and a lane agent analyses its chapter either way —
    but the six were not harmless: retrieval is keyed by *fired rule*, so a lane
    with no rule also received no reference note and no worked case, whatever
    the knowledge tables held.

    Closing the set is what makes the corpus reach every chapter. The assertion
    stays as an empty list rather than being deleted, so a lane that loses its
    last rule fails here instead of quietly going dark.
    """
    ruleless = sorted(set(ASVS.lanes) - {rule.lane for rule in ASVS.rules})

    assert ruleless == [], (
        "an ASVS lane lost its last candidate rule. A lane with no rule reaches"
        " its agent with no deterministic lead and retrieves nothing, because"
        " retrieval is keyed by fired rule. Say why here, or give it a rule."
    )
    # STRIDE's contrast, which is the parity question this pins: every STRIDE
    # lane carries a rule, because its rules read structure every model has.
    assert not set(STRIDE.lanes) - {rule.lane for rule in STRIDE.rules}


def test_a_claim_naming_an_unpublished_requirement_is_dropped_and_marked():
    """An invented requirement number costs its entry, never the run (#138's rule).

    ``99.99`` is as well-formed as ``2.1``: both match the key pattern, and the
    service composes ``v5.0.0-6.99.99`` from either. Only the catalog knows
    which one the standard publishes. Unchecked, the report cites the standard's
    own version-safe reference format for a requirement that does not exist —
    a citation that reads as verifiable and resolves to nothing.
    """
    model = valid_model()
    catalog = evidence_catalog(model)
    reference = next(iter(catalog))
    proposals = [
        RequirementProposal(
            requirement="2.1",
            title="A real requirement",
            description="d",
            evidence_refs=[reference],
        ),
        RequirementProposal(
            requirement="99.99",
            title="An invented requirement",
            description="d",
            evidence_refs=[reference],
        ),
    ]

    resolution = resolve_proposals(proposals, catalog, ASVS, "authentication")

    assert [draft.id for draft in resolution.drafts] == ["v5.0.0-6.2.1"]
    assert [
        (mark.claim_id, mark.title)
        for mark in resolution.marks.unknown_claim_identities
    ] == [("v5.0.0-6.99.99", "An invented requirement")]


def test_a_framework_that_mints_its_own_ids_marks_nothing_unknown():
    """STRIDE declares no ``known`` predicate, so no key of its can be absent.

    The empty list is a written statement rather than an omission: ``S-01`` is a
    lane letter and a counter, so there is no roster for a key to be missing
    from. Asserted rather than assumed, because a predicate added to the neutral
    resolver by mistake would start dropping STRIDE's claims silently.
    """
    assert STRIDE.id_rule.known is None
    for key in (1, 99, 999):
        assert STRIDE.id_rule.knows("spoofing", key)


def test_a_claims_id_and_its_chapter_field_must_agree():
    """The service composes both from one lane, so a disagreement is its own defect.

    Fatal rather than dropped, which is the split this package's two new checks
    sit either side of: an agent can invent a requirement number, so that costs
    the entry; an agent cannot separate the ID from the chapter, so this costs
    the run and says so.
    """
    crossed = _block(1, [sample_asvs_claim("v5.0.0-11.1.1", "authentication")])

    issues = crossed.block_issues(set())

    assert any("chapter field says 'authentication'" in issue for issue in issues)
    assert any("V11.1.1" in issue for issue in issues)


def test_a_claim_whose_chapter_and_id_agree_raises_no_chapter_issue():
    """The ordinary case, so the check above cannot be passing vacuously."""
    block = _block(1, [sample_asvs_claim("v5.0.0-6.2.1", "authentication")])

    assert not [issue for issue in block.block_issues(set()) if "chapter" in issue]


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
        # Named a web app in its own prose, and every flow's protocol unstated.
        # This case is why the precondition stopped reading transport (#219).
        ("11-sparse-shift-scheduling", "satisfied"),
        ("12-overclaiming-supplier-portal", "satisfied"),
        # Airflow and Spark: the model states what both processes present, and
        # neither is the web. A statement, not a silence.
        ("03-batch-data-pipeline", "refuted"),
        # The source never says what the controller or the store servers
        # present, so nothing here is decided. Its remedy is to submit more.
        ("07-cicd-store-deploy", "undecidable"),
    ],
)
def test_the_precondition_answers_the_corpus_as_measured(case_id, expected):
    """All three states, against real models.

    The split moved when the precondition stopped answering "is this a web
    application?" from a **Data Flow**'s ``protocol``
    ([#219](https://github.com/mstarks01/work-agent/issues/219)). Four cases
    whose prose names a web application had been refused for saying nothing
    about transport, which was the wrong question asked of the wrong field.
    """
    assert run_precondition(ASVS, corpus_model(case_id)) == expected


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (("web", "non-web"), "satisfied"),
        (("web", "unknown"), "satisfied"),
        (("unknown", "non-web"), "undecidable"),
        (("unknown", "unknown"), "undecidable"),
        (("non-web", "non-web"), "refuted"),
    ],
)
def test_the_processes_decide_whatever_the_flows_leave_unsaid(kinds, expected):
    """One process presenting the web is enough; one silent one holds it open.

    Every flow here states no protocol at all, which is the shape that used to
    force ``undecidable`` however plainly the processes were described.
    """
    model = valid_model()
    template = model.processes[0]
    processes = [
        template.model_copy(update={"id": f"process:p{index}", "interface_kind": kind})
        for index, kind in enumerate(kinds)
    ]
    silent = [
        flow.model_copy(update={"protocol": "unknown"}) for flow in model.data_flows
    ]

    answer = run_precondition(
        ASVS, model.model_copy(update={"processes": processes, "data_flows": silent})
    )

    assert answer == expected


def test_a_stated_web_protocol_still_satisfies_on_its_own():
    """A flow that says HTTPS says the same thing by another route.

    Kept as a satisfier because it is one. What it lost is the power to *refuse*
    and the power to hold the answer open, neither of which was ever a fact about
    transport.
    """
    model = valid_model()
    non_web = [
        process.model_copy(update={"interface_kind": "non-web"})
        for process in model.processes
    ]
    web = [flow.model_copy(update={"protocol": "HTTPS"}) for flow in model.data_flows]

    answer = run_precondition(
        ASVS, model.model_copy(update={"processes": non_web, "data_flows": web})
    )

    assert answer == "satisfied"


def test_a_flow_that_never_said_no_longer_holds_a_decided_model_open():
    """The defect #219 reported, as a test.

    Every process states a non-web interface and no flow states anything. The
    model has answered the question; the unstated transport is a different fact
    and no longer overrides it.
    """
    model = valid_model()
    decided = [
        process.model_copy(update={"interface_kind": "non-web"})
        for process in model.processes
    ]
    silent = [flow.model_copy(update={"protocol": ""}) for flow in model.data_flows]

    answer = run_precondition(
        ASVS, model.model_copy(update={"processes": decided, "data_flows": silent})
    )

    assert answer == "refuted"


@pytest.mark.parametrize(
    ("protocols", "expected"),
    [
        (("", ""), "undecidable"),
        (("", "AMQP"), "undecidable"),
        (("unknown", "AMQP"), "undecidable"),
        (("unknown; the team never said", "AMQP"), "undecidable"),
        (("none", "AMQP"), "undecidable"),
        (("unknownish binary framing", "AMQP"), "refuted"),
        (("AMQP", "SFTP"), "refuted"),
        (("HTTPS", "SFTP"), "satisfied"),
    ],
)
def test_a_model_with_no_processes_is_answered_by_its_flows(protocols, expected):
    """The fallback, for a model carrying no **Process** to ask.

    Silence is ``undecidable`` and a stated non-web protocol is ``refuted``. The
    two are never collapsed, because the remedy differs: one says do not name
    this framework for this system, the other says the input did not say. A blank
    protocol reaches a **Valid System Model** — the gate sets no minimum length —
    and reading it as a stated non-web protocol would tell an operator to drop
    ASVS when what they need is to submit more.

    ``unknownish binary framing`` is a stated protocol on purpose: the leading
    token is read as a *word*, which is the rule every other reader of an
    attribute in this repo already applies.
    """
    model = valid_model()
    flows = [
        flow.model_copy(update={"protocol": protocol})
        for flow, protocol in zip(model.data_flows, protocols, strict=True)
    ]

    answer = run_precondition(
        ASVS, model.model_copy(update={"processes": [], "data_flows": flows})
    )

    assert answer == expected


def test_a_model_with_nothing_to_read_at_all_is_undecidable():
    """Nothing said, rather than nothing there."""
    model = valid_model()

    assert run_precondition(
        ASVS, model.model_copy(update={"processes": [], "data_flows": []})
    ) == ("undecidable")


# --- The block ---------------------------------------------------------------


def sample_asvs_claim(
    claim_id: str = "v5.0.0-6.2.1", chapter: AsvsChapter = "authentication"
) -> RequirementRuling:
    """One ruled ASVS claim against valid_model(), in the shape the graph builds."""
    return RequirementRuling(
        id=claim_id,
        framework="asvs",
        framework_version=ASVS.version,
        chapter=chapter,
        title="No password length policy is stated",
        description="The requirement applies and the input does not settle it.",
        affected_element_ids=[],
        grounds=[Ground(kind="derived-fact", flow_id="flow:customer-to-web-app:login")],
        verdict=Verdict(status="confirmed"),
    )


def _block(
    level: AsvsLevel,
    claims: Sequence[RequirementRuling] = (),
    refusal_reason: str = "",
) -> AsvsAnalysis:
    """One ASVS block, built the way ``assemble`` builds it.

    ``scope`` and ``summary`` come from the block type's own hooks rather than
    from a literal, so a test cannot assert against a shape the graph would not
    produce.
    """
    return AsvsAnalysis(
        framework="asvs",
        framework_version=ASVS.version,
        disclaimer=(ASVS_ROOT / "disclaimer.md").read_text(encoding="utf-8").strip(),
        level=level,
        claims=list(claims),
        scope=AsvsAnalysis.scope_entries(
            lanes=ASVS.lanes,
            claims=claims,
            options={"level": level},
            refusal_reason=refusal_reason,
        ),
        summary=AsvsAnalysis.summarize(claims, []),
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


def test_a_claim_outside_the_level_does_not_fail_the_report():
    """An agent's slip costs its entry, never the run.

    A block issue raises out of the report validator and costs the whole job,
    after 23 `strong`-tier calls have been paid for. So the only checks on that
    list are ones the *service* can fail — and a claim naming a requirement one
    level further out is the one thing on this block a lane agent can get wrong
    on its own. The finding rides; `level` still says what was asked for.
    """
    out_of_level = sample_asvs_claim("v5.0.0-6.2.9")  # V6.2.9 is level 2
    block = _block(1, claims=[out_of_level])

    assert block.block_issues(known_element_ids=()) == []
    assert len(block.scope) == 70


def test_a_scope_entry_outside_the_level_still_fails_the_report():
    """The other half of the same rule: `scope` is the service's own to build.

    Nothing an agent emits reaches this list, so an entry outside the level can
    only mean `scope_entries` got it wrong — which is exactly what a fatal check
    is for.
    """
    block = _block(1)
    tampered = block.model_copy(
        update={
            "scope": [
                *block.scope,
                ScopeEntry(unit="V6.2.9", state="applicable", reason=""),
            ]
        }
    )

    (issue,) = tampered.block_issues(known_element_ids=())
    assert "scope names requirements outside level 1" in issue


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
        # Derived from the knowledge tables rather than listed, so a document
        # on disk that no table indexes shows up here as a stray file — which
        # is what it is: unreachable text no rule can retrieve.
        *(Path(f"{document}.md") for document in ASVS.knowledge.documents()),
    }
    found = {path.relative_to(ASVS_ROOT) for path in ASVS_ROOT.rglob("*.md")}

    assert found == expected
