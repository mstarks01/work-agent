# A Chapter That Does Not Reach This System

## Pattern

A model shows a machine-to-machine ingest service: a partner system posts signed batches over HTTPS to an API, a worker processes them, results land in object storage. No process presents a browser interface. No element mentions a cookie, a page or a rendered document. The WebRTC and web-frontend chapters are in the selected level, and the scope line lists their requirements as open, under the question whether people use the application through a web browser.

## Considered

That the frame-ancestors and cookie-attribute requirements are unanswered, because nothing in the input shows them being met.

## Ruling

Rejected — recorded as not applicable rather than raised as a finding.

## Why

"Unanswered" and "does not apply" are different states, and collapsing them is how a report fills with noise a reader has to clear by hand.

A requirement about a cookie attribute presupposes a cookie. A requirement about frame ancestors presupposes a document a browser renders. This system has neither, and that is a fact the model states rather than one the input merely omits: every element that faces a party is an API or a worker, which is a positive answer, not a silence.

Raising a conditional ruling here would be worse than useless. It would tell an operator to go and check a control on a surface their system does not have, and it would do it 30 times over across two chapters. The reader cannot tell that from a `needs-info` verdict, which is exactly why the scope list exists.

Compare a system whose input never says what the service presents to a person. There the capability stays unknown, the input never said, and the remedy is to answer the question or submit more — a different state with a different answer.

## What decided it

The requirements are open on the scope line, so an `excluded` draft is allowed, and only on the fact the question asks about: whether a browser renders anything this system serves. The draft names the browser-rendered surface in `absent_elements`, and the critic rejects it for `evidence`, which records the requirement as not applicable. Had the owner answered that question with a stated absence, the applicability rule would have ruled both chapters out before this lane ran, and the lane would not have seen them.
