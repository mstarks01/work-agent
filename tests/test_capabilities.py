"""The capability vocabulary and the three-valued applicability evaluator."""

from __future__ import annotations

import pytest

from analysis_service.capabilities import (
    ALWAYS,
    CAPABILITIES,
    CapabilityFact,
    Presence,
    evaluate,
    expression_issues,
    expression_keys,
    resolve,
)

BOTH = {"all": ["browser-frontend", "cookies"]}


def _facts(**states: Presence) -> dict[str, CapabilityFact]:
    return {
        key.replace("_", "-"): CapabilityFact(
            key.replace("_", "-"), state, (f"a:{key}",)
        )
        for key, state in states.items()
    }


@pytest.mark.parametrize(
    ("browser", "cookies", "expected"),
    [
        ("present", "present", "applicable"),
        ("absent", "present", "not-applicable"),
        ("absent", "absent", "not-applicable"),
        ("absent", "unknown", "not-applicable"),
        ("present", "absent", "not-applicable"),
        ("present", "unknown", "unknown"),
        ("unknown", "present", "unknown"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_all_reads_an_unknown_term_as_unknown_never_as_false(
    browser, cookies, expected
):
    facts = _facts(browser_frontend=browser, cookies=cookies)
    assert evaluate(BOTH, facts).state == expected


@pytest.mark.parametrize(
    ("browser", "native", "expected"),
    [
        ("present", "unknown", "applicable"),
        ("absent", "absent", "not-applicable"),
        ("absent", "unknown", "unknown"),
        ("unknown", "unknown", "unknown"),
    ],
)
def test_any_is_the_mirror_of_all(browser, native, expected):
    expression = {"any": ["browser-frontend", "native-client"]}
    facts = _facts(browser_frontend=browser, native_client=native)
    assert evaluate(expression, facts).state == expected


def test_silence_is_unknown_and_names_what_would_settle_it():
    decision = evaluate(BOTH, {})
    assert decision.state == "unknown"
    assert decision.missing == ("browser-frontend", "cookies")
    assert decision.deciding == ()


def test_a_negative_names_the_fact_that_ruled_it_out():
    facts = _facts(browser_frontend="present", cookies="absent")
    decision = evaluate(BOTH, facts)
    assert [fact.key for fact in decision.deciding] == ["cookies"]
    assert decision.deciding[0].evidence == ("a:cookies",)


def test_a_positive_names_every_fact_it_rests_on():
    decision = evaluate(BOTH, _facts(browser_frontend="present", cookies="present"))
    assert [fact.key for fact in decision.deciding] == ["browser-frontend", "cookies"]


def test_always_applies_and_names_no_fact():
    decision = evaluate(ALWAYS, {})
    assert (decision.state, decision.deciding, decision.missing) == (
        "applicable",
        (),
        (),
    )


def test_only_the_open_terms_are_missing():
    decision = evaluate(BOTH, _facts(browser_frontend="present"))
    assert decision.missing == ("cookies",)


def test_an_absent_parent_makes_every_descendant_absent():
    facts = resolve(_facts(oauth="absent"))
    for key in ("oauth-client", "oauth-resource-server", "oidc"):
        assert facts[key].state == "absent"
        assert facts[key].evidence == ("a:oauth",)
        assert facts[key].derived_from == "oauth"
    deep = resolve(_facts(webrtc="absent"))
    assert deep["media-recording"].state == "absent"


def test_a_present_child_makes_its_parent_present():
    facts = resolve(_facts(turn_server="present"))
    assert facts["webrtc"].state == "present"
    assert facts["webrtc"].evidence == ("a:turn_server",)
    assert facts["webrtc"].derived_from == "turn-server"
    assert facts["media-server"].state == "unknown"


def test_a_child_that_contradicts_its_parent_stays_as_stated():
    facts = resolve(_facts(oauth="absent", oauth_client="present"))
    assert facts["oauth"].state == "absent"
    assert facts["oauth-client"].state == "present"


def test_a_present_parent_says_nothing_about_a_child():
    assert resolve(_facts(webrtc="present"))["turn-server"].state == "unknown"


def test_resolve_answers_for_every_capability():
    assert set(resolve({})) == set(CAPABILITIES)
    assert all(fact.state == "unknown" for fact in resolve({}).values())


def test_every_parent_is_a_capability_and_no_lineage_loops():
    for key, capability in CAPABILITIES.items():
        seen = {key}
        parent = capability.parent
        while parent:
            assert parent in CAPABILITIES, (key, parent)
            assert parent not in seen, f"{key} loops through {parent}"
            seen.add(parent)
            parent = CAPABILITIES[parent].parent


def test_every_question_is_a_yes_or_no_question():
    for key, capability in CAPABILITIES.items():
        assert capability.question.endswith("?"), key
        assert capability.question.split()[0] in {
            "Do",
            "Does",
            "Is",
            "Are",
            "Can",
            "Must",
        }, key


def test_the_expression_grammar_refuses_what_it_does_not_define():
    assert expression_issues("oauth") == []
    assert expression_issues({"all": ["oauth", {"any": ["cookies", "cors"]}]}) == []
    assert expression_issues("oauth2")
    assert expression_issues({"none": ["oauth", "cors"]})
    assert expression_issues({"all": ["oauth"]})
    assert expression_issues({"all": ["oauth", "always"]})
    assert expression_issues({"all": ["oauth"], "any": ["cors"]})
    assert expression_issues(["oauth"])


def test_expression_keys_lists_each_key_once():
    expression = {"all": ["oauth", {"any": ["oauth", "cors"]}]}
    assert expression_keys(expression) == ("oauth", "cors")
    assert expression_keys(ALWAYS) == ()


def test_a_need_counts_each_unit_once_and_keeps_its_highest_band():
    """The band a capability question is ranked by (#1289)."""
    from analysis_service.capabilities import Decision, lineage, open_capability_needs

    child = next(key for key in CAPABILITIES if next(lineage(key), None))
    parent = next(lineage(child))
    decisions = {
        "low": Decision(state="unknown", missing=(child,)),
        "high": Decision(state="unknown", missing=(parent,)),
    }
    needs = open_capability_needs(decisions, {"low": 1, "high": 3}.__getitem__)
    assert needs[child].units == 1
    assert needs[child].band == 1
    assert needs[parent].units == 2, "a parent settles its child's unit too"
    assert needs[parent].band == 3
