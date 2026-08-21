# A Chapter That Does Not Reach This System

## Pattern

A model shows a machine-to-machine ingest service: a partner system posts signed batches to an API, a worker processes them, results land in object storage. No process presents a browser interface. No element mentions a cookie, a page or a rendered document. The WebRTC and web-frontend chapters are in the selected level.

## Considered

That the frame-ancestors and cookie-attribute requirements are unanswered, because nothing in the input shows them being met.

## Ruling

Rejected — recorded as not applicable rather than raised as a finding.

## Why

"Unanswered" and "does not apply" are different states, and collapsing them is how a report fills with noise a reader has to clear by hand.

A requirement about a cookie attribute presupposes a cookie. A requirement about frame ancestors presupposes a document a browser renders. This system has neither, and that is a fact the model states rather than one the input merely omits: every process reads `non-web`, which is a positive answer, not a silence.

Raising a conditional ruling here would be worse than useless. It would tell an operator to go and check a control on a surface their system does not have, and it would do it 30 times over across two chapters. The reader cannot tell that from a `needs-info` verdict, which is exactly why the scope list exists.

Compare a system whose processes read `unknown`. There the input never said, and the remedy is to submit more — a different state with a different answer.

## What decided it

The `non-web` `interface_kind` on every process, cited as a derived fact about the model. The distinction that decided the ruling is between a stated `non-web` and an `unknown`: the first answers the applicability question, and the second leaves it open.
