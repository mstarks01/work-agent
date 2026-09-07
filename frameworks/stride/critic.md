# What a STRIDE Verdict Asserts

This framework rules on **threats**: claims that a named attacker action against a named element is credible against what the System Model states.

## The three states, for this framework

- **confirmed** — the threat holds. The attacker action is credible, the elements it names are the ones it acts on, and the facts it rests on are stated in the model. A confirmed threat needs no reason: the finding is the argument.
- **needs-info** — the threat is raisable but cannot be settled, because a control it turns on is `unknown`. Name every element and attribute the threat hangs on. An `unknown` is not a missing control and never becomes one here; it is a question the submitter can answer.
- **rejected** — the threat does not hold. The model's stated facts rule the attacker action out for an element of this shape (`evidence`); it reasons from a fact the model does not state (`reasoning`) — where a fact the model states by its shape, such as a record that names no actor, counts as stated; it is filed in a lane it does not belong to (`lane`); or another draft already covers the same attacker action against the same element (`duplicate`). Say plainly which of those it was; the rejected array is an audit trail, and a reader has to be able to tell which step killed it.

## What this framework does not say

**Nothing here means "the system is safe".** A rejected draft says that *this* draft did not hold, not that the element is sound. Confirming nothing in a lane is not a clean bill of health for that lane — what a lane examined and what it cited are recorded separately in the coverage account, and neither is a claim about what is not there.

**A severity band is never asserted.** It is derived from the two ratings by a fixed matrix. Leave the ratings you agree with in place and replace both halves together where the rubric says otherwise.

## Lane boundaries

Judge a draft's lane against the scope definitions in the digest below, which are the same lane boundaries the six agents were given, and not against your own sense of the six categories. A draft filed in the wrong lane is rejected with the correct lane named — never moved, because a silent recategorisation hides the fact that a lane missed something.

## The judgements this framework's rulings carry

Beyond a verdict, a STRIDE ruling carries a **confidence** rating, and may carry a replacement **severity**. Run both over the drafts that survived the three gating steps.

**Severity calibration.** Check `likelihood` and `impact` against the rubric above and against each other across the whole set: identical fact patterns must carry identical ratings regardless of which agent wrote them. A draft carrying `rated_unlike` names the other drafts the service found with its verb and its catalogued grounds and a different rating; settle each such group on one rating. Where the rubric's anchors contradict a rating, emit a replacement `severity` on that ruling — the corrected `likelihood` and `impact`, plus a `justification` citing the fact that drove the change. Leave `severity` off every ruling you did not correct. Including it replaces the agent's rating and justification together, so the report can never carry a rating and a justification that disagree.

**Confidence.** Rate how firmly the surviving threat is grounded in stated model facts: **high** when every load-bearing claim is stated outright, **medium** when the chain is sound but an intermediate step is inferred from the model rather than stated, **low** when the threat rests largely on an `unknown`. Confidence is about grounding, not about severity or about how likely the attack is.

Read the draft's `grounds` here, and read them for **relevance**: does the cited entry actually justify this threat? A quote that is word-for-word accurate and beside the point is the one defect no check can catch — the service already matched every quote against the source it names, so do not re-run that by eye. Entries that fail to support the threat may **lower** the rating you would otherwise give. Entries that support it well never **raise** one: a perfect quote cannot make a threat resting on an `unknown` any more certain, and letting it try turns this dial from grounding-in-model-facts into quality-of-citation.

A `low` rating is not a grounding defect. A threat triggered by an `unknown` carries an `unknown-attribute` ground because that is the correct branch for its trigger, and it is correctly `low` — the two say the same thing about the same threat, and neither is a complaint about the other.
