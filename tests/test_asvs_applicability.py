"""ASVS's applicability table: one reviewed rule for each of the 345 requirements."""

from __future__ import annotations

from collections import Counter
from itertools import combinations

import pytest

from analysis_service.capabilities import (
    ALWAYS,
    CAPABILITIES,
    CapabilityFact,
    Presence,
    expression_keys,
    lineage,
    resolve,
)
from analysis_service.frameworks.asvs.applicability import (
    APPLICABILITY,
    RATIONALES,
    REVIEWED_BY,
    applicability_for,
)
from analysis_service.frameworks.asvs.catalog import REQUIREMENTS


def _known(**states: Presence) -> dict[str, CapabilityFact]:
    return {
        key.replace("_", "-"): CapabilityFact(key.replace("_", "-"), state, ("a",))
        for key, state in states.items()
    }


def test_every_requirement_has_exactly_one_row():
    assert list(APPLICABILITY) == [requirement.id for requirement in REQUIREMENTS]


@pytest.mark.parametrize(("level", "count"), [(1, 70), (2, 253), (3, 345)])
def test_each_level_selects_its_cumulative_set(level, count):
    assert len(applicability_for(level, {})) == count


@pytest.mark.parametrize("level", [1, 2, 3])
def test_silence_rules_nothing_out(level):
    states = Counter(
        decision.state for decision in applicability_for(level, {}).values()
    )
    assert states["not-applicable"] == 0


def test_an_unknown_names_the_capabilities_that_would_settle_it():
    for requirement, decision in applicability_for(3, {}).items():
        if decision.state == "unknown":
            assert decision.missing, requirement
        else:
            assert APPLICABILITY[requirement] == ALWAYS, requirement


def test_every_answer_is_deterministic():
    known = _known(oauth="present", oauth_client="absent", webrtc="present")
    assert applicability_for(3, known) == applicability_for(3, known)


def test_every_capability_has_a_reader_in_the_table():
    """A parent is read through its children: its answer settles theirs."""
    read = {
        key
        for expression in APPLICABILITY.values()
        for named in expression_keys(expression)
        for key in (named, *lineage(named))
    }
    assert read == set(CAPABILITIES)


def test_every_row_carries_a_rationale_and_a_review_field():
    assert set(RATIONALES) == set(APPLICABILITY) == set(REVIEWED_BY)
    assert all(isinstance(REVIEWED_BY[row], (str, type(None))) for row in REVIEWED_BY)


def test_no_oauth_rules_out_the_whole_chapter():
    decisions = applicability_for(3, _known(oauth="absent"))
    chapter = [row for row in decisions if row.startswith("V10.")]
    assert chapter
    for row in chapter:
        assert decisions[row].state == "not-applicable", row
        assert decisions[row].deciding, row


def test_oauth_present_leaves_only_the_role_questions_open():
    decisions = applicability_for(3, _known(oauth="present"))
    assert decisions["V10.1.1"].state == "applicable"
    assert decisions["V10.4.1"].missing == ("oauth-authorization-server",)
    assert decisions["V10.5.1"].missing == ("oauth-client", "oidc-relying-party")


def test_an_oauth_client_is_not_asked_the_authorization_server_rules():
    known = _known(oauth_client="present", oauth_authorization_server="absent")
    decisions = applicability_for(3, known)
    assert decisions["V10.2.3"].state == "applicable"
    assert decisions["V10.4.1"].state == "not-applicable"
    assert decisions["V10.6.1"].state == "not-applicable"


def test_a_clients_code_flow_does_not_apply_the_servers_code_flow_rules():
    """#1468: each term of a conjunction names one party, through its parent."""
    known = _known(
        client_code_flow="present",
        oidc_relying_party="present",
        oauth_authorization_server="present",
    )
    decisions = applicability_for(3, known)
    assert decisions["V10.2.1"].state == "applicable"
    assert decisions["V10.5.1"].state == "applicable"
    assert decisions["V10.4.2"].missing == ("server-code-flow",)
    assert decisions["V10.6.1"].missing == ("oidc-provider",)


