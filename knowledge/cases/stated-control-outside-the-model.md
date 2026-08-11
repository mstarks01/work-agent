# A Candidate the Source Text Answers

## Pattern

A deterministic candidate fires on an internal flow whose `authentication` is unverified. The submitted description, several paragraphs away from where that component is introduced, says every internal call carries a mutual-TLS client certificate issued by the platform and that services reject calls without one.

## Considered

That anything inside the network can call the service as a trusted peer.

## Ruling

Rejected.

## Why

The candidate fired on the *model*, which is a lossy view of the material. The source said what the control is; the extraction did not carry it onto that field. A finding filed anyway would be contradicted by the submitter's own words, and the reviewer's confidence in every other finding drops with it.

A rejected candidate is the system working. Rules direct attention deterministically and are not permitted to know what the prose says — reading the sources is exactly the part of the job that could not be mechanised, and the answer is sometimes "this one is covered".

Two things are still worth doing rather than staying silent. If the stated control covers the flow but leaves a real edge — certificates that identify the fleet rather than the service, no revocation, an exemption for a legacy client — that edge is a finding on its own terms. And if the material's coverage is ambiguous rather than clear, the conditional form is available.

## What decided it

The quoted sentence in the source describing the control, read against the flow the candidate named.
