# Three-condition diagnostic

This protocol finds where an important finding is lost, and whether a
correction of that loss improves the final report. It compares three
conditions. Each condition changes one input and keeps the other inputs fixed.

It is a branch of the audit, not a required sweep. Take it only through the
entry test below. Each step can stop the protocol with a diagnosis or a
blocker, and both of those are good results.

## Entry test

Take this protocol only when **both** of these are true:

- The free checks (`SKILL.md` step 4) leave important final-report misses
  without an explanation.
- The evidence you have cannot choose between these causes:
  - the source does not carry the information;
  - extraction lost or misattached a relevant fact;
  - the System Model, its projection, preparation or a consumer lost usable
    information;
  - analysis did not develop a supported security argument;
  - fan-in, criticism or reporting dropped a correct finding;
  - the reference or the matcher is wrong.

Before step 1, read what earlier work already settled:

```bash
uv run python -m evals.harness.run experiments --signature "<the miss>"
uv run python -m evals.harness.run bottleneck
uv run python -m evals.harness.run oracle
```

`bottleneck` and `oracle` are the results of #1033. They already split
extraction loss from representation loss on every signed case, at no cost.
#1003's arm comparison is the facts-first evidence. `run.py compare-arms`
re-reads it. If these already answer the question, report the answer and stop
here.

## 1. Qualify the target and freeze the baseline

For each miss you sample, decide whether it is distinct, essential, in scope,
and inferable from the supplied source. Write the minimum acceptable security
argument for it:

- the attacker action;
- the affected target;
- the mechanism and its preconditions;
- the consequence;
- the uncertainty or disposition the source supports.

An unstated control is not a confirmed absence.

Freeze these judgements and their acceptable variants **before** you read any
condition's output. Propose a reference correction as a separate change. Keep
the original denominator and score beside the revised ones. Never remove a
hard target to raise coverage.

