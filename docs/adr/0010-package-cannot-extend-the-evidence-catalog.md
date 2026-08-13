# 10. A framework package selects from the evidence catalog; it cannot extend it

- **Status**: accepted
- **Date**: 2026-08-13
- **Effort**: [#165 — the evidence catalog stays the service's and a package
  selects from it](https://github.com/mstarks01/work-agent/issues/165), part
  of map [#158](https://github.com/mstarks01/work-agent/issues/158)
- **Amends**: [ADR 0004](0004-evidence-references.md) by addition. ADR 0004
  is not edited in place — it created the closed catalog and named no
  owner for it; this ADR names the owner now that a second framework
  package exists to ask the question of.
- **Relates to**: [ADR 0002](0002-finding-level-attribution.md), whose
  finding-level attribution this decision keeps intact for every framework —
  grounds stay required for every framework, so none is exempt.

## Context

Map #158 settled that STRIDE stops being this service's architecture and
becomes its first **framework package**, with more to follow. ADR 0004
built the Evidence Catalog — `evidence_catalog()`'s derivations, the
`Ground` model, its closed three-kind `GroundKind` — back when STRIDE was
the only caller, and named no owner for the catalog because there was no
second party to own it against.

#163 raised a concrete case for extension: an ASVS claim can rest on a fact
the input never states at all — a password policy the source text is silent
on — which is not an `unknown` attribute on any element, because the Valid
System Model carries no field for a password policy in the first place.
#163 asked #165 to decide whether that needs a new catalog entry, a new
derivation rule, or a fourth `Ground` kind.

## Decision

**No. The Evidence Catalog is the service's. A framework package selects
from it and may not add an entry, a derivation, or a `Ground` kind.** The
catalog grows only when the service grows it, and only for a derivation
that passes three tests:

1. it is a **pure function of the Valid System Model**;
2. it is **framework-neutral** — every lane of every framework receives the
   entry;
3. its ID is **built from IDs the model already carries**, so a reference
   resolves against the model the report already embeds.

A derivation that reads a framework's own requirement list fails tests 2
and 3 by construction — it isn't neutral, and there is no model-carried ID
to build it from.

**#163's case resolves without a new kind, because the premise was wrong,
not just the proposed fix.** #163's own reasoning already established that
every ASVS claim this service can emit is *undetermined* — never passed —
and that the ASVS record therefore carries no status field for a third
state, because one legal value is not a field. The same argument holds one
level down: undeterminedness is a property of the whole ASVS claim list, not
a per-claim fact that needs a ground to justify it. **Grounds justify
applicability, and never undeterminedness.** An absent fact — the input
never mentioning a password policy — grounds nothing, because there is
nothing for a ground to support: the claim is that the requirement's status
is unknown, not that some fact is true. The catalog owes no fourth kind for
it, and the eight-member `FrameworkPackage` contract (#164) gains no ninth
member from this ticket either — no `evidence` member, no catalog hook.

**What an ASVS lane actually cites is the existing three kinds.** A quote
for the stated fact that makes a requirement applicable; an
`unknown:<element-id>:<attribute>` entry where the bearing control attribute
is unstated; a `crossing:<flow-id>` entry for a requirement about a
boundary crossing. No new mechanism — the repo's own canonical tampering
exemplar already grounds on exactly this shape, citing a `crossing` and a
quote together.

## Consequences

**Every framework's claims are grounded through the identical seam.** A
`Claim.grounds` list (`min_length=1`, #163) resolves through one
`evidence_catalog()` regardless of which package produced the claim — the
property ADR 0004 built, that an agent selects and the service constructs,
holds for framework two exactly as it holds for framework one, because
there is only one construction path to hold it in.

**A framework with a genuinely new class of fact has one route, and it is
slow.** It argues the three tests against the service's own catalog and
accepts a `schema_version` bump if it wins. The catalog stays narrower than
the vocabulary around it — an ASVS presence test still reads free text on
`technology`, `protocol` and `authentication` — and this decision doesn't
close that gap; it just settles that a quote, not a new catalog entry, is
the answer when the gap is crossed.

**A related but separate gap surfaced while ruling on this, and is filed
off-ADR as [#171](https://github.com/mstarks01/work-agent/issues/171),
open.** `evidence_catalog()` tests exact equality against the literal
string `unknown`, while `control_state` reads a leading token and
distinguishes `unverified` / `absent` / `stated`. A control the input says
is **not there** (`absent`) currently has no Evidence Reference at all —
framework-neutral, present on `main` today, not caused by ASVS and not
fixable by a package. #171 owns whether and how the catalog's derivation
widens to cover it; this ADR's three-test rule is what any such widening
has to satisfy.

**What was considered and rejected: a constrained package-supplied
selector instead of a package-supplied entry.** Let a package declare a
*selector* over the model rather than a literal entry, with the service
still building the record — nothing about that shape can express a
conclusion on its face. It fails anyway: a selector that is a pure function
of the Valid System Model contains nothing that is the package's, so two
packages selecting the same fact give one fact two IDs or one ID two
meanings in a report that carries both; a selector that isn't a pure
function reads the framework's own requirement list, which is the failure
this decision already rules out. A package selector is either duplication
or a conclusion, rejected on both readings.
