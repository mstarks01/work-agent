# 16. The token caps are drift alarms, and the 6-8K envelope is retired

- **Status**: accepted
- **Date**: 2026-08-22
- **Effort**: [#276 — the 6-8K prompt envelope is circular, and two packages'
  text is unlinted](https://github.com/mstarks01/work-agent/issues/276)
- **Amends**: `.wayfinder/tickets/006-skills-sme-design.md`, which set the first
  caps and named the envelope. The ticket's other decisions stand: full skill
  text into the instruction, stable-first order, and a CI lint over the tree.

## Context

Every cap over the static instruction cited a **6-8K envelope**. The envelope
has no source but the caps that cite it.

Ticket 006 chose the caps — a category skill at 3K, a rubric at 1K, a pack at
2K — and then stated the envelope as a parenthetical: "(worst-case analyst
instruction ~6-8K)". The envelope is the sum of the caps that sentence had just
chosen. `prompts.py` and `tests/test_prompt_lints.py` then cited the envelope as
the constraint each cap must respect. The caps justify the envelope, and the
envelope justifies the caps. No model limit, no cost target and no measurement
enters the loop.

Nothing outside the loop binds at this size. `docs/Configuration.md` records
output ceilings of 128,000 tokens on `gpt-5.6` and `claude-opus-5`, and the
worst composed lane instruction is 5,734 tokens. The static text is also the
**cacheable prefix** — `prompts.py` and `graph.py` lay it out for exactly that,
and `report.py` reports `cached_prompt_tokens` to confirm the prefix caches. So
the tightest rule in the repo governed the text a cache serves, while the
job-varying block a provider re-reads per lane carried no such rule.

The loop cost four things.

**A cap stopped leaving room.** `prompts.py` said a cap is "sized to leave ~90
tokens — room for a normal edit without a CI fight". `critic.md` had 28 tokens
and `extract.md` had 67.

**A raise came to require a deletion.** The comments escalate: "argued for",
then "weighed against deleting something", then "there is no longer room for a
raise that does not come with one". The rule never asks whether the new text is
worth more than the text it displaces. It asks only that something leaves.

**Two packages' text went unlinted.** `tests/test_skill_lints.py` parametrized
over `STRIDE_CATEGORIES` and loaded `frameworks/stride`. ASVS's 17 lane skills
and 17 exemplar files had no token lint at all, and
`lanes/authentication/skill.md` reached 3,239 tokens with nothing watching it.
The lane-boundary digest test ran on STRIDE only; the ASVS digest sat at 1,980
tokens against a 2,000 budget. `output.md`, `critic.md` and `disclaimer.md`
carried no cap in any package. This is the failure `CLAUDE.md` names — a
constant per kind, read by a lint that walks one package's tree, is a gap that
opens the moment a second package arrives.

**The word collided.** `CONTEXT.md` defines **envelope** as the `Report`
envelope. The prompt comments used the same word for a token allowance.

## Decision

**A cap is a drift alarm. It rations nothing.** It makes a size change visible
in review and fails the lint when one file grows past what the alarm allows.
Raising a cap costs a one-line edit and needs no argument, because no
measurement in this repo says a shorter instruction finds more threats.
**Nothing has to leave to make room for new text.**

**The 6-8K envelope is retired**, along with every comment that argued a number
against it. `envelope` returns to its one `CONTEXT.md` meaning.

**One table, keyed by asset kind: `TOKEN_CAPS` in
`analysis_service.token_caps`.** Eight module constants across two modules become
thirteen entries in one dictionary. The package half is keyed by *kind* rather
than by package, so a framework nobody has written yet is already covered.

**One headroom rule, `alarm_at`: the largest shipped asset of a kind, plus a
tenth, rounded up to the next hundred.** Proportional rather than fixed, so the
alarm means the same thing on a 600-token file and a 3,200-token one. A fixed
allowance gives `repair.md` room for a paragraph and gives the ASVS
authentication chapter room for one sentence.

**The lint checks a band, not a value: `size <= cap <= 2 * size`.** The upper
bound is the other half of an alarm. A cap far above its content measures
nothing, which is the failure mode a mechanical raise would otherwise
introduce. A shrink therefore costs one line here, and that is the price of an
alarm that stays proportional to what it watches.

**`covered_assets` walks `PACKAGES` and raises on a file whose kind has no
key.** This is the registry check `CLAUDE.md` asks for. A table nobody compares
to its registry fails as quietly as the branch it replaced, so the comparison is
code: a new asset kind cannot arrive uncapped, and a package the registry names
cannot escape the lint.

