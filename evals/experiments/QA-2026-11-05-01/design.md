# QA-2026-11-05-01 design: does the `attacker_position` bullet help the lane?

Fixed 2026-11-05, before the first call of E1. The maintainer granted $2.00
on 2026-11-05 for this run plus a later critic replay. This run takes at most
$0.95 of it.

## The question

#2141 (`46d9b132`) added the `attacker_position` bullet to
`frameworks/stride/output.md`. The bullet defines the five positions. No arm
measured it. The schema keeps the field required, and its type is the closed
set `AttackerPosition`, so a lane must state a position with or without the
bullet. The question: does the lane state the ruled position more often with
the bullet's definitions than without them?

## The arms

- **Treatment:** the bullet as on `main`. Stage 1 of QA-2026-11-03-01
  (`../QA-2026-11-03-01/runs/id-*.json`) serves as this arm. No lane input
  under `frameworks/stride` or `prompts/` changed since `d51830a1`, except the
  critic prompts of #2171. `request_digest.py` rebuilds each case's
  information-disclosure lane request offline, as `lane-replay
  --prepare-today` builds it. At `d51830a1` and at `ec6e8a1e` the 13
  instruction digests and the 13 user-turn digests are equal
  (`runs/request-d51830a1.json`, `runs/request-ec6e8a1e.json`).
- **Control:** `no-position-bullet.patch` on `ec6e8a1e`. It removes only the
  bullet. "Eleven fields" and the field list keep `attacker_position`, and
  the exemplars keep their stated positions. The patch is applied in a
  scratch worktree and is not committed. Against the treatment, the control
  request has an equal user turn and a different instruction on all 13 cases
  (`runs/request-control.json`).

## The run

`run_control.sh`: 13 `lane-replay --prepare-today` calls, one for each case
with a ruled information-disclosure reference, with the cases and catalog arms
of stage 1 (`signed`, and `none` for cases 14 and 15). Route: OpenRouter,
`openai/flex` upstream, strong tier `openai/gpt-5.6-terra`, through the local
tiers file, as stage 1 used. The runner stops before a call that could take
the total over $0.95 (the total plus $0.10), and it stops on a non-zero exit,
a missing charge, or a credit error. Expected cost: about $0.75, as stage 1.

## The measures

`score_arms.py` reads both arms through `score_stage.read_run` of
QA-2026-11-03-01, under the rulings on `main` at `ec6e8a1e`. These equal the
2026-11-03 rulings: the treatment re-scores at 17 agreeing of 21 reached ruled
references, 19 must-finds with the position rule and 23 without it, as
`id-rescore-2026-11-03.json`.

- **Primary:** the number of ruled references where a reaching draft states
  the ruled position (agreeing count).
- **Secondary:** must-finds matched by the shipped scorer, with the position
  rule; must-finds without the rule are reported beside it.

No class rule and no word rule on titles enters either measure. If a later
reading uses one, it is checked on the titles of both arms before any count
is read.

## The falsifier

"The bullet's definitions make the lane state the ruled position more often"
is refuted if the control's agreeing count is 17 or more. Then the
definitions add nothing measurable on this lane in one pass.

**Harm read:** the bullet harms coverage if the treatment matches 3 or more
fewer must-finds (with the position rule) than the control.

One pass in each arm gives a direction, not a rate. Either result leaves
`frameworks/stride/output.md` on `main` unchanged; the result goes to the
maintainer as the next decision.
