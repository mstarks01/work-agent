# What a STRIDE Verdict Asserts

This framework rules on **threats**: claims that a named attacker action against a named element is credible against what the System Model states.

## The three states, for this framework

You do not write the state. The service reads it off your ruling's fields, as the Output section says.

- **confirmed** — the threat holds. The attacker action is credible, the elements it names are the ones it acts on, and the facts it rests on are stated in the model. A confirmed threat needs no reason: the finding is the argument.
- **needs-info** — the threat holds if the facts it turns on hold, and at least one of them is open: a control that is `unknown`, or a fact the model has no place for. Name every open fact the threat hangs on. An `unknown` is not a missing control and never becomes one here; it is a question the submitter can answer.
- **rejected** — the threat does not hold. The model's stated facts rule the attacker action out for an element of this shape (`evidence`); its argument does not follow even when every open fact it depends on holds, or it contradicts a fact the model states (`reasoning`) — a fact the model does not state is open, and makes the threat needs-info rather than rejected; it is filed in a lane it does not belong to (`lane`); or another draft already covers the same attacker action against the same element (`duplicate`). Say plainly which of those it was; the rejected array is an audit trail, and a reader has to be able to tell which step killed it.

## What this framework does not say

**Nothing here means "the system is safe".** A rejected draft says that *this* draft did not hold, not that the element is sound. Confirming nothing in a lane is not a clean bill of health for that lane — what a lane examined and what it cited are recorded separately in the coverage account, and neither is a claim about what is not there.

**You do not rate severity.** The lane agent's `likelihood` and `impact` stand as written, and the band is derived from them by a fixed matrix. Do not reject a draft because you would rate its severity differently.

## Lane boundaries

Judge a draft's lane against the scope definitions in the digest below, which are the same lane boundaries the six agents were given, and not against your own sense of the six categories. A draft filed in the wrong lane is rejected with the correct lane named — never moved, because a silent recategorisation hides the fact that a lane missed something.

## The judgements this framework's rulings carry

Beyond a verdict, a STRIDE ruling carries a **confidence** rating. Run it, and the recommendation reading below, over the drafts that survived the three gating steps.

**Confidence.** Rate how firmly the surviving threat is grounded in stated model facts: **high** when every load-bearing claim is stated outright, **medium** when the chain is sound but an intermediate step is inferred from the model rather than stated, **low** when the threat rests largely on an `unknown`. Confidence is about grounding, not about severity or about how likely the attack is.

Read the draft's `grounds` here, and read them for **relevance**: does the cited entry actually justify this threat? A quote that is word-for-word accurate and beside the point is the one defect no check can catch — the service matched each quote against its source and says on the draft which ones failed, so do not re-run that by eye. Entries that fail to support the threat may **lower** the rating you would otherwise give. Entries that support it well never **raise** one: a perfect quote cannot make a threat resting on an `unknown` any more certain, and letting it try turns this dial from grounding-in-model-facts into quality-of-citation.

**Recommendation.** A threat's `mitigations` reach the report as written and nothing else reads them. On every surviving threat emit a `recommendation`: `sound` true where each one addresses *this* threat and would close it, false otherwise, with a `note` saying what it misses. Advice naming a control the model already states is the common case. Never rewrite one, and leave the field off a rejected threat.

**Never reject a threat for its recommendation.** Validity and advice are separate judgements: advice that reads well does not make an unsupported threat hold, and flawed advice does not make a sound threat go away. Rule the claim on its own argument, then rule the advice.

A `low` rating is not a grounding defect. A threat triggered by an `unknown` carries an `unknown-attribute` ground because that is the correct branch for its trigger, and it is correctly `low` — the two say the same thing about the same threat, and neither is a complaint about the other.
