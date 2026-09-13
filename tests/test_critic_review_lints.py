"""The critic-review fixture set: well formed, really grounded, and signed or not.

The set answers one question — does the critic read a draft's argument, or only
its references — and it is the only instrument for it, because whether a
conclusion follows from a fact is the judgement no rule can make. That makes its
provenance load-bearing in a way a corpus lint's is not: an expectation nobody
signed is one agent grading another agent's change.

So these checks do two jobs. They hold each fixture to the real corpus model, so
a draft cannot drift into describing a system the corpus no longer carries. And
they read ``reviewed_by``, so an unsigned set can be run and read but cannot be
quoted as evidence.

Deterministic, credential-free, and free of provider calls.
"""

from __future__ import annotations

import pytest

from analysis_service.frameworks import PACKAGES
from analysis_service.grounding import normalize, verify_normalized
from analysis_service.system_model import ModelIndex
from evals.critic_review.loading import corpus_model, load_fixtures, source_text
from evals.critic_review.model import CriticFixture, FixtureKind


def draft_of(fixture: CriticFixture):
    """The fixture's draft in its own package's record shape.

    Looked up in ``PACKAGES`` rather than imported, so this lint reads whichever
    package a fixture names and a second package's fixtures need no edit here.
    """
    return PACKAGES[fixture.framework].record.model_validate(fixture.draft)


ALL = load_fixtures()
IDS = [entry.id for entry in ALL]


def test_the_set_is_small_and_carries_every_kind():
    """Four kinds, and none of them empty.

    A set missing ``credible-conditional`` would be passed by a critic that
    rejects every conditional draft, which is the cheapest wrong answer to the
    change and the one a kill-counting measurement rewards.
    """
    kinds = {entry.kind for entry in ALL}

    assert kinds == set(FixtureKind.__args__)
    assert len(ALL) == len(set(IDS)), "fixture ids are unique"


def test_both_halves_of_the_measurement_have_rows():
    """Findings to remove and findings to preserve, or the set measures one thing."""
    survives = [entry for entry in ALL if entry.expect.survives]
    removed = [entry for entry in ALL if not entry.expect.survives]

    assert survives and removed


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_each_draft_is_one_a_lane_agent_could_have_emitted(fixture):
    """The package's own record parses it, so the fixture is a draft and not a sketch."""
    draft_of(fixture)


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_every_element_a_fixture_names_is_in_its_corpus_model(fixture):
    """A fixture describing elements the case does not hold tests nothing."""
    model = corpus_model(fixture.case)
    known = {element.id for element in model.elements()}
    draft = draft_of(fixture)

    assert set(draft.affected_element_ids) <= known
    for ground in draft.grounds:
        if ground.element_id:
            assert ground.element_id in known, ground.element_id
        if ground.flow_id:
            assert ground.flow_id in ModelIndex.of(model).flow_endpoints


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_every_quoted_ground_is_really_in_the_source(fixture):
    """The quotes are the case's own words, checked the way the gate checks them.

    A fixture arguing against a sentence nobody submitted would be rejected for
    its citation rather than for its reasoning, and stops testing the thing it
    is there to test.

    Read through :func:`~analysis_service.grounding.verify_normalized`, which is
    what the fan-in runs on a real ground. A second rule here would eventually
    accept a quote the service rejects, and the fixture would be measuring the
    lint rather than the critic — the sources are hard-wrapped prose, so a plain
    substring test disagrees with the gate on the first quote spanning a line
    break.
    """
    folded = normalize(source_text(fixture.case))
    draft = draft_of(fixture)

    for ground in draft.grounds:
        if ground.kind == "quote":
            assert verify_normalized(ground.text, folded), ground.text


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_an_unknown_ground_names_an_attribute_the_model_really_leaves_open(fixture):
    """The shield has to be real, or the bypass fixture does not exercise it."""
    model = corpus_model(fixture.case)
    by_id = {element.id: element for element in model.elements()}
    draft = draft_of(fixture)

    for ground in draft.grounds:
        if ground.kind != "unknown-attribute":
            continue
        value = getattr(by_id[ground.element_id], ground.attribute, None)
        assert value == "unknown", (
            f"{fixture.id}: {ground.element_id}.{ground.attribute} is {value!r},"
            " so this ground would not resolve as an unknown on a real run"
        )


