# 12. The evidence catalog carries a stated absence, as a fourth ground kind

- **Status**: accepted
- **Date**: 2026-08-14
- **Effort**: [#171 — the evidence catalog enumerates one of the three control
  states](https://github.com/mstarks01/work-agent/issues/171), filed off-map
  while resolving [#165](https://github.com/mstarks01/work-agent/issues/165)
- **Amends**: [ADR 0004](0004-evidence-references.md) by addition — its closed
  catalog and its "the agent selects, the service constructs" property are
  unchanged; what changes is which facts the enumeration reaches.
- **Relates to**: [ADR 0010](0010-package-cannot-extend-the-evidence-catalog.md),
  which named the service the catalog's owner and set the three tests any
  widening must pass. This is the first widening argued against them.

## Context

`evidence_catalog()` enumerated an attribute by testing exact equality with the
string `unknown`. The analysis layer never did: `control_state` reads the
attribute's *leading token* and returns one of three states — `unverified`,
`absent` or `stated` — and `is_unverified` is the union of the first two, which
11 of STRIDE's 12 candidate rules fire on.

Two facts therefore had no **Evidence Reference** at all:

- a control the input says is **not there** — `authentication: "none; accepted
  by network position"`;
- a control someone **hedged** — `"unknown; possibly a shared group account"` —
  because the string is not exactly `unknown`, though `CONTEXT.md` has always
  defined **Unknown** to include a voiced hedge.

Measured on the 12 corpus cases, which hold 251 control-attribute instances:
187 exactly `unknown`, 1 hedged, 8 stated absent, 55 stated. **Coverage counted
196 and the catalog offered 187** — `build_coverage`'s denominator is
`unknown_controls`, which keeps every instance whose state is not `stated`, so
9 instances were facts Coverage reported a lane had not cited and the catalog
never let it cite. Of the 300 candidates the rules produce across the corpus,
**18 fire on a control the input stated absent**.

The repo's own canonical tampering exemplar showed the workaround: it rests on
a flow carrying `authentication: none` and `encryption_in_transit: none`, and
grounded itself on the crossing plus the quote *"not authenticated and not
encrypted"*. Legal, and taught by `analyze.md` — and it spends a quote, which
has to pass the pinned ladder in `stride_service.grounding`, where a derived
fact would have resolved by set membership.

## Decision

**The catalog enumerates a stated absence, under a new `absent:` reference and
a fourth `GroundKind`, `absent-attribute`.** One classifier — `control_state`,
the same one the rules read — now decides every attribute entry, so the catalog
and the analysis layer can no longer disagree about what an attribute says.

Against ADR 0010's three tests: the derivation is a **pure function of the
Valid System Model** (one attribute's leading token); it is **framework-neutral**
(every lane of every framework receives the entry, and nothing in it names a
method); and its ID is **built from IDs the model already carries**
(`absent:<element-id>:<attribute>`, the `unknown:` scheme exactly).

**A fourth kind rather than a flag, because the two attribute branches carry
identical fields and different facts.** An unknown is a question — the claim
resting on it is conditional and routes to `needs-info` — while a stated
absence is the answer, and the claim resting on one is not conditional. `kind`
is the only place that difference can live, and a consumer folding the two
would report a control the input described as missing as a gap in the
*description*.

**The absent half is confined to the five `CONTROL_ATTRIBUTES`; the unverified
half stays over every type-specific attribute.** The asymmetry is the honest
reading rather than an oversight: `unknown` is the extraction sentinel and
means "the input never settled this" on every field, decorated with a hedge or
not, while `none` carries a determinate meaning only where the attribute names
a control. "The submitter said this is not there" is not a fact about a
`protocol` or a `data_description`, and an entry asserting it would be a row an
agent could rest a finding on. Measured on the corpus, all 80 non-control
attributes reading a `none`/`unknown` leading token are the bare sentinel, so
this rule costs nothing observed today and closes a seam that would otherwise
open on the first model that writes one.

**No `schema_version` bump of its own.** A fourth member of a closed enum is a
breaking change — a consumer switching over three kinds now meets a fourth —
and it would have earned a major bump had it arrived alone. It rides 3.0, which
has never shipped, because two hard cutovers for one release is a cost paid
twice for nothing.

## Consequences

**The catalog and the coverage denominator finally count the same facts.** On
the corpus the catalog grows from 306 entries to 315 — 8 stated absences and
the 1 hedged unknown — and its control-attribute half is now exactly the 196
`unknown_controls` counts. The 2.9% growth is the measurement that made this
cheap: for contrast, an entry for every *stated* type-specific attribute would
grow the catalog 169%, to 823, and take the rendered table from 1.0–4.0 KB to
about 3–11 KB on every lane's instruction.

**A quote is no longer the only way to cite a stated absence.** The canonical
tampering exemplar drops its quote and cites the two `absent:` rows beside the
crossing — one fact filed once, resolving by set membership rather than through
the quote ladder. That is the shape agents are now taught, and `analyze.md`
says so in the grounding step.

**`analyze.md` costs ~160 more static tokens**, and its cap moves 3900 → 4100
with the composed budget 5300 → 5500. The worst lane now sits at ~5.4K of that
budget and the worst composed instruction around 7.2K against a 6–8K envelope.
The file's own rule — the next thing wanting static room is weighed against
deleting something — was applied here and produced one deletion, and there is
no longer room for a raise that does not come with one.

> **Amended by [ADR 0016](0016-the-token-caps-are-drift-alarms.md).** The cap
> named here is now the `prompts/analyze` entry of `TOKEN_CAPS`, the composed
> budget is derived from its parts, and the 6-8K envelope is retired. The rule
> this paragraph applies — that a raise comes with a deletion — is withdrawn.
> The deletion it records still stands on its own merits: the tampering draft
> cites the rows rather than re-quoting the sentence behind them.

**An `absent-attribute` ground does not license an empty `mitigations` list.**
`MissingMitigation` reads `unknown-attribute` only, and deliberately: the
prompt licenses an empty list where nothing can be recommended before a fact is
learned, and a control the submitter said is missing is a fact already in hand.

**What this does not do is judge.** An entry still asserts only *this fact is
in the validated system representation*. `authentication` being stated absent
is not a spoofing finding, exactly as `authentication` being unknown was never
one; whether either participates in a credible attack stays the agent's
argument and the critic's to rule on.

**What was considered and rejected: reusing `unknown-attribute` with a widened
derivation.** It is the smaller diff — no enum member, no renderer branch, no
version note — and it destroys the distinction the fix exists to serve. Every
consumer would read "never stated" over a control the submitter had explicitly
described as absent, `analyze.md`'s conditional-writing rule would fire on
findings that are not conditional, and the needs-info route would collect
claims that need no information. The catalog would have gained the fact and
lost what it says.
