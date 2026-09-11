"""The stated-control diagnostic, and the corpus figure its module publishes."""

from __future__ import annotations

import logging
from unittest import mock

import pytest

from analysis_service import basis
from analysis_service.analysis import CONTROL_ATTRIBUTES, matches_term
from analysis_service.basis import (
    CONTROL_SCOPE,
    IN_SCOPE,
    MAX_SCAN_WORK,
    content_tokens,
    stated_controls,
    unbased_controls,
)
from analysis_service.system_model import SystemModel
from evals import verify_corpus
from evals.harness.reference import load_case

# The figures published in the module docstring's table. Re-derived below, so
# the prose and the code cannot drift apart.
CORPUS_VALUES = 22
CORPUS_FLAGGED_WEAK = 0
CORPUS_FLAGGED_STRICT = 16


def _model(**flow) -> SystemModel:
    """A two-element model whose one flow carries the values under test."""
    defaults = {
        "protocol": "HTTPS",
        "authentication": "unknown",
        "data_description": "orders",
        "encryption_in_transit": "unknown",
    }
    return SystemModel.model_validate(
        {
            "external_entities": [
                {
                    "id": "entity:shopper",
                    "name": "shopper",
                    "kind": "human",
                    "trust_zone": "boundary:public",
                }
            ],
            "processes": [
                {
                    "id": "process:api",
                    "name": "api",
                    "technology": "python",
                    "trust_zone": "boundary:public",
                    "exposure": "internet-facing",
                    "interface_kind": "web",
                }
            ],
            "data_stores": [],
            "data_flows": [
                {
                    "id": "flow:shopper-to-api:place-order",
                    "name": "place order",
                    "source": "entity:shopper",
                    "destination": "process:api",
                    "source_label": "System description",
                    **defaults,
                    **flow,
                }
            ],
            "trust_boundaries": [
                {
                    "id": "boundary:public",
                    "name": "public",
                    "kind": "network",
                }
            ],
            "assumptions": [],
        }
    )


WORKED_EXAMPLE = {"System description": "The API sends payment requests to Stripe."}


def test_the_worked_example_is_flagged_on_every_invented_control():
    """#465's example: two controls asserted from a sentence that states neither."""
    model = _model(authentication="OAuth 2.0", encryption_in_transit="TLS 1.3")

    flagged = unbased_controls(model, WORKED_EXAMPLE)

    assert [flag.attribute for flag in flagged] == [
        "authentication",
        "encryption_in_transit",
    ]
    assert flagged[0].tokens == ("oauth", "2.0")
    assert flagged[0].source_label == "System description"


def test_a_value_sharing_one_word_with_its_source_passes():
    """The weak rung: one content token is enough, and the rest is wording."""
    model = _model(authentication="OAuth 2.0 bearer token issued by the broker")
    sources = {"System description": "Callers present a bearer credential."}

    assert unbased_controls(model, sources) == []


def test_a_token_matches_at_the_start_of_a_word():
    """Through ``matches_term``, so ``encrypt`` reaches ``encrypted``."""
    model = _model(encryption_in_transit="encrypt in transit")
    sources = {"System description": "Traffic is encrypted between the two."}

    assert unbased_controls(model, sources) == []


def test_a_sentinel_states_no_control_and_is_never_flagged():
    """``unknown`` and ``none`` assert nothing, so there is no basis to want."""
    unknown = _model(authentication="unknown; nobody said")
    absent = _model(authentication="none; the endpoint is open")

    assert unbased_controls(unknown, WORKED_EXAMPLE) == []
    assert unbased_controls(absent, WORKED_EXAMPLE) == []


def test_a_value_of_function_words_alone_is_not_flagged():
    """Nothing to look for is not the same fact as looking and not finding.

    ``FUNCTION_WORDS`` is frozen at the measurement, so it is an ordinary
    English closed-class list and not a complete one: a value whose only
    content token is a word the list happens to omit is flagged. That costs a
    diagnostic line on a value that names no mechanism, and it is the price of
    a list nobody tuned against the sample it reports on.
    """
    model = _model(authentication="it is the same as that")

    assert unbased_controls(model, WORKED_EXAMPLE) == []


def test_an_element_citing_a_source_the_job_never_carried_is_passed_over():
    """The validity gate refuses that shape; this reports on it a second time."""
    model = _model(authentication="OAuth 2.0")

    assert unbased_controls(model, {"Some other label": "anything"}) == []


def test_a_version_number_survives_as_one_token():
    assert content_tokens("TLS 1.3") == ("tls", "1.3")
    assert content_tokens("OAuth 2.0") == ("oauth", "2.0")


def test_single_characters_and_function_words_drop_out():
    assert content_tokens("a shared key, X, for the fleet") == (
        "shared",
        "key",
        "fleet",
    )


def test_every_control_attribute_is_declared_in_or_out_of_scope():
    """The table against its registry: a sixth control attribute answers here.

    Read with ``.get`` so the guard still runs while an attribute is half
    added — a collection-time lookup would make the missing entry an import
    error in the module whose message names it.
    """
    undeclared = [
        attribute for attribute in CONTROL_ATTRIBUTES if attribute not in CONTROL_SCOPE
    ]

    assert undeclared == [], (
        f"{undeclared} are control attributes with no CONTROL_SCOPE entry: say"
        " whether the diagnostic measures each one, and why"
    )
    assert IN_SCOPE == (
        "authentication",
        "encryption_in_transit",
        "encryption_at_rest",
    )