def test_the_contradiction_fixtures_leave_their_step_open_on_purpose():
    """Which step kills a contradicted draft is unsettled, and stays unsettled.

    The model showing what the draft calls missing reads as ``evidence``; the
    draft asserting what the model contradicts reads as ``reasoning``. Both are
    defensible, so a set proving the contradiction is caught must not also
    fail a critic for picking the other name. This test exists so nobody
    tightens that back up without deciding the taxonomy first.
    """
    contradicted = [entry for entry in ALL if entry.kind == "contradicted-by-source"]

    assert contradicted
    assert all(entry.expect.rejected_because is None for entry in contradicted)


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_an_exact_status_agrees_with_whether_the_draft_survives(fixture):
    """The two expectation fields cannot disagree about the same fixture."""
    if fixture.expect.status is None:
        return
    assert fixture.expect.survives == (fixture.expect.status != "rejected")


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_every_negative_fixture_says_what_its_reason_must_engage_with(fixture):
    """A rejection is only right for the right reason.

    "The control is unknown" rejects every conditional draft in the corpus. It
    would score full marks on every negative row here and destroy the report,
    so a negative fixture that named no anchor would reward exactly the wrong
    answer to the change.
    """
    if fixture.expect.survives:
        assert fixture.expect.reason_must_name == (), "nothing to justify"
    else:
        assert fixture.expect.reason_must_name, fixture.id


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_no_anchor_is_satisfied_by_naming_the_unknown_alone(fixture):
    """The anchors must not be answerable by the bypass wording itself.

    An anchor of "unknown" or of the shielding attribute's own name would pass
    a critic that rejected the draft for being conditional, which is the answer
    these fixtures exist to fail.
    """
    draft = draft_of(fixture)
    shields = {
        ground.attribute.lower()
        for ground in draft.grounds
        if ground.kind == "unknown-attribute"
    }

    for anchor in fixture.expect.reason_must_name:
        assert anchor.lower() not in {*shields, "unknown", "needs-info"}, anchor


def test_the_set_keeps_a_control_that_reaches_the_critic_today():
    """One negative row with no unknown ground, so a failure can be localised.

    Every other negative fixture is behind the bypass, and if the critic cannot
    detect a bad argument at all then all of them fail for that reason rather
    than for the bypass. The row without a shield tells the two apart.
    """
    unshielded = [
        entry
        for entry in ALL
        if not entry.expect.survives
        and not any(
            ground.kind == "unknown-attribute" for ground in draft_of(entry).grounds
        )
    ]

    assert unshielded, "no negative fixture reaches the critic before the change"


def test_every_other_negative_fixture_is_behind_the_bypass():
    """The rest must carry an unknown ground, or they do not test the change."""
    shielded = [
        entry
        for entry in ALL
        if not entry.expect.survives
        and any(
            ground.kind == "unknown-attribute" for ground in draft_of(entry).grounds
        )
    ]

    assert len(shielded) >= 3, "too few negative fixtures exercise the bypass"


def test_no_fixture_is_signed_by_whoever_drafted_it():
    """The signature means a second reader, or it means nothing.

    ``drafted_by`` says an agent drafted the fixture and its proposed answer.
    If that same name could appear in ``reviewed_by``, the set would carry a
    signature attesting that its author agrees with itself, which is the exact
    failure the field exists to prevent.
    """
    for entry in ALL:
        assert entry.reviewed_by != entry.drafted_by, entry.id


def test_an_unsigned_set_is_named_rather_than_trusted():
    """The gate on quoting this set as evidence.

    Not a failure while the set is unsigned — it is useful to run before anyone
    reads it. What it must never do is pass silently, because a number from an
    unsigned set looks exactly like a number from a signed one.
    """
    unsigned = [entry.id for entry in ALL if entry.reviewed_by is None]

    if unsigned:
        pytest.skip(
            f"{len(unsigned)} of {len(ALL)} fixtures are unsigned, so this set"
            " states what an agent proposed and not what a reader ruled. It may"
            " be run; it may not be quoted as evidence that the review change"
            f" works. Unsigned: {unsigned}"
        )
    assert all(entry.reviewed_by for entry in ALL)


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_a_recommendation_ruled_sound_is_about_this_claim(fixture):
    """A fixture's own `recommendation_sound` decides which rule applies.

    A package whose critic text rules on a draft's recommendation needs the
    set to carry both kinds. Where the reader ruled the advice sound it has to be about this
    claim, and the mechanical half of that is decidable: advice about this
    claim names a place this claim is about, and advice about the harness
    names the harness.

    Where the reader ruled it unsound it is a deliberate distractor, and
    :func:`test_the_set_carries_both_answers_about_a_recommendation` is what
    holds it in place instead.

    Asked of a package whose drafts carry recommendations, which is a property
    of the record rather than a package's name: a package whose claims offer
    none asks for no such judgement and reads nothing here.
    """
    draft = draft_of(fixture)
    mitigations = getattr(draft, "mitigations", None)
    if mitigations is None:
        pytest.skip(f"{fixture.framework} claims carry no recommendations")

    assert mitigations, (
        f"{fixture.id}: the recommendation reading has nothing to rule on"
    )
    for mitigation in mitigations:
        words = f"{mitigation.summary} {mitigation.detail}".lower()
        for term in ("fixture", "placeholder", "the critic now reads"):
            assert term not in words, (
                f"{fixture.id}: a recommendation naming {term!r} describes this"
                " file rather than the claim"
            )
        if not fixture.expect.recommendation_sound:
            continue
        cited = set(draft.affected_element_ids)
        assert any(element_id.lower() in words for element_id in cited), (
            f"{fixture.id}: advice ruled sound that names none of"
            f" {sorted(cited)} is not advice about this claim"
        )


