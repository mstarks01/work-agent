"""Candidate-trigger recall over the real corpus, per framework.

Credential-free and deterministic, so it gates on every PR: each package's
rules and the blessed models are all it reads.

**Per framework, never pooled.** A rule belongs to whichever package declares
it, so one combined figure divides one package's firings by another's
references. That is not hypothetical: pooling STRIDE's 194/243 with ASVS's
20/62 gives 70%, which clears STRIDE's floor while hiding that ASVS's
deterministic layer sees a third of what its reference set says applies.
"""

from pathlib import Path

import pytest

from evals.harness.reference import load_corpus
from evals.harness.triggers import by_framework, case_trigger_recall, corpus_recall
from stride_service.frameworks import PACKAGES

CORPUS = Path(__file__).resolve().parents[1] / "evals" / "corpus"

#: Floors per framework, and **floors are not targets**.
#:
#: STRIDE was measured at 0.81 must-find / 0.78 overall across 224 references
#: when the rule table landed. The gap to 1.0 is threats that turn on what a
#: submitter *said* rather than on the model's shape, which have no structural
#: trigger by construction and should not acquire one. Each floor sits ~10
#: points below its measurement so a rule rewrite has room to trade one lead for
#: a better one, and a collapse — a rule silently matching nothing after a
#: schema change — still fails.
#:
#: **ASVS's numbers are low and that is a finding, not a calibration.** Its
#: predicates are presence tests (#160), which fire on a shape being *there*
#: rather than on the shape a requirement is about, so a rule can fire all over
#: a model and never on the element its own reference record names. The floor is
#: recorded so the number cannot quietly fall further while #218 is open.
#:
#: Measured 2026-08-18 at 0.37 must-find / 0.32 overall over 62 scoreable
#: records, and 2026-08-19 at 0.34 / 0.32 over 98, when four cases gained
#: reference sets (#236). The floors do not move for that. A corpus that grows
#: by a third moves the measurement for a reason unrelated to rule quality, and
#: a floor chasing each re-measurement stops being able to catch the collapse it
#: is for.
#:
#: Measured 2026-08-21 at **0.48 / 0.42** when the six ASVS chapters with no
#: rule got one. Unlike the two readings above, this one moved for a reason that
#: *is* rule quality, so the argument for holding the floor is weaker here — but
#: it is one measurement, and raising a floor is a policy decision rather than a
#: consequence of a change. Recorded, not chased.
TRIGGER_FLOORS: dict[str, dict[str, float]] = {
    "stride": {"must_find_recall": 0.70, "recall": 0.65},
    "asvs": {"must_find_recall": 0.27, "recall": 0.23},
}

#: Lanes whose rules fire on no reference claim anywhere in the corpus, with the
#: reason. **Debt, not an exemption** — the way ``tests/test_case_review.py``
#: frames its list, and unlike ``UNEXERCISED`` in ``test_rule_coverage.py``,
#: where an entry says the omission is right.
#:
#: Note what this is *not* saying: ``test_rule_coverage.py`` passes with an
#: empty exemption list, so every one of these rules does fire on some blessed
#: model. They fire somewhere and not on the elements the reference records
#: name, which is the sharper finding and the one only this module can make.
#: **Four lanes left this list** when the six chapters that had no rule at all
#: got one: ``authorization``, ``configuration``, ``secure-communication`` and
#: ``security-logging-and-error-handling`` now draw leads on their own reference
#: records. ``secure-coding-and-architecture`` and ``webrtc`` gained a rule and
#: stayed, which is the distinction this module exists to make — the first fires
#: on models but not on the elements its records name, and the second fires
#: nowhere at all and says so in ``UNEXERCISED``.
UNTRIGGERED_LANES: dict[str, str] = dict.fromkeys(
    (
        "cryptography",
        "secure-coding-and-architecture",
        "self-contained-tokens",
        "validation-and-business-logic",
        "webrtc",
    ),
    "asvs: the chapter's rule fires on some model but never on an element one of"
    " its own reference records names. Tracked by #218.",
)