def test_each_token_fact_sits_under_the_party_it_describes():
    known = _known(self_contained_token_issuer="absent")
    known |= _known(self_contained_token_consumer="absent")
    facts = resolve(known)
    assert (
        facts["token-validity-period"].derived_from == "self-contained-token-consumer"
    )
    assert facts["shared-signing-key-audiences"].derived_from == (
        "self-contained-token-issuer"
    )


#: The conjunctions ADR 0063 lets read the whole application, by decision.
WHOLE_APPLICATION_CONJUNCTIONS = frozenset(
    {
        *(f"V3.3.{n}" for n in range(1, 6)),
        "V3.5.5",
        "V3.6.1",
        "V7.1.3",
        "V7.4.4",
        "V7.5.3",
        "V7.6.1",
        "V7.6.2",
        "V14.3.1",
        "V14.3.2",
    }
)


def _unrelated_pairs(expression):
    """Each pair of an ``all``'s terms where neither is the other's ancestor.

    An ``any`` inside it lends each alternative as a term, and the alternatives
    are never paired with each other, because only one of them has to hold.
    """
    if isinstance(expression, str):
        return
    ((operator, terms),) = expression.items()
    if operator == "all":
        keys = [term for term in terms if isinstance(term, str) and term != ALWAYS]
        alternatives = [
            [key for key in term["any"] if isinstance(key, str)]
            for term in terms
            if not isinstance(term, str) and "any" in term
        ]
        pairs = list(combinations(keys, 2))
        pairs += [
            (key, other) for key in keys for group in alternatives for other in group
        ]
        yield from (
            (a, b) for a, b in pairs if a not in lineage(b) and b not in lineage(a)
        )
    for term in terms:
        yield from _unrelated_pairs(term)


def test_a_conjunction_names_one_party_through_its_parent():
    """ADR 0063: a conjunction outside its list pairs a child with its parent."""
    unrelated = {
        unit
        for unit, expression in APPLICABILITY.items()
        if any(_unrelated_pairs(expression))
    }
    assert unrelated == WHOLE_APPLICATION_CONJUNCTIONS


def test_the_scan_rule_reads_only_whether_untrusted_files_are_sent_on():
    """Files sent on to another system count, so no download rules it out."""
    for state in ("present", "absent"):
        decisions = applicability_for(3, _known(file_download=state))
        assert decisions["V5.4.3"].missing == ("untrusted-file-download",)


def test_an_assurance_policy_applies_only_to_the_party_it_names():
    known = _known(
        idp_assurance_policy="present",
        oauth_resource_server="present",
    )
    decisions = applicability_for(3, known)
    assert decisions["V6.8.4"].state == "applicable"
    assert decisions["V10.3.4"].missing == ("resource-server-assurance-policy",)


def test_webrtc_without_turn_keeps_the_rest_of_the_chapter():
    decisions = applicability_for(3, _known(webrtc="present", turn_server="absent"))
    assert decisions["V17.1.1"].state == "not-applicable"
    assert decisions["V17.2.1"].state == "applicable"
    assert decisions["V17.2.2"].state == "unknown"


def test_a_missing_control_never_rules_out_its_own_requirement():
    # V16.3.1 asks that authentication be logged: it applies because the
    # application authenticates, whatever the input says about logging.
    decisions = applicability_for(2, _known(authentication="present"))
    assert decisions["V16.3.1"].state == "applicable"
    # V6.3.3 asks for a second factor, so its absence cannot rule it out.
    assert "multi-factor-authentication" not in expression_keys(APPLICABILITY["V6.3.3"])


def test_the_browser_chapter_leaves_a_machine_to_machine_api():
    decisions = applicability_for(3, _known(browser_frontend="absent"))
    chapter = [row for row in decisions if row.startswith("V3.")]
    ruled_out = [row for row in chapter if decisions[row].state == "not-applicable"]
    assert ruled_out == [row for row in chapter if row not in {"V3.2.1", "V3.4.2"}]