Choose a small diagnostic set: misses with different failure signatures, plus
cases that succeed, plus a negative control. A set that you chose for its
misses supports a diagnosis. It does not estimate overall quality. Keep fresh
cases back for confirmation (#1093).

State the baseline with its run identity, metric, denominator, scorer and
reference version (`SKILL.md` step 3). A figure from an earlier thread is not a
baseline. For example, "roughly 41%" was reference coverage, not must-find
coverage.

**Done when** every sampled miss has a frozen argument, the set names its
negative control, and the baseline names all five identity parts.

## 2. Run the three conditions

| Ledger `condition.name` | Input | What it asks | How it runs today |
| --- | --- | --- | --- |
| `current-pipeline` | the model that the shipped route extracts, through the normal consumers | what does the shipped system achieve? | `run --mode end-to-end` |
| `corrected-extraction` | the signed `model.json`, through the same preparation and consumers | how much of the loss does a corrected extraction recover? | `run --mode analysis` (#742 pairs this with the row above) |
| `direct-facts` | the signed `model.json` **and** every signed `facts.json` row as an assertion row the lanes can cite, so a fact reaches analysis whether or not the System Model has a field for it | does giving analysis the facts directly recover what `corrected-extraction` still lost? | `run --mode direct-facts`; `lane-replay` for one lane at the analysis stage |

**Build verified input from the whole source**, never backwards from the
expected findings. Where you can, have somebody who has not seen the expected
findings or the observed misses build it. Record what the builder saw in
`condition.exposure`.

**No answer may reach generation.** No expected finding text, reference ID,
must-find label or hint derived from an answer may appear in an input.
`python evals/verify_corpus.py` fails a case whose `model.json` or
`facts.json` carries a reference claim sentence, its rationale, a requirement
identifier or a tier label. It cannot detect a paraphrase, so exposure is still
a fact you record.

**Keep the unrepresentable facts.** For `corrected-extraction`, list each source
fact the System Model cannot hold in `condition.unrepresentable`. Never drop
one, and never force one into the wrong field. `direct-facts` keeps them in
`facts.json`, so this difference is part of the intervention, and it must be
visible.

**Hold everything else fixed.** Use the same model and settings, the same
source, instructions, output limits and downstream handling. Record every
adapter or prompt difference in `condition.adapters`. Meter the actual usage:
a shared output limit gives neither equal cost nor equal context load.

**What `direct-facts` changes, exactly.** The `assert` node answers with the
case's signed rows instead of a model call, and every later stage runs as it
ships: the resolver, the gate, the projection, the evidence catalog, the lanes,
the critic and the report. The lanes still read the System Model. So this
condition tests whether facts the model cannot hold help analysis when they
arrive as rows. It does not test analysis with no model at all. Record that
difference in `condition.adapters`. The mode refuses a case with an unsigned
row, and it records no served build for `assert`, because no provider
answered.

Paid calls need the user's permission, as `SKILL.md` "Money" says. Without
permission, write the experiment and its cost estimate and stop.

**Done when** each condition you ran has a ledger row with a full `condition`
block, and each condition you could not run has a `blocked` row that names
its blocker.

## 3. Review the outcomes and trace where each finding went

Judge the findings for meaning, not only by identity. Where you can, judge them
without knowing the condition. Record who judged, what assistance they had,
where they disagreed, and what stays unresolved. A judgement with model
assistance is not independent human ground truth.

For each condition, report:

- correct important final findings over eligible reviewed references;
- unsupported or contradicted conclusions, disposition errors and duplicates;
- valid findings outside the reference set, reviewed separately;
- review burden: the measured time, or a proxy that you label as a proxy;
- cost and latency, and independent repeats where the spread can change the
  decision.

Read each finding at the analysis output and at the final report. Where the
artifacts allow it, follow it through preparation, fan-in, criticism and
reporting. Name the earliest observed loss and every other contributing loss.
`unknown` is an attribution.

Review the unmatched outputs as well as the apparent matches. If you sample,
state the sampling method and the denominator: a selection is not whole-report
precision. A union of repeated runs is not single-run recall.

**Done when** every sampled miss has a stage for its earliest loss (or
`unknown`), and every figure states its denominator.

## 4. Read the result

| Observation | What it supports | The next test that discriminates |
| --- | --- | --- |
| `corrected-extraction` beats `current-pipeline` | a corrected extraction recovers findings under these conditions | narrow repairs of fact, attachment or scope, then a replay of what they affect |
| `direct-facts` beats `corrected-extraction` | a bypass of representation and consumption helps | narrower substitutions that separate schema expressiveness, adapter loss, preparation or routing, and prompt or input format |
| all three miss | a corrected extraction alone does not fix these misses | whether analysis wrote the argument at all, then where it stopped surviving |
| a correct candidate disappears downstream | a downstream stage contributes | inject the same reviewed candidate at each boundary in turn |
| a semantic review finds a valid output that scoring missed | the measurement contributes | fix the reference or the matcher on its own, with before and after kept |

**What the table does not license:**

- `direct-facts` beating `corrected-extraction` does **not** show that STRIDE or
  the System Model schema is the limit. A bypass also changes salience, token
  load, order, routing and instructions. Say "a broad bypass effect" until a
  narrower substitution shows a schema-specific mechanism.
- A null result does not show that two conditions are equal. Report the sample
  size, the variation and the scope that you tested.
- A good score with oracle help shows what is achievable with those inputs. It
  is not a formal upper bound, and it does not show that automatic extraction
  can reach it.
- A synthetic preservation test shows local behaviour. It does not show how
  often the loss occurs in real reports, or that the schema has no effect at
  generation time.

**Done when** the report states what is established, what stays uncertain,
and the smallest useful next action.

## 5. Confirm an improvement

Only an authorised, isolated change goes past diagnosis. Verify its local
mechanism cheaply. Then measure the benefit to an ordinary end-to-end report,
and confirm it on fresh, frozen cases with independent repeats at a declared
budget (#1093).

**Oracle-assisted conditions never qualify a promotion of the route.**
`corrected-extraction` and `direct-facts` read signed material. A promotion
reads the shipped route on material the change never saw.

**One exception: a gate that isolates one flag.** A gate that holds the signed
model fixed to compare one switch on and off may read `analysis` mode, because
the fixed model is its control, and not a claim about the route. The
`ANALYSIS_ASSERTIONS` gates in `evals/harness/promotion.py` (#926) are the case
the maintainer accepted on 2026-09-24. Such a gate decides the flag only. A
claim that the whole route improved still needs the ordinary pipeline on fresh
cases.

Declare beforehand what gain is material and which trade-offs are acceptable. A
recall gain with more unsupported output, or with too much review burden, is
not by itself a quality gain. A claim of more than 90% must name the
population, the semantic review criteria, the denominator, the variation per
case, the repeatability and the uncertainty. Treat 90% as a provisional
feasibility target against a named, reviewed reference population. It is not a
promise and it does not replace report quality. A claim that the service beats
human analysts needs its own controlled comparison.
