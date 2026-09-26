# 30. Every assessment unit ends somewhere

- **Status**: accepted.
- **Date**: 2026-09-09; amended 2026-09-25
- **Effort**: [#739 — a bounded coverage pass that recovers what a lane omitted](https://github.com/mstarks01/work-agent/issues/739),
  finding 4 of the audit on [#732](https://github.com/mstarks01/work-agent/issues/732)
- **Builds on**: [ADR 0026](0026-coverage-is-per-framework.md), which counts
  what a lane cited; [ADR 0028](0028-a-draft-states-its-direction.md), which
  gives an ASVS draft a direction
- **Measured in**: `QA-2026-09-24-03-E1` and `-E2`, `QA-2026-09-25-01-E8`,
  `QA-2026-09-26-01-E2`, `-E4` and `-E5`

## Context

The graph runs each lane once, merges, and reviews. The **Critic** may not add a
claim the lanes missed, move a misfiled one, or improve one; the re-ask repairs
malformed rulings only. So one enumeration by one lane agent is the only
discovery opportunity a job has, and a valid but short answer ends the search.

A lane is offered **assessment units**, which are its own package's. For a
package whose claims compose an identity from an action and a place, a unit is
one **candidate** or one **boundary crossing** in one lane, never every
element: the first Baseline offered each STRIDE lane 223 elements and the lanes
cited about half, so a unit per element is a second pass by another name. For a
package whose claims rule on a catalog, the neutral `units_for` hook already
names them: the selected requirements.

A lane leaves many units unanswered. On Baseline 6bff717, 153 of 324 candidates
and 111 of 258 crossings are cited by no draft, summed over lanes, about 20
units a case. The coverage instrument counts this, and its own docstring says a
lane that cleared a unit and one that skipped it look alike.

## Decision

**A lane addresses every assessment unit it is offered, in its first call, and
files a finding that holds only if an open fact is true as a conditional
draft.** The lane request states this in one instruction, and the lane reads it
**last**: it is the final part of the lane's user turn, from the package's
`LANE_CLOSING_DOC` entry. STRIDE carries one; ASVS carries none, because its
scope table already gives every selected requirement an entry. A unit ends either in
a claim that cites it or in the lane's judgement that the sources settle
against a finding there; the second leaves no record.

This is a rule for the lane's first and only call. No second call follows, and
no list of cleared units is written. The critic reads the drafts as it does
today, and an open fact makes a draft conditional, never rejected.

## Consequences

**Recall rises on the leads the lane passed by.** One pass of twelve captured
lane requests with the instruction added wrote 4 of 13 must-finds the archived
runs lost, against none archived (`-E8`). Two of the four had never appeared in
any earlier run: case 07 ref 1 in the reference's direction, and case 12 ref 14.

**Output rises with it.** The same twelve lanes wrote 59 drafts with the
instruction and 26 without. Each extra draft is work for the critic and for a
reviewer, and nothing yet measures how many of them are real. The rule is
adopted only where its recall gain clears the band at a draft cost the
measurement below states.

**The rule changes a prompt that every lane reads.** Two plausible prompt edits
in this repository measured worse at corpus level. That is why the measurement
below reads the whole corpus rather than the rows the instruction was tried on.

## Alternatives considered

**A bounded second call on the unanswered units.** One more call per lane,
carrying the lane's first drafts and the units no draft cited, asking for new
drafts only. Rejected on measurement. The ceiling is 8 must-finds, the ones
whose place sits on an unanswered unit at the scorer's grain, not the 33 a
count of every `place` and `unled` row suggested (`QA-2026-09-24-03-E1`).
Against a plain repeat of the lane at the same cost, the second call won on 1
row of 4 and tied on 3 (`-E2`). It recovers a miss that is chance, which a
repeat also recovers. It does not change a place or a direction the lane chooses
every time.

**A `cleared` mark per unit.** One sentence per unit on why nothing follows,
counted beside `cited` in coverage and never scored. Not adopted. A cleared mark
proves attention and not correctness, it adds output to every lane call, and
nothing measured shows it recovers a finding.

**Repeat the whole lane prompt.** Two full passes merged. It recovers the same
chance misses as the second call at twice the lane cost, and it is the control
the second call failed to beat.

**Let the critic add findings.** Rejected: the critic prompt's first rule is
that it rules and does not write, and a critic that writes is a third lane with
no candidates and no exemplars.

**A structural lint on coverage.** Rejected: it is what the coverage instrument
already is, and it recovers nothing.

## Measurement

On 30 captured lane requests that held 31 missed must-finds, the instruction
recovered 8 and 8 by meaning over two passes, and a plain repeat of the same
requests recovered 3 and 4 (`QA-2026-09-26-01-E2`, a blind maintainer ballot).
It wrote about 75% more drafts.

**Where the lane reads it decides whether it works.** As the last section of
`output.md`, before the lane's exemplars, the same words raised lane proposals
18% and no target row reached a report (`-E4`). As the last part of the user
turn, the full graph on cases 02, 05 and 10 nearly doubled lane proposals,
the critic rejected none, and must-finds by structure rose from 13 to 15
(`-E5`). A lane call costs about 20% more.

**Not yet measured:** the holdout cases, and what a reader makes of a report
about twice as long, most of it conditional. The next full sweep reads the
holdout split.
