# 130. A quote repair that moves a negation or a number is refused

- **Status**: accepted
- **Date**: 2026-11-18
- **Amends**: [ADR 0018](0018-the-repair-rung.md), whose rung kept a span that
  changed a negation or a number and marked it.
- **Relates to**: [ADR 0100](0100-the-extraction-gate-repairs-an-excerpt-before-it-refuses.md),
  which refuses such a span at the extraction gate; #2270.

## Context

The repair rung replaces a refused quote with the nearest span of the source
the quote names. A span inside the similarity threshold can still state the
opposite fact: "does encrypt" and "does not encrypt" differ by one word. ADR
0018 kept such a span in the fan-in and marked it with `moved`, so that the
critic could reject the claim. The extraction gate already refused it (ADR
0100). An offline reproduction showed the fan-in rewrite a quote that was
verbatim in another source into its named source's opposite, at a similarity
of 0.976.

## Decision

A span that changes a negation or a number is refused in the fan-in as it is
at the extraction gate. `grounding.faithful_repair` is the one reader of
whether a refused quote may take the source's span, and both sites call it.
The quote stays unverified and keeps its `unverified_grounds` mark. The claim
keeps its other grounds, and it drops only where no ground verifies.

This is a guard, not proof of meaning. It compares negation and number tokens
only. A changed principal or control term still passes it, and the critic
still judges whether a span supports the claim.

## Consequences

A repair the fan-in makes carries an empty `moved`. An archived report can
still carry a non-empty one, and the load check still reads it.

A claim whose only ground is such a quote is dropped, where ADR 0018 kept it
on the flipped span.