**The composed cap is derived, not written down.** `COMPOSED_ANALYZE_CAP` is
the sum of the three part caps. The number it replaces was set by hand *below*
the sum of its parts, so it bound first and a body cap it could not accommodate
was a cap nothing could reach. A sum cannot do that.

**`notes/` and `cases/` keep their own caps**, in `tests/test_knowledge_lints.py`
and outside this table. Those files ride in the job-varying block, so their cap
answers *how much one lane may retrieve* rather than how far one file has
drifted. `JOB_VARYING_DIRS` names the exclusion in code, so it is a decision
rather than a silence.

## Consequences

**Every cap is now `alarm_at` over its largest shipped asset.** Room per kind,
against the largest file that kind holds:

| kind | largest | cap | room |
| --- | ---: | ---: | ---: |
| `prompts/analyze` | 3727 | 4100 | 373 |
| `prompts/critic` | 1472 | 1700 | 228 |
| `prompts/recritic` | 999 | 1100 | 101 |
| `prompts/extract` | 2383 | 2700 | 317 |
| `prompts/repair` | 610 | 700 | 90 |
| `package/critic` | 603 | 700 | 97 |
| `package/disclaimer` | 156 | 200 | 44 |
| `package/output` | 747 | 900 | 153 |
| `package/severity_rubric` | 790 | 900 | 110 |
| `package/lane_skill` | 3239 | 3600 | 361 |
| `package/lane_exemplars` | 1376 | 1600 | 224 |
| `package/lane_digest` | 1980 | 2200 | 220 |
| `domain/pack` | 686 | 800 | 114 |

Two of these caps do not move: `prompts/analyze` at 4100 and `prompts/recritic`
at 1100 are what the rule produces from their current size. The hand-argued
numbers were already close to the rule on the files that had been argued most
recently, which is why the rule is stated as it is rather than more loosely.

**53 package files are linted where 16 were.** 37 of them are ASVS's, which had
none. Both packages' lane digests are now checked, where only STRIDE's was.

**Three new capped kinds.** `output.md`, `critic.md` and `disclaimer.md` are
package members the gate already requires and no cap watched.

**A raise is now mechanical.** Call `alarm_at` over the new size, write the
answer into `TOKEN_CAPS`, and say in the commit message what the new text buys.
The commit message is where that reasoning belongs — a paragraph above a
constant recording why it moved from 3900 to 4100 is provenance in prose, which
`docs/agents/provenance.md` says drifts.

**What this does not decide is whether a longer instruction analyses worse.**
That is a real question and this ADR does not answer it. The claim the retired
envelope made — that a lane agent degrades somewhere above 6-8K — may well be
true. Nothing here measures it, and no instrument in `evals/harness/instruments.py`
reads prompt size, so a raise that improves findings and a deletion that costs
findings look alike to the sweep. Until an instrument measures it, a cap states
how far the text has moved and claims nothing about how well it works.

> **The instrument now exists** — `evals/harness/instruction.py`, added by
> [#278](https://github.com/mstarks01/work-agent/issues/278). It records each
> node's built instruction size and digest beside the scores in the same
> artifact, so two sweeps either side of a cap raise answer *which node moved,
> by how much, and what the numbers did*. Reading a trend out of that still
> needs sweeps on both sides; the reading is now possible, which it was not.
>
> **The comparison is now a command**, added by
> [#288](https://github.com/mstarks01/work-agent/issues/288):
> `harness compare <before> <after>` prints which node's instruction moved and
> by how much, then every instrument number that moved beside it. It is
> credential-free — both artifacts already hold what their nodes were told —
> and it concludes nothing. A score change smaller than the run-to-run spread
> `harness stability` measures is noise, so the answer still needs sweeps on
> both sides of an edit and more than one pair. What no longer stands in the
> way is the reading.
>
> **It also settled the envelope's last claim by measuring it.** The comments
> this ADR retired put the worst composed lane instruction at "~7.4K against a
> 6-8K envelope". The built number is **8,338 tokens** — `analyze_stride_repudiation`
> — and every one of STRIDE's six lane agents is above 7,900. The comments were
> adding a "~2.2K skill text" figure that stopped being true when the rubric and
> the lane skills grew. So the envelope was not merely circular: nothing had
> been inside it for some time, and no reading existed that would have said so.

**What was considered and rejected: keeping a hand-argued number per file.** It
is what produced the comment history above each constant, and that history is
the most careful writing in the repo. It also produced 28 tokens of room on
`critic.md`, a rule that a raise must come with a deletion, and 37 ASVS files
that no lint read — because a number a human argues is a number a human has to
remember to argue for the second package too. The reasoning moves to the commit
message and the table stays mechanical.
