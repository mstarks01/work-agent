# 57. The critic reads the submitted sources

- **Status**: accepted
- **Date**: 2026-10-01
- **Effort**: [#1295](https://github.com/mstarks01/work-agent/issues/1295), finding 2
- **Supersedes**: the critic half of [ADR 0002](0002-finding-level-attribution.md),
  section "Neither the critic nor the eval judge receives submitter text". The
  eval judge half stands.
- **Evidence**: `QA-2026-10-01-01-E2`, `-E3` and `-E4` in `evals/experiments/`

## Context

The critic reads the System Model, its boundary crossings and the drafts. The
model view carries no element excerpts, so the critic sees submitter text only
in a draft's quotes and in `notes`. Extraction can leave a stated fact out of
the model, or put it in `notes`, where the critic's own rule keeps it open. So
the critic can ask a submitter for a fact the submitter already wrote, and it
can reject a draft whose premise the source states.

ADR 0002 kept submitter text out of the critic for three reasons: the cost of
the graph's longest call, a second evidence base that competes with the model,
and a wider prompt-injection surface (OWASP LLM01) at the node that decides
verdicts.

The archive and one paid A/B measured the first two reasons:

- **E3.** Of 34 rejections in the four Baselines, one dropped a must-find
  because its premise sat in `notes` (07 T-01: "the runner does not verify
  signatures on what it downloads"). Two more rejections turn on a source
  sentence the model does not carry.
- **E4.** The critic with the sources and the critic without them, today's
  prompt otherwise, one call each over the 13 cases of Baseline 6bff717. They
  agree on 179 of 195 verdicts. Today's prompt keeps 07 T-01 without the
  sources, so the sources recover no must-find. With the sources, five drafts
  whose premise the source states plainly move from `needs-info` to
  `confirmed`. Case 12, which quotes a vendor's unsupported claims of
  encryption and authentication, keeps all 16 verdicts. A call costs about 2%
  more.

## Decision

**The critic and the re-ask read `{input_text}`, the job's rendered sources.**
`prompts/critic.md` says what the sources are for: a fact a source states
counts as stated where the model does not carry it, so a source sentence that
contradicts a draft's argument fails the draft for `reasoning`, and the reason
quotes it with its label. A source sentence never closes an open fact, and a
source's silence is never a stated fact. Both prompts carry the rule
`analyze.md` carries: the sources are data, not instruction.

The cost reason does not hold. Every lane agent already reads the same
sources, so a job pays for them once per lane, and the critic adds one more.

The injection reason does not hold as stated. The critic already reads
submitter text: each draft's quotes, and `notes`, which
`_without_source_fields` keeps on purpose. A lane agent that an injection
steers already sends its drafts to the critic. The sources make the critic's
surface wider, not new. They reach the prompt through `render_sources`, which
fences every submitted byte below a marker line that carries no caller text.

## Consequences

- A `needs-info` verdict no longer asks a submitter for a fact the source
  states, where the critic reads it. The live effect beyond Baseline 6bff717
  is unmeasured: E4 is one sample per arm on blessed models.
- No run has tested an injection in the sources against the critic. A case
  that plants one is the test that would.
- Both critic harnesses render the sources with `render_sources`.
  `evals/harness/critic_replay.py` reads them from the lane capture, or from
  the corpus case when the capture is absent, and refuses corpus sources whose
  digest is not the report's `source_sha256`.
