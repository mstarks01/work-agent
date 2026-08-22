# 8. The local corpus is retrieved by fired rule, not by query

- **Status**: accepted
- **Date**: 2026-08-11
- **Relates to**: [ADR 0004](0004-evidence-references.md), whose evidence catalog
  stays the closed set a finding may cite and which nothing here joins, and
  [ADR 0006](0006-two-exemplar-systems.md), whose exemplars carry the method
  while a **Worked Case** carries a judgement.

## Context

#127 asks for a local security knowledge base and a curated case library,
retrieved selectively rather than copied wholesale into prompts. It also warns,
in its own non-goals, against "a complex vector/RAG infrastructure without
demonstrated need" and against letting reference documentation serve as finding
evidence.

Those two constraints pull against the obvious implementation. The reflex for
"retrieve relevant documents" is an embedding model and a similarity search over
a query built from the submission — which is nondeterministic across index
rebuilds, untestable offline, and takes caller-controlled text as its query.
That last point is the serious one: the submission is attacker-influenced input,
and a query built from it is a lever on which repo-authored text enters the
agent's context (OWASP LLM01).

The repository already had the selector this needed and had not noticed.
`candidates.py` fires a table of structural rules against the validated System
Model, and a rule firing is a precise statement about what a lane is looking at
— far more precise than a bag of words from the submission, and computed
entirely from validated state.

The measurement problem is unchanged from #116 and #122: **no live eval lane in
this repository has ever run**, so whether retrieved text improves an analysis
cannot be answered here. What can be answered is whether the right documents
arrive, deterministically, and whether they can leak into evidence.

## Decision

**Retrieval is a set intersection against the rules that fired, and that is the
whole mechanism.** Two closed tables in `stride_service/knowledge.py` map each
document to the rule IDs that select it; a lane gets the documents its own fired
rules name, ranked by how many named them, with declaration order as the
tie-break. No index, no query, no scoring function, no embedding model.

Three properties fall out of that rather than being engineered on top:

- **Progressive disclosure is the default case.** A lane that fired nothing
  retrieves nothing, which is most lanes on most models. No job carries
  reference material about a condition nobody's model exhibits.
- **Caller text selects nothing.** Selection reads rule IDs, which come from
  code, against a closed table. No submitted byte reaches the composed text
  through this path — the same argument that already covers **Domain Packs**.
- **Two runs over one model send byte-identical instructions.** Ordering is
  fixed in source, so retrieval cannot be the reason two otherwise identical
  jobs differ.

**Two document kinds, differing in standing rather than subject.** A
**Reference Note** is security reference on one condition, of the same standing
as a Domain Pack. A **Worked Case** is a judgement about another system —
pattern, threat considered, ruling, why, what decided it. Cases exist because an
exemplar is a finished draft and therefore always ends in a threat; "investigate
and reject", which the candidate design calls the system working, had no worked
example anywhere in the tree.

**The corpus is knowledge and can never become evidence.** It is stated in the
prompt, restated in every note's own Guardrails section where it is actually
read, and structural besides: the document ID space (`notes/<id>`, `cases/<id>`)
cannot collide with an **Evidence Reference** (`unknown:…`, `crossing:…`), and
`knowledge.py` is imported by `graph.py` alone — not by `evidence.py`, which
builds the citable set, and not by `critic.py`, which resolves what a finding
rests on. A test pins that importer set, so the seam has to be opened
deliberately rather than drifted into.

**Capped per lane, not per corpus.** Six agents retrieve independently, so a
document's cost is paid up to six times on one job. Two notes and one case per
lane, 700 tokens each, keeps the worst lane at or under the domain-pack block
already beside it.

**Curated, never learned.** Nothing writes back into `knowledge/` from a run.
Cases are authored and reviewed like any other file in the tree, because a
library that ingested its own output would let one model's mistake become the
next model's reference.

## Consequences

**Coverage of the corpus is bounded by the rule set.** A condition no rule fires
on cannot retrieve anything, however well written the note. That is the intended
trade — it is what buys determinism and the LLM01 argument — but it means the
corpus grows with `candidates.py` rather than independently, and
`test_knowledge_lints.py` fails CI when the two drift in either direction.

**The static prompt cost is ~57 tokens**, one clause in the Input section and
two sentences fixing the standing of the new blocks;
`ANALYZE_PROMPT_TOKEN_CAP` moves 3450 → 3550. The standing sentences are what
earn their place rather than the pointer: a retrieved note reads exactly like
the System Model until something says it is not a fact about this system, and a
case ending in a rejection reads like an instruction to reject until something
says it is somebody else's reasoning.

> **Amended by [ADR 0016](0016-the-token-caps-are-drift-alarms.md).** The
> constant named here is now the `prompts/analyze` entry of `TOKEN_CAPS`, and the
> 6-8K envelope it was argued against is retired. A cap states how far the
> text has drifted and rations nothing.

**Whether any of this improves an analysis is unmeasured, and stays unmeasured
until a sweep runs.** The tests here establish that the right documents arrive
and that they cannot become evidence, and stop exactly there. Retrieval
usefulness, as #127 defines it, needs the live lane that has never executed.

**What was considered and rejected: embedding the corpus and searching it.**
It buys recall over documents no rule points at, and costs determinism, offline
testability, and the property that caller text selects nothing. Worth revisiting
only when a sweep can show the fired-rule mapping is the thing limiting recall —
which is an argument no one can currently make from data.

**Also rejected: retrieval as a callable tool on the agent.** ADK supports tools
alongside `output_schema`, so this was available. Every document this design
retrieves is selectable before the agent runs, and pre-computing costs one pass
instead of a round trip per question, keeps the instruction byte-identical
across jobs, and cannot fail halfway.

**Also rejected: a default document for lanes that fired nothing.** It would put
the whole corpus into every job by another route, which is the outcome the
design exists to avoid.