#: Cases where a package's whole reference set draws no structural lead. Debt on
#: the same terms.
#:
#: **Empty.** ``04-ml-inference-service`` was the one entry: none of its ten
#: ASVS records drew a lead, because the case is a model-inference service whose
#: requirements land in chapters that had no rule at all. Giving those chapters
#: rules is what emptied this list, which is the clearest evidence the six were
#: worth writing — a whole case moved from drawing nothing to drawing leads.
UNLED_CASES: dict[tuple[str, str], str] = {}


@pytest.fixture(scope="module")
def results():
    return corpus_recall(load_corpus(CORPUS))


@pytest.fixture(scope="module")
def totals(results):
    return by_framework(results)


@pytest.mark.parametrize("framework", sorted(TRIGGER_FLOORS))
def test_trigger_recall_holds_its_floor(totals, framework):
    measured = totals[framework]
    floors = TRIGGER_FLOORS[framework]
    assert measured["must_find_recall"] >= floors["must_find_recall"], measured
    assert measured["recall"] >= floors["recall"], measured


def test_every_framework_the_corpus_declares_is_measured(totals):
    """A package scored by nothing is the failure #218 was filed for."""
    declared = {
        name
        for case in load_corpus(CORPUS)
        for name in (entry.name for entry in case.meta.frameworks)
    }
    assert set(totals) == declared == set(PACKAGES)


def test_every_case_gets_some_structural_lead(results):
    """A reference set where nothing fires means the rules cannot read that shape."""
    unled = sorted(
        (result.framework, result.case_id)
        for result in results
        if result.total and not result.triggered
    )
    undeclared = [entry for entry in unled if entry not in UNLED_CASES]
    assert not undeclared, (
        f"these reference sets draw no structural lead at all: {undeclared}."
        " Either a rule reads a shape the case does not carry, or the case's"
        " records are about something the candidate layer cannot see — add it to"
        " UNLED_CASES with which."
    )


def test_every_lane_is_triggered_somewhere_in_the_corpus(results):
    """A lane no rule ever fires in is a lane the candidate layer skipped."""
    triggered = {
        (result.framework, hit.lane)
        for result in results
        for hit in result.hits
        if hit.triggered
    }
    silent = sorted(
        f"{framework}/{lane}"
        for framework, package in PACKAGES.items()
        for lane in package.lanes
        if (framework, lane) not in triggered and lane not in UNTRIGGERED_LANES
    )
    assert not silent, f"no reference claim in these lanes draws a lead: {silent}"


def test_the_debt_lists_do_not_rot(results):
    """A lane or a case that starts drawing leads has to leave its list."""
    triggered = {
        (result.framework, hit.lane)
        for result in results
        for hit in result.hits
        if hit.triggered
    }
    revived = sorted(
        lane
        for framework, package in PACKAGES.items()
        for lane in package.lanes
        if lane in UNTRIGGERED_LANES and (framework, lane) in triggered
    )
    assert not revived, f"these lanes now draw leads and are still listed: {revived}"

    led = sorted(
        key
        for result in results
        if result.triggered
        and (key := (result.framework, result.case_id)) in UNLED_CASES
    )
    assert not led, f"these cases now draw leads and are still listed: {led}"


def test_a_claim_naming_no_element_is_excluded_by_name(results):
    """ "Fired on an element it is about" has no answer for a claim naming none.

    Counting those as misses reports the rules failing at a question nobody
    asked them; counting them as hits inflates the rate. One ASVS record is in
    this position, and it is reported rather than absorbed.
    """
    unscoreable = sum(result.unscoreable for result in results)
    assert unscoreable == 1
    assert all(hit.element_ids for result in results for hit in result.scoreable)
    assert sum(result.total for result in results) + unscoreable == sum(
        len(result.hits) for result in results
    )


def test_a_miss_is_recorded_rather_than_hidden(results):
    """The metric names what it did not see; a silent drop would flatter it."""
    misses = [
        hit for result in results for hit in result.scoreable if not hit.triggered
    ]
    assert misses
    assert all(hit.rule_ids == () for hit in misses)


def test_scoring_is_stable_across_calls():
    case = load_corpus(CORPUS)[0]
    assert case_trigger_recall(case, "stride") == case_trigger_recall(case, "stride")
    assert case_trigger_recall(case, "asvs") == case_trigger_recall(case, "asvs")
