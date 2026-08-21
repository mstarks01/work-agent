# Which Side Enforces the Rule

## When this applies

The model shows code running somewhere the operator does not control — a browser, a mobile app, a desktop client, a partner-run job — or a business process that runs in stages. Chapter V2 asks where a rule is actually enforced, and whether the stages can be taken out of order.

## What to look for

- **Untrusted side, trusted decision.** A control that only exists in client code is not enforcement, because the caller supplies the request. The requirement is that the server re-decides. A description of a validating form leaves the server-side question entirely open.
- **Business rules are requirements too.** V2 is not only input validation. Quantity limits, price and discount rules, entitlement checks, refund and cancellation windows — these are business-logic requirements, and a description of an ordering or billing flow brings them into scope.
- **Sequence and replay.** A multi-step flow raises two distinct requirements: that steps run in the expected order, and that a completed step cannot be replayed. Checkout, onboarding, password reset and approval workflows are the recurring shapes.
- **Anti-automation belongs to the flow.** A requirement about excessive calls attaches to the function being called, so name which function — checkout, search, reset — rather than the system as a whole.
- **Documented expectations.** V2's first section asks the organization to *document* what the business rules are. A system that clearly has such rules and no stated documentation is a ruling on the documentation requirement.

## Guardrails

- Analysis knowledge, not evidence. Cite the flow or the element the model carries, not this note.
- Rule applicability, never a pass. "Validated server-side" in prose is a claim by the submitter, not a verification; the requirement still applies and the input still does not settle it.
- Do not restate V1 here. How a value is escaped for an interpreter is that chapter's; whether the value is *allowed* is this one's.
