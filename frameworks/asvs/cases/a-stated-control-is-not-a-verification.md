# A Stated Control Is Not a Verification

## Pattern

The description says, in the submitter's own words, "all traffic is TLS 1.3 and passwords are hashed with bcrypt". The model's flows carry `encryption_in_transit: "tls 1.3"`. The cryptography and secure-communication requirements are in the selected level.

## Considered

That the transport and password-storage requirements are satisfied, and should be recorded as confirmed passes.

## Ruling

Accepted, as **needs-info** — the requirements apply, and the statement does not verify them.

## Why

This service rules applicability. It does not, and cannot, report a pass. The distinction survives even when the submitter names the right control, because what the requirement asks is narrower than what the sentence says.

"TLS 1.3" does not say which cipher suites are permitted, whether the certificate is validated, whether older versions are still accepted on the same endpoint, or whether the stated protection covers the hop behind the load balancer. "bcrypt" does not say what work factor, whether the salt is per-user, or whether the comparison is constant-time. Every one of those is its own requirement, and none of them is in the material.

A confirmed pass here would be a false statement about a standard, published under the standard's own reference format. That is worse than an unanswered requirement, because a reader would stop looking.

What the submitter's words *do* change is the ruling's text. Name the stated control, then name what it leaves open. That is a more useful finding than one written as though the sentence were absent.

## What decided it

The flow's stated `encryption_in_transit` value, cited as the attribute it is, together with the submitter's quote. The statement narrowed the question; it did not close it.
