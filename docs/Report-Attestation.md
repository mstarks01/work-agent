# Report attestations

**What a signature says: this report came from that deployment, and no covered
byte has moved since. What it does not say: that the findings are correct, or
that the run was certified.**

That distinction is the whole reason this document exists. A green signature is
the easiest thing in a report to over-read, and reading it as an endorsement of
the analysis is reading it backwards.

## Why the hashes already in the report are not this

A report carries `input.source_sha256`, `analysis_context.instruction_sha256`
and an `execution_fingerprint` per node. All three are **unkeyed digests**: a
reader recomputes a value from the artifact and notices when the two disagree.
Anyone who edits the report can recompute them too. They establish internal
consistency, and nothing whatsoever about origin.

An attestation is a signature by a key a deployment holds. It answers the two
questions a digest cannot — *which deployment* and *has it changed* — and no
others.

## The shape

A **detached** signature, beside the report rather than inside it. A field
inside the report would have to be excluded from its own coverage, and
"everything except this one field" is a rule that grows exceptions.

```json
{
  "canonical_version": 1,
  "payload_type": "application/vnd.work-agent.report+json",
  "report_sha256": "…",
  "key_id": "deployment-2026-09",
  "signature": "…",
  "signed_at": "2026-09-01T12:00:00Z"
}
```

The signature is **not** over the report bytes. It is over the report digest
bound to the canonicalization version, the payload type, the key id and the
signing time, so a signature cannot be lifted onto another document, replayed
under a different canonicalization, re-attributed to another key, or moved in
time to predate a retirement.

`signed_at` is when the deployment signed. It is **not evidence of when**: a
machine's clock is not a trusted timestamp and nothing countersigns it. It is
recorded because a rotation policy is stated in time. Signing it makes it the
key holder's own claim and nobody else's; it does not make the claim true. A
deployment needing a trustworthy time needs a timestamping authority, which
this is not.

## Canonicalization

JSON, sorted keys, no insignificant whitespace, UTF-8, no ASCII escaping. That
is version 1, and the version is **in the signed payload**. A verifier meeting an
envelope on a version it does not implement returns `unsupported` and stops — it
does not guess, because a guess that produced the same bytes by luck would
validate a report nobody signed.

Ed25519, because it needs no parameter choices: no curve to pick, no hash to
pair, no padding mode. The scheme with fewest ways to be configured wrongly is
the right one for a signature nobody will tune.

## Verifying

```sh
python -m analysis_service.verify_report report.json \
    --keyring keyring.toml --attestation report.sig.json
```

It reads JSON and never loads this project's models, so a report from a build
whose schema has since moved still verifies, and a verifier written in another
language can follow the same rules from the description above.

Six verdicts, each with its own exit code, because the states a caller must act
on differently are more than two:

| Verdict | Exit | Meaning |
| --- | --- | --- |
| `verified` | 0 | Origin and integrity established. Nothing more. |
| `unsigned` | 10 | No attestation supplied. An ordinary artifact from a deployment that does not sign — **not** a failure, and not trust. |
| `unknown-key` | 11 | The key id is not in this keyring. The signature may be good to somebody who holds it. |
| `unsupported` | 12 | A canonicalization or payload type this verifier does not implement. |
| `revoked` | 13 | The signature checks out **and** the key is revoked. |
| `invalid` | 14 | The signature fails, or a covered byte moved. |

Collapsing these into 0/1 would make "we do not sign" and "this key was
compromised" the same event to anything automated.

## The keyring

Held by the **verifier**, never shipped beside a report — a report carrying the
key that validates it would verify against itself.

```toml
version = 1

[[keys]]
key_id = "deployment-2026-09"
public_key = "<base64 raw Ed25519 public key>"
status = "active"

[[keys]]
key_id = "deployment-2026-03"
public_key = "<base64 raw Ed25519 public key>"
status = "retired"
retired_at = 2026-09-01T00:00:00Z
```

Loading fails closed: an unreadable file, invalid TOML, a malformed key, a
retired key with no date, or a keyring naming no keys at all. The last one
matters — an empty keyring would answer `unknown-key` to everything, which reads
as a configuration problem rather than as the absence of every key.

## Rotation and revocation are different events

| Status | What it means | What happens to old signatures |
| --- | --- | --- |
| `active` | Currently signing. | Verified. |
| `retired` | Stopped signing at `retired_at`. | **Still verified**, if they predate it. |
| `revoked` | Compromised. | **Refused**, including ones that predate the revocation. |

Two statuses would force a deployment to choose between keeping history
verifiable and being able to disown a leaked key.

A retired key still verifies what it signed before retirement, because refusing
it would invalidate every historical report on the day a deployment rotated. A
revoked key verifies nothing, because an attacker holding it could backdate, so
what it signed before anyone noticed is no safer than what it signed after.

**Revocation is checked after the cryptography, not before.** A revoked key id
over a forged document returns `invalid`, not `revoked` — an operator needs to
know whether the revoked key really signed this or whether somebody is replaying
its id, and those are different incidents.

## To rotate

1. Generate a new key pair. The private key never enters this repository, a
   report, or a log.
2. Add the new public key to every verifier's keyring as `active`.
3. Switch the signer to the new key.
4. Mark the old key `retired` with the moment step 3 happened.

To revoke, set `status = "revoked"` instead and re-issue anything that mattered
under a key you still trust.
