# 74. Each framework has its own question limit

- **Status**: accepted
- **Date**: 2026-10-09
- **Effort**: [#1562](https://github.com/mstarks01/work-agent/issues/1562)
- **Relates to**: [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md),
  whose limits this applies to each framework, and
  [ADR 0071](0071-the-floor-exception-reads-each-framework-s-own-bands.md),
  which removed the same coupling from the floor
- **Evidence**: `QA-2026-10-09-01-E1` in `evals/experiments/`

## Context

A paused job asks each kind of early question up to a limit of 30. The limit
was one count for the whole pause. With STRIDE and ASVS selected together, the
frameworks took turns for the 30 field places, and ASVS field questions took
places that STRIDE fills when it runs alone.

`QA-2026-10-09-01-E1` measured this on 223 archived STRIDE reports. Alone,
STRIDE asked a median of 30 questions. With ASVS beside it, STRIDE asked 27, and
55 reports lost 3 to 5 questions. In case 06, the 30 places held 18 questions
that both frameworks share, 7 STRIDE questions and 5 ASVS questions: the 5 ASVS
questions took the 5 places that STRIDE lost. ASVS lost no question.

## Decision

**Each framework has its own limit of each kind** (maintainer's decision of
2026-10-09). A question counts toward the limit of every framework it serves.
A round takes a new question while at least one of those frameworks has a
place left. An answer counts toward the limit of each framework its question
served, and toward every selected framework's limit where no list holds its
question (`answer_round.next_round`).

The floors, the number of questions a round asks and the turn order do not
change.

## Consequences

A framework asks the same questions with a second framework beside it as it
asks alone, apart from questions the two share, which the list ranks by their
summed score (ADR 0053).

A pause with two frameworks asks more questions in total. On the 223 STRIDE
reports with ASVS added, the median choices asked rose from 74 to 78, the most
questions asked rose from 60 to 65, and the longest pause rose from 6 rounds to
7. A third framework adds its own limit in the same way.

## Evidence

`test_a_second_framework_takes_no_place_from_the_first` in
`tests/test_answer_admission.py` fails before the change and passes after it.
An offline replay of the 254 archived reports, each alone and with both
frameworks, gives STRIDE the same count alone and joint in all 223 STRIDE
reports. ASVS asks the same or more in all 31 ASVS reports.