def test_an_out_of_scope_attribute_states_its_reason():
    assert all(
        CONTROL_SCOPE[attribute] is None or CONTROL_SCOPE[attribute].strip()
        for attribute in CONTROL_ATTRIBUTES
    )


def _corpus_values() -> list[tuple[str, tuple[str, ...], str]]:
    """Every stated in-scope control the corpus carries, with its cited source.

    One row per value: the case, the value's content tokens, and the lowered
    text of the source it cites. Walked through
    :func:`~analysis_service.basis.stated_controls`, which is the same reader
    :func:`~analysis_service.basis.unbased_controls` walks, so the published
    denominator and the published rates cannot be computed over two different
    sets of values. Only the rung differs between them: ``any`` here, ``all``
    below.
    """
    rows = []
    for case_dir in verify_corpus.case_dirs():
        case = load_case(case_dir)
        sources = {source.label: source.text for source in case.sources}
        lowered = {label: text.lower() for label, text in sources.items()}
        for control in stated_controls(case.model, sources):
            rows.append((case.id, control.tokens, lowered[control.source_label]))
    return rows


@pytest.fixture(scope="module")
def corpus_values() -> list[tuple[str, tuple[str, ...], str]]:
    return _corpus_values()


def test_the_published_denominator_is_what_the_corpus_holds(corpus_values):
    assert len(corpus_values) == CORPUS_VALUES


def test_the_published_weak_rung_rate_is_what_the_corpus_gives(corpus_values):
    """0 in 22 false rejections, the figure the module's table publishes.

    A flag on a blessed value is a false rejection: the corpus is the closest
    thing to ground truth here. Read it beside the module's own caveat — 22 is
    a small sample and no case has been read by a person (#226).
    """
    flagged = [
        case_id
        for case_id, tokens, source in corpus_values
        if not any(matches_term(token, source) for token in tokens)
    ]

    assert len(flagged) == CORPUS_FLAGGED_WEAK, flagged


def test_the_published_strict_rung_rate_is_what_the_corpus_gives(corpus_values):
    """16 in 22, which is why the strict rung is recorded and not shipped."""
    flagged = [
        case_id
        for case_id, tokens, source in corpus_values
        if not all(matches_term(token, source) for token in tokens)
    ]

    assert len(flagged) == CORPUS_FLAGGED_STRICT, flagged


def test_the_corpus_runs_clean_through_the_shipped_reader():
    """The rule a job runs, over the same corpus, agrees with the weak rung.

    Two readers of one rung otherwise: the loop above applies ``any`` to the
    rows, and :func:`unbased_controls` applies it inside a budgeted scan. They
    are tested against each other rather than each against its own expectation.
    """
    flagged = []
    for case_dir in verify_corpus.case_dirs():
        case = load_case(case_dir)
        sources = {source.label: source.text for source in case.sources}
        flagged.extend(unbased_controls(case.model, sources))

    assert flagged == []


def test_the_corpus_spends_a_fraction_of_the_scan_budget():
    """The worst case is 440 times under the bound, which is what the constant says.

    Re-derived rather than asserted in prose: the budget is a number somebody
    will want to lower, and this says what lowering it would cost.
    """
    worst = 0
    for case_dir in verify_corpus.case_dirs():
        case = load_case(case_dir)
        sources = {source.label: source.text for source in case.sources}
        scans = {
            (token, control.source_label)
            for control in stated_controls(case.model, sources)
            for token in control.tokens
        }
        worst = max(worst, sum(len(sources[label]) for _, label in scans))

    assert worst == 45_448
    assert worst * 400 < MAX_SCAN_WORK


def test_the_scan_stops_at_the_budget_and_says_so(caplog):
    """A model that spends the budget is unmeasured from there on, not accused.

    The values below share no word with the source, so an unbudgeted scan flags
    every one of them. The budget admits two searches and the rest read as
    echoed, because a diagnostic that ran out of work must not start accusing.
    """
    source = "quaternary " * 200
    model = _model(authentication="zeta eta theta iota", encryption_in_transit="kappa")

    with caplog.at_level(logging.WARNING, logger="analysis_service.basis"):
        flagged = unbased_controls(model, {"System description": source})
        with mock.patch.object(basis, "MAX_SCAN_WORK", 2 * len(source)):
            budgeted = unbased_controls(model, {"System description": source})

    assert [flag.attribute for flag in flagged] == [
        "authentication",
        "encryption_in_transit",
    ]
    assert budgeted == []
    assert "the rest of this model is unmeasured" in caplog.text


def test_one_token_is_searched_once_however_many_values_carry_it():
    """The memo, which is what takes the 150-element worst case off 13 seconds.

    Measured through the budget rather than through a call count: three values
    naming the same token spend one token's worth of it.
    """
    source = "quaternary " * 200
    model = _model(authentication="zeta", encryption_in_transit="zeta")

    with mock.patch.object(basis, "MAX_SCAN_WORK", len(source)):
        flagged = unbased_controls(model, {"System description": source})

    assert [flag.attribute for flag in flagged] == [
        "authentication",
        "encryption_in_transit",
    ]
