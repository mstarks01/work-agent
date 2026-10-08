# 71. The floor exception reads each framework's own bands

- **Status**: accepted
- **Date**: 2026-10-08
- **Effort**: [#1542](https://github.com/mstarks01/work-agent/issues/1542) (F2, F)
- **Relates to**: [ADR 0053](0053-a-paused-job-asks-in-bounded-rounds.md),
  whose floors and highest-band exception this narrows, and
  [ADR 0068](0068-a-pause-says-what-it-leaves-open.md), which left the
  exception for its own decision

## Context

A capability question passes its floor where it settles at least two units.
It also passes where it sits in the highest band of its kind and the kind's
questions span more than one band. That exception read the bands of every
selected framework together.

The audit of 7 October 2026 asked for a policy decision on the exception: a
single-unit level 1 question passes in a level 2 ASVS job, but not in a level 1
job. A test with a third framework package then showed a second effect. With
ASVS level 1 alone, 24 capability questions that only ASVS asks sit under the
floor. With a package of three bands selected beside it, every one of them
passed. One framework's bands decided another framework's floor.

## Decision

**The exception reads each framework's bands apart** (maintainer's decision of
2026-10-08). A capability question carries the band it has for each framework
it serves (`EarlyQuestion.framework_bands`). It passes by the exception where,
for one of those frameworks, that framework's questions of the kind span more
than one band and this question is in that framework's highest band.

The floor itself and the summed score of a shared question do not change. So
a question that two frameworks share still passes where their units together
reach the floor (ADR 0053).

**The level 1 and level 2 difference inside ASVS stays.** A level 2 job ranks
level 1 above level 2, so the band tells its questions apart. A level 1 job has
one band, so the floor alone decides. The questions under the floor are listed
under "More questions" and take an answer (ADR 0068).

## Consequences

An ASVS job asks the same capability questions whatever other framework is
selected, apart from questions it shares, whose summed score can pass the
floor.

A framework with one band never passes a question by the exception. A
framework added later gets the exception only from its own bands.

## Evidence

`test_another_package_s_bands_do_not_move_a_framework_s_floor` in
`tests/test_future_frameworks.py` fails before the change and passes after it.
The ASVS-only reproduction of #1542 (F2) gives the same counts before and
after.
