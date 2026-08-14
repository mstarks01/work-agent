# Two Findings on One Flow, and When That Is Right

## Pattern

A flow carries records containing personal data into a store. Its `encryption_in_transit` is unknown, and the caller's identity is unverified.

## Considered

Filing one finding covering "the flow is unprotected", or two: reading the records in transit, and writing forged records into the store.

## Ruling

Accepted as two findings, in two lanes.

## Why

They differ in every part that matters to a reader deciding what to do. The attacker positions differ — on-path observation versus the ability to send a request. The consequences differ — disclosure of the records versus corruption of what downstream readers trust. The mitigations differ — transport protection versus caller authentication and write authorization. A single merged finding would have one severity and one remediation for two problems, and whichever one is cheaper to fix would silently close it.

The opposite mistake is just as common: splitting one finding into a family of near-duplicates, one per affected element, when the attacker action, the position and the fix are identical. That is one threat with several affected elements, and the model's own element list is where the breadth belongs.

The test is not "how many controls are missing" but "how many distinct attacker actions, with distinct consequences, does this permit".

## What decided it

The store's asset grading, the flow's two separate unknown attributes, and the fact that each supports a different attacker action.
