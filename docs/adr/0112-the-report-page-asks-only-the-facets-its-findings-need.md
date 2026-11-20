# 112. The report page asks only the facets its findings need

- **Status**: accepted
- **Date**: 2026-10-30
- **Effort**: [#2029](https://github.com/mstarks01/work-agent/issues/2029) (package 1)
- **Relates to**: [ADR 0073](0073-a-finding-can-wait-on-some-facets-and-an-answer-can-carry-a-detail.md),
  whose `needs` this reads, and
  [ADR 0081](0081-a-question-kind-is-asked-once-for-the-system.md), whose
  shared answer this limits

## Context

ADR 0073 lets a finding name the facets of a question kind that it needs. The
coverage count, a report's conditions and the follow-up read those facets. The
report page still showed one input for each facet of the kind. So a finding
that needs only the request size showed four inputs: rate, size, concurrency
and quota.

Over the 304 archived reports that the current schema reads, the report page
showed 1,197 facet inputs, and the findings needed 794 of them. `capacity-limits`
held 97 of the 215 questions that can show fewer inputs.

## Decision

**A page asks the facets that a waiting finding needs.**
`fact_answers.asked_facets` is the one reader of the rule. It takes the union
of the facets that each waiting finding needs. A finding with no entry in
`needs` needs every facet, and so does a fact that no finding waits on. Each
question that a page reads carries the result as `asks`:
`FactQuestion.to_json`, `EarlyQuestion.to_json` and the rows of earlier answers.
An early question and an earlier answer have no waiting finding, so each asks
every facet.

**The page shows the other facets under "Additional details".** A facet table
shows the facets that its rows ask and each facet that already holds an answer.
A button shows the rest. A facet left out stays unknown, and a later finding
that needs it asks it again.

**The counts read the same facets.** A facets row is known when each facet it
asks has an answer other than "I don't know". The "answers reach" line and the
skip of a part-answered row read that. The coverage count already read each
finding's own facets. A shared answer to a facet writes only the rows that
show the facet, so it never answers a facet that a row does not ask.

The service admits a facet answer in part, as before. Its admission does not
read `asks`, so the page cannot narrow what the service accepts.

## Consequences

`follow-up-policies` counts the effort two ways, `asked` and `whole`. Over 273
archived STRIDE reports, the whole follow-up list asks 4,469 choices instead of
4,872, and all 4,490 conditional findings stay complete. At 5 choices the
shipped order completes 2,029 findings instead of 1,957, and at 20 choices
4,213 instead of 4,038. ASVS reports do not change. These are counts of
dependency sets, not of findings that an answer changes.

The follow-up order (`follow_up_order`) charges a finding the facets it needs
when it compares the findings nearest to completion. Over the same 273 STRIDE
reports this moved the completed findings by at most two at any budget (2,029
to 2,031 at 5 choices, 4,213 to 4,212 at 20), and ASVS did not move: the
order reads the same facets as the page, with no measured gain.

The live effect on owners is unmeasured. Source-to-facet binding (#1347) stays
deferred: a facet that a source states is not shown as known.

## Evidence

`TestTheFacetsAPageAsks` in `tests/test_facet_dependencies.py` and in
`tests/test_webapp_questions.py`, `TestThePageAndTheServiceCoverAlike` in
`tests/test_webapp_questions.py`, and
`test_the_nearest_finding_counts_only_the_facets_it_needs` in
`tests/test_questions.py`.
