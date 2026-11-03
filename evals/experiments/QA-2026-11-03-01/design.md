# QA-2026-11-03-01 design, fixed before the first call

Written 2026-11-03T16:01:45Z, before any paid call of this audit.

## Question

Does the STRIDE information-disclosure lane state the attacker position that
the maintainer ruled (#2145) on the references its drafts reach?

## Stage 1 (granted, cap $1)

One `run.py lane-replay --prepare-today signed --lane information-disclosure`
call on each of the 13 cases with a ruled information-disclosure reference:
01, 02, 03, 04, 05, 06, 09, 10, 11, 12, 13, 14, 15. Cases 14 and 15 have no
`facts.json`, so they run with `--prepare-today none`, and the report shows
them apart (amended 2026-11-03T16:02Z, before any call, after an offline
prepare of each case). The calls run from a clean worktree of `main`. The runner stops before a call that could take the
reported total over $0.90.

## Definitions

- **Drafts:** the lane's proposals after the fan-in, as `lane_must_finds`
  builds them.
- **Reached reference:** a ruled information-disclosure reference that at
  least one draft of its case matches by the scorer's verb-and-place rule with
  the position gate off (`_match_in_lane` on the case with no ruled
  positions, own and ruled readings).
- **Agreement:** a reached reference agrees when at least one draft that
  reaches it states the ruled position. The agreement rate is agreeing
  references over reached references, summed over the 13 cases.
- **Fill:** the proposals the lane returned, and the proposals in the invalid
  list whose fault names `attacker_position`.
- **Coverage:** must-finds of the lane matched by `score_case` with the ruled
  positions and without them.

## Falsifier

The claim "the lane states the ruled position" is refuted if the agreement
rate is under 70%.

## Decision for stage 2 (each stage 2 needs its own approval)

| Stage 1 result | Stage 2 |
|---|---|
| Agreement 85% or more | Tampering, elevation-of-privilege and repudiation lanes on the cases with ruled references in them: 21 calls, about $0.99 |
| Agreement 70% to under 85%, or fewer than 10 references reached | A second information-disclosure pass on the same 13 cases: 13 calls, about $0.61 |
| Agreement under 70% | No stage 2; the claim is refuted |
| 10% or more of the proposals are invalid for `attacker_position` | No stage 2; the output contract has a fault |

The last row comes first: a contract fault stops the plan whatever the
agreement.

## Stage 2, fixed 2026-11-03T16:15Z, before any stage 2 call

Stage 1 gave 17 of 22 reached references agreeing. All five misses are ruled
`outside` and are a read of a store through its own access, where the lane
stated `storage-copy`. The maintainer accepted a fix and its measurement in
place of the table's second pass ("Recommendation accepted and you may spend
$1 more.").

**The fix:** one sentence after the `storage-copy` definition in
`frameworks/stride/output.md`, commit `d9931ded` on the branch
`exp/position-read-contrast`: "A read through the store's own access, such as
a grant, a credential or an open port, is `outside`, and `storage-copy` is
only media, a backup or a snapshot taken away from the store."

**The run:** the same 13 calls, cases and arms as stage 1, from a clean
worktree of `d9931ded`. The runner stops before a call that could take the
reported stage 2 total over $0.90.

**The falsifier:** the fix fails if either holds:

1. The `outside` references reached agree at a rate of 2 in 7 or less, as in
   stage 1.
2. Any reached reference ruled `storage-copy`, `on-path`, `in-component` or
   `authorized-principal` does not agree. Stage 1 had 15 of 15.

The definitions of stage 1 apply unchanged.

## Stage 3, fixed 2026-11-03T16:45Z, before any stage 3 call

Stage 2 left 4 of the 5 stage 1 misses unchanged: 02/6, 04/8, 11/10 and
12/8. The drafts show the cause: the lane writes no draft for an attacker who
holds a grant on the store, and every `read` of a store in the exemplars is a
storage copy or a compromised reader. The maintainer approved a measurement of
a contrast exemplar ("$1 approved").

**The fix:** exemplar 5 of `frameworks/stride/lanes/information-disclosure/exemplars.md`,
commit `615b1885` on the branch `exp/position-grant-exemplar`, made from
`main` without the stage 2 sentence: a `read` of `store:accounts-db` by an
attacker who holds the database password or another grant, with
`attacker_position` `outside`, in its own contrast section.

**The run:** the same 13 calls, cases and arms as stage 1, from a clean
worktree of `615b1885`. The runner stops before a call that could take the
reported stage 3 total over $0.90.

**The falsifier:** the exemplar fails if either holds:

1. Fewer than 2 of the 4 repeated misses (02/6, 04/8, 11/10, 12/8) agree.
2. Any reached reference ruled `storage-copy`, `on-path`, `in-component` or
   `authorized-principal` does not agree. Stage 2 had 16 of 16.

The number of proposals is reported beside stage 1 (132) and stage 2 (140)
as a cost, not as a test. The definitions of stage 1 apply unchanged.
