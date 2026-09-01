# 22. A report attestation is detached, and says only origin

- **Status**: accepted
- **Date**: 2026-09-01
- **Effort**: [#501 — Add signed report attestations for portable artifact
  authenticity](https://github.com/mstarks01/work-agent/issues/501)

## Context

A report carries `input.source_sha256`, `analysis_context.instruction_sha256`
and an `execution_fingerprint` per node. Every one is an **unkeyed digest**: a
reader recomputes it from the artifact and notices when the two disagree.

Anyone who edits the report recomputes them too. They establish internal
consistency and nothing about origin, and a portable report — one that has left
the service that produced it — had no way to say who made it.

## Decisions

### The signature says one thing, and the docs say so first

**A valid signature establishes origin and integrity. It does not establish that
the findings are correct, and it does not establish that the run was certified.**

Certification is a separate, deployment-local verdict about generation identity
(`analysis_service.certification`), and a signed uncertified report is an
ordinary artifact. The two are easy to conflate because both are green ticks
about the same file.

So the claim is stated in the module docstring, in the guide's first sentence, in
the `verified` verdict's own detail string, and on the CLI's passing path
specifically — a failure is already read as a failure, while a pass is the one a
reader is tempted to over-read. A verifier printing a bare "OK" would be
inviting exactly that.

### Detached, not enveloped

The signature lives beside the report. A field inside the report would have to be
excluded from its own coverage, and "everything except this one field" is a rule
that grows exceptions. Detached keeps the covered set equal to the whole
artifact — adding a field invalidates, which is the property wanted.

### The signature is over a bound digest, not over the report bytes

`signed_payload` is the report digest together with the canonicalization
version, the payload type and the key id. Signing the report bytes alone would
leave three replays open: lifting a signature onto another document, replaying it
under a different canonicalization, and re-attributing it to another key.

### Canonicalization is versioned, and a verifier will not guess

JSON, sorted keys, no insignificant whitespace, UTF-8, no ASCII escaping. The
version is **in the signed payload**, so an envelope from another version fails
with its own verdict rather than being compared across schemas. A guess that
happened to produce the same bytes would validate a report nobody signed.

### Ed25519

It needs no parameter choices: no curve to pick, no hash to pair, no padding
mode. For a signature nobody will tune, the scheme with fewest ways to be
configured wrongly is the right one. `cryptography` is already in `uv.lock`
transitively; #501 makes it a direct dependency, which is the honest spelling of
a thing the code now imports.

### Six verdicts, not a boolean

The states a caller must act on differently are more than two. `unsigned` is an
ordinary artifact from a deployment that does not sign. `unknown-key` may be
perfectly good to somebody holding a fuller keyring. `revoked` is a signature
that checks out and must still be refused. A boolean collapses all three into
"no" and teaches a reader to treat them alike; the CLI gives each its own exit
code so a script can tell "we do not sign" from "this key was compromised".

### Rotation and revocation are different events

A **retired** key still verifies what it signed before it retired: refusing it
would invalidate every historical report on the day a deployment rotated. A
**revoked** key verifies nothing, including what it signed before anyone
noticed, because an attacker holding it could backdate.

Two statuses would force a deployment to choose between keeping history
verifiable and being able to disown a leaked key.

Revocation is checked **after** the cryptographic check. A revoked key id over a
forged document is `invalid`, not `revoked` — an operator needs to know whether
the revoked key really signed this or whether somebody is replaying its id, and
those are different incidents.

### The verifier reads JSON, never a model

`analysis_service.verify_report` loads the report as a plain mapping. It never
imports the report schema, so a report from a build whose schema has since moved
still verifies, and a verifier written in another language can follow the same
rules from the docstring alone. That is most of what "operates independently of
the producing service" means.

## Consequences

- An unsigned report is **identifiable as unsigned**, with its own verdict and
  exit code. It is never silently trusted and never reported as a failure.
- The keyring is the **verifier's**, never shipped beside a report: a report
  carrying the key that validates it would verify against itself.
- A deployment that signs must hold a private key. Nothing here manages one —
  key custody, generation and storage are the operator's, and this repository
  deliberately holds no key material and no example private key.
- **Not addressed here**: a transparency log, and a certificate chain binding a
  key to an organisation. `signed_at` is a claim by the signer, not a trusted
  timestamp. A deployment needing either needs a timestamping authority or a
  Sigstore-style bundle, and the envelope is versioned so adding one is a new
  canonicalization rather than an ambiguous field.
- **Not addressed here**: the service does not yet sign automatically. Signing
  is a library call plus a key the deployment holds; wiring it into the job
  lifecycle needs a decision about where a private key lives in a process that
  also runs model output, and that decision is #502's boundary work.