def test_the_set_carries_both_answers_about_a_recommendation():
    """Both halves of the rule, each with a row that proves it.

    **A plausible recommendation must not rescue an unsupported finding.** The
    negative fixtures carry advice that reads well, and the critic has to
    reject them for their own argument —
    :func:`test_no_mitigation_hands_a_negative_fixture_its_own_anchors` stops it
    reaching the anchors through the advice.

    **A flawed recommendation must not erase a valid threat.** That needs a row
    that survives while carrying one, and it is also what keeps the
    recommendation measure honest: where every readable fixture expects the same
    answer, a critic replying ``sound`` to all of them scores full marks without
    opening the block. ``ReplayScore.recommendation_informative`` reads the same
    fact at run time.
    """
    unsound = [entry for entry in ALL if not entry.expect.recommendation_sound]
    assert unsound, "no distractor: the set cannot tell reading from rejecting"

    readable = {
        entry.expect.recommendation_sound for entry in ALL if entry.expect.survives
    }
    assert len(readable) > 1, (
        "every fixture that can carry a reading expects the same answer, so the"
        " recommendation measure cannot tell a reading from a constant. Add a"
        " fixture that survives while carrying advice a reader ruled unsound"
    )


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
def test_no_mitigation_hands_a_negative_fixture_its_own_anchors(fixture):
    """A critic must not pass ``reason_engages`` by quoting the recommendation.

    ``_engages`` matches the anchors against the ruling's reason. An anchor
    spelled in the draft's own mitigation is one a critic can return without
    reading the source or the argument, which is the same bypass
    :func:`test_no_anchor_is_satisfied_by_naming_the_unknown_alone` refuses one
    step earlier.
    """
    draft = draft_of(fixture)
    words = " ".join(
        f"{mitigation.summary} {mitigation.detail}"
        for mitigation in getattr(draft, "mitigations", ())
    ).lower()

    for anchor in fixture.expect.reason_must_name:
        assert anchor.lower() not in words, anchor


def test_the_cli_resolves_its_critic_tier_through_the_one_reader():
    """The CLI's node name is the graph's, and `tier_of` is what takes it.

    Two spellings name this node: the graph's ``critic_<framework>`` and the
    tiers file's ``critic/<framework>``.
    :meth:`~analysis_service.deployment.Deployment.tier_of` is documented as the
    one place that walk is written, and asking ``tiers.resolve_tier`` with the
    graph spelling raises ``unknown LLM node`` — so the run dies at its first
    line, after a reader has already paid for nothing.

    Driven against a real deployment rather than a spelling assertion, so the
    two names are checked against each other and not each against its own idea.
    """
    from analysis_service.deployment import Deployment
    from evals.critic_review.__main__ import _critic_node

    env = {
        "ANALYSIS_MODEL_BASE_VENDOR": "openrouter",
        "ANALYSIS_MODEL_BASE_MODEL": "openai/gpt-5.6-terra",
        "ANALYSIS_MODEL_STRONG_VENDOR": "openrouter",
        "ANALYSIS_MODEL_STRONG_MODEL": "openai/gpt-5.6-terra",
        "ANALYSIS_MODEL_CHARGES_OPENROUTER": "direct",
    }
    deployment = Deployment.from_env(env=env)

    for framework in PACKAGES:
        node = _critic_node(framework)
        assert deployment.tier_of(node) in ("base", "strong", "review")
        assert node in deployment.tier_nodes
