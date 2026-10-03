# 59. The critic rules each draft, and rejects none as a duplicate

- **Status**: accepted
- **Date**: 2026-10-03
- **Effort**: [#1438](https://github.com/mstarks01/work-agent/issues/1438)
- **Supersedes**: the critic's duplicate step and the `same_action_as` pairs
  that [#440](https://github.com/mstarks01/work-agent/issues/440) added to the
  critic's view.
- **Relates to**: [ADR 0031](0031-a-claim-identity-carries-no-direction.md),
  whose identity rule keyed the pairs.

## Context

The critic had three steps: evidence, lane and duplicate. For the third, code
marked each draft with the other drafts of the same lane, verb and place
(`same_action_as`), and the critic kept one of each group and rejected the
rest with `rejected_because` of `duplicate`.

Ledger rows `QA-2026-10-03-02-E2` and `E3` measured what that step did:

- In four Baselines, 11 draft keys held two drafts, and every one held two
  different findings: 5 impersonations in opposite directions, and 6 that
  differ by access path or channel.
- Every one of the 10 duplicate rejections in the archive dropped a different
  finding. One joined two lanes, which the prompt said is never a duplicate.
- Twice the dropped draft was the one that stated a must-find, and the scorer
  credited the kept draft, which the #890 ballot ruled `different`.

No archived duplicate rejection removed a true duplicate.

## Decision

**The critic rules every draft on its own.** The duplicate step is gone from
`prompts/critic.md`, the view carries no `same_action_as`, and
`ProposedVerdict` admits only `evidence`, `reasoning` and `lane`. A critic that
writes `duplicate` writes a value outside its schema.

**A report still reads `duplicate`.** `Verdict` keeps the value, and
`duplicate-on-unit` joins the archived kinds, because archived reports carry
both and are scored again.

**Which finding a claim is stays one function.** `critic.finding_key` keys a
claim by framework, lane, verb and folded place, or by its unit for a catalog
claim. `report_changes` reads it; nothing rejects by it.

## Consequences

A true duplicate now reaches the report twice. None appeared in the archive,
and a reader can see two near findings side by side, where a wrong rejection
hid one.

The live effect is unmeasured. The critic prompt and schema changed, so a
critic replay on archived drafts (about $1) can show how verdicts and
must-find matches move, and it needs its own approval.

## Framework parity

- **stride**: the step is removed for it, and it is where every measured
  duplicate rejection fell.
- **asvs**: nothing changes in practice, because a catalog claim carries no
  verb, so it was never paired, and a duplicate of a requirement is refused by
  identifier before the critic reads the set.
