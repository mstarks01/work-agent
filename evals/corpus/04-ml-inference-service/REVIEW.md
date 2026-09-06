# Review sitting — is `04-ml-inference-service`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/04-ml-inference-service`.

**Hosted model inference gateway** — domain `ml-serving`.

**What you are checking.** Not whether two write-ups are the same threat — the
identity rule decides that mechanically. This asks the question underneath:
**do these reference sets describe what could actually go wrong with this
system?** If a set misses a whole class of attack, the tool scores full marks
for missing it too, and nothing in the repo would ever say so.

## The one rule

**Read Part 1 and write your own list before you open Part 2.** If you read
the recorded threats first you will find them reasonable, and the sitting
measures nothing. Your list does not have to be good or complete — it only has
to be yours, written first.

Roughly an hour.

---

## Part 1 — the system

### System description (description)

Exactly what the service would receive.

> Hosted model inference for our product teams.
>
> Other teams' backends call our inference gateway, a FastAPI service on GKE
> that we expose on the internet because two of the calling services are in a
> different cloud. Callers pass an API key in a header. Keys are issued per
> calling team and we have never expired one.
>
> The gateway forwards the request to the model server, which runs the actual
> model on GPU nodes in our model network. There is no auth between the gateway
> and the model server; the model network is meant to be reachable only from the
> gateway.
>
> The model server loads model artifacts from a model registry bucket at startup.
> It uses its own service account for that. I don't believe anything verifies the
> artifact hasn't been swapped — we just trust the bucket.
>
> Requests often need customer features, which the model server reads from a
> Redis feature store on the same network. Redis has no password on it; it is
> only reachable inside the model network. The features include account age and
> spend bands per customer.
>
> The gateway writes every request and response into an inference log in
> BigQuery, for debugging. That means raw prompts, which sometimes carry whatever
> the calling team's users typed.
>
> ML engineers publish new model artifacts to the registry bucket. I'm not sure
> what governs who can push — I think it is a shared group account.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:calling-service | external-system | boundary:public-internet |
| entity:ml-engineer | human | boundary:model-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:inference-gateway | internet-facing | web | boundary:serving-edge | FastAPI on GKE |
| process:model-server | internal | unknown | boundary:model-network | GPU inference server |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:model-registry | boundary:model-network | unknown | internal |
| store:feature-store | boundary:model-network | unknown | confidential |
| store:inference-log | boundary:serving-edge | unknown | confidential |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:calling-service-to-inference-gateway:submit-inference-request | entity:calling-service | process:inference-gateway | HTTPS | per-team API key in a header, never expired or rotated | unknown |
| flow:inference-gateway-to-model-server:forward-request | process:inference-gateway | process:model-server | unknown | none; accepted by network position | unknown |
| flow:model-server-to-model-registry:load-artifact | process:model-server | store:model-registry | object storage API | model server's own service account | unknown |
| flow:model-server-to-feature-store:read-features | process:model-server | store:feature-store | Redis protocol | none | unknown |
| flow:inference-gateway-to-inference-log:write-request-log | process:inference-gateway | store:inference-log | BigQuery API | unknown | unknown |
| flow:ml-engineer-to-model-registry:publish-artifact | entity:ml-engineer | store:model-registry | object storage API | unknown; possibly a shared group account | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:serving-edge | network |
| boundary:model-network | network |

**Assumptions**

- `entity:ml-engineer` — ML engineers work from inside the model network rather than over the public internet. (basis: No remote-access path is described for artifact publication; the registry is stated to sit in the model network.)
- `store:inference-log` — The inference log contains personal data. (basis: Stated to hold raw prompts carrying "whatever the calling team's users typed".)

### Your list

Write what could go wrong. Anything: an attack, a missing control, a question
the text does not answer. Bullet points, in any order, no need to sort by
category.

```
-
-
-
```

---

## Part 2 — the 10 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### api-and-web-service

**A1.** `V4.1.1` — The inference gateway's response content types and method policy are never described.

- cites: `process:inference-gateway`
- tier: must-find
- recorded note: An internet-facing HTTPS surface exists and its contract is unstated.

> mark:


### file-handling

**A2.** `V5.2.1` — Nothing limits the size or type of a model artifact published into the registry.

- cites: `entity:ml-engineer`, `store:model-registry`, `flow:ml-engineer-to-model-registry:publish-artifact`
- tier: must-find
- recorded note: An artifact upload path exists; feature:file-upload has a subject here.

> mark:

**A3.** `V5.2.2` — Nothing states what validates a published artifact before the model server loads it.

- cites: `store:model-registry`, `flow:ml-engineer-to-model-registry:publish-artifact`
- tier: expected
- recorded note: The registry is read by an internal server, so an unvalidated artifact is executed content.

> mark:


### self-contained-tokens

**A4.** `V9.1.1` — The per-team API key carries no claims, so this chapter does not apply to the gateway.

- cites: `entity:calling-service`, `process:inference-gateway`, `flow:calling-service-to-inference-gateway:submit-inference-request`
- tier: must-find
- recorded note: The stated credential is an opaque key rather than a self-contained token; the exclusion is the answer.

> mark:


### authentication

**A5.** `V6.2.10` — The per-team API key is never expired or rotated.

- cites: `entity:calling-service`, `flow:calling-service-to-inference-gateway:submit-inference-request`
- tier: must-find
- recorded note: Stated outright, so the ruling is plain.

> mark:


### authorization

**A6.** `V8.2.1` — The model server accepts forwarded requests on network position with no stated permission check.

- cites: `process:inference-gateway`, `process:model-server`, `flow:inference-gateway-to-model-server:forward-request`
- tier: expected
- recorded note: authentication is stated as none, so the ruling is plain.

> mark:


### cryptography

**A7.** `V11.3.2` — No cipher is stated for the feature store or the inference log at rest.

- cites: `store:feature-store`, `store:inference-log`
- tier: expected
- recorded note: Both are confidential with encryption_at_rest unknown.

> mark:


### secure-communication

**A8.** `V12.3.3` — The gateway to model server link states neither a protocol nor transport protection.

- cites: `process:inference-gateway`, `process:model-server`, `flow:inference-gateway-to-model-server:forward-request`
- tier: must-find
- recorded note: protocol and encryption_in_transit are both unknown on an internal crossing.

> mark:


### security-logging-and-error-handling

**A9.** `V16.2.5` — Nothing states what of an inference request is written to the confidential request log.

- cites: `process:inference-gateway`, `store:inference-log`, `flow:inference-gateway-to-inference-log:write-request-log`
- tier: must-find
- recorded note: The log is confidential and its content is never described.

> mark:


### configuration

**A10.** `V13.3.2` — Nothing states what limits the model server's registry service account.

- cites: `process:model-server`, `store:model-registry`
- tier: expected
- recorded note: A service account is named and its scope is not.

> mark:

## Part 3 — the 18 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `reject` — overstated, unsupported by the text, or not really a finding here.
- `duplicate` — the same finding as another entry on this list, by number.
- `unsure` — you read it and cannot decide. It is a real answer: say it rather
  than pick one of the other three to get past the entry.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker who obtains a never-expiring API key calls the inference gateway as that team indefinitely.

- cites: `flow:calling-service-to-inference-gateway:submit-inference-request`, `entity:calling-service`
- tier: must-find · severity: high/high · verb: `use-credential`
- recorded note: Bearer credential with no expiry on an internet-facing endpoint; compromise is permanent until noticed.

> mark:

**2.** Any workload inside the model network submits inference requests posing as the gateway, which the model server accepts on network position alone.

- cites: `flow:inference-gateway-to-model-server:forward-request`, `process:model-server`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: Stated absence of authentication behind a boundary that is only 'meant to be' closed.

> mark:

**3.** An attacker publishes a model artifact under a shared group account with no individual identity behind it.

- cites: `flow:ml-engineer-to-model-registry:publish-artifact`, `entity:ml-engineer`
- tier: expected · severity: medium/high · verb: `use-credential`
- recorded note: Publish authentication is unknown and possibly shared; report as unverified.

> mark:


### tampering

**4.** An attacker who can write to the registry swaps the model artifact and the model server loads it without any integrity verification.

- cites: `store:model-registry`, `flow:model-server-to-model-registry:load-artifact`
- tier: must-find · severity: high/high · verb: `plant`
- recorded note: The defining supply-chain finding of this case; the source states verification is absent.

> mark:

**5.** An attacker with model-network access writes to the unauthenticated Redis feature store and changes the features a decision is made on.

- cites: `store:feature-store`, `flow:model-server-to-feature-store:read-features`
- tier: must-find · severity: medium/high · verb: `alter`
- recorded note: No password is a stated fact, not an unknown; poisoning features silently changes inference output.

> mark:

**6.** An attacker inside the model network alters request payloads in flight on the unauthenticated, unencrypted forward path.

- cites: `flow:inference-gateway-to-model-server:forward-request`
- tier: expected · severity: medium/medium · verb: `alter-in-transit`
- recorded note: Same flow as the spoofing entry; the lane difference is modifying content versus assuming identity.

> mark:


### repudiation

**7.** Nobody can establish which engineer published a given model artifact, because publication runs through a shared account.

- cites: `store:model-registry`, `entity:ml-engineer`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: Model provenance is the audit trail that matters here; a shared account destroys it.

> mark:

**8.** A calling team disputes a request the log attributes to them, and a shared long-lived API key cannot establish who actually sent it.

- cites: `store:inference-log`, `entity:calling-service`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: The log records the key's team, not an actor; distinct from disclosure findings about the same store.

> mark:


### information-disclosure

**9.** An attacker who reaches the inference log reads raw end-user prompts, whose protection at rest is unverified.

- cites: `store:inference-log`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: A debugging store that silently became the most sensitive data collection in the system.

> mark:

**10.** An attacker with model-network access reads per-customer account age and spend bands from the unauthenticated feature store.

- cites: `store:feature-store`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: Stated absence of authentication over data tagged pii and financial.

> mark:

**11.** A caller crafts a request that makes the model emit customer features belonging to a different tenant.

- cites: `process:model-server`, `flow:calling-service-to-inference-gateway:submit-inference-request`
- tier: expected · severity: medium/high · verb: `elicit`
- recorded note: Model-mediated disclosure: the gateway authenticates the team but nothing scopes which customers' features a request may pull. Review sitting 01 struck 'or training data' from this claim: no training pipeline exists in this model, and the label set already rules training-time attacks out of scope for this case, so the claim graded the tool against a fact the model does not hold.

> mark:

**12.** An attacker on the internal path reads end-user text out of forwarded requests, because transport encryption there is unverified.

- cites: `flow:inference-gateway-to-model-server:forward-request`
- tier: expected · severity: medium/medium · verb: `intercept`
- recorded note: Boundary crossing from serving edge into model network with no stated protection.

> mark:


### denial-of-service

**13.** A caller holding a valid key floods the gateway with inference requests and exhausts the shared GPU capacity behind it.

- cites: `process:inference-gateway`, `flow:calling-service-to-inference-gateway:submit-inference-request`
- tier: must-find · severity: high/high · verb: `flood`
- recorded note: GPU capacity is the scarce, expensive resource; no quota or rate limit per key is described.

> mark:

**14.** An attacker with model-network access flushes or fills the unauthenticated Redis store, stalling every request that needs features.

- cites: `store:feature-store`, `process:model-server`
- tier: expected · severity: medium/high · verb: `disable`
- recorded note: An unauthenticated cache is as easy to destroy as to read.

> mark:

**15.** An attacker deletes or corrupts the registry artifact so the model server cannot start after a restart.

- cites: `store:model-registry`, `process:model-server`
- tier: expected · severity: low/high · verb: `delete`
- recorded note: Startup dependency with no fallback described.

> mark:


### elevation-of-privilege

**16.** An attacker who can write to the registry turns an unverified artifact load into code execution on the GPU nodes.

- cites: `store:model-registry`, `process:model-server`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: Model artifacts are loaded as code; the escalation framing of the swap finding.

> mark:

**17.** An attacker who compromises the internet-facing gateway inherits unauthenticated access to everything in the model network.

- cites: `process:inference-gateway`, `process:model-server`
- tier: must-find · severity: medium/high · verb: `escalate`
- recorded note: The whole model network's security rests on the gateway being the only reachable path.

> mark:

**18.** A caller uses its key to reach models or capabilities its team was never entitled to, because the key authenticates without scoping what it may invoke.

- cites: `entity:calling-service`, `process:inference-gateway`
- tier: expected · severity: medium/medium · verb: `abuse-grant`
- recorded note: Authorization scope on a multi-tenant gateway is unspecified; report as unverified.

> mark:

---

## What was on your list and not on either of theirs

The point of the sitting. One line each, and say which set you expected it in.

```
-
-
```

---

## What to do with the result

**Counts first**, kept apart per framework: how many `agree`, `reject`,
`duplicate` per part, and how many of your own items are missing from either set.

- **Few `reject` marks, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several `reject` marks** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sitting.** This document is your reading aid and the
evidence that the method ran; it is not the record. The record is **one JSON
file** under `evals/review/submissions/`, carrying your own list, your marks,
your missing list, your notes and a digest of each file you read:

```json
{
  "envelope": 1,
  "submitted_by": "<the GitHub login opening the PR>",
  "submitted_for": "<who read the case: a login, or the word anonymous>",
  "generated": "<YYYY-MM-DD>",
  "cases": {
    "04-ml-inference-service": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "3da14d8d61e45baa73b0a7ee2b6935b0da3c1d47c62fdf9cb30ef4a09d6c67b6",
      "model.json": "e0a3a1a0bf67fefcbd10b510e4ed5e7a15f2e47651bc5cdc8f660899de65d37c",
      "claims/asvs.json": "3985e6d73b9aa5b5dc8f1b72eee1930c67fab410a2d4710041b39df07339ad31",
      "claims/stride.json": "219c4116a56305055f1ff4ce543c246ab37e0bb5e3082e83523044d56d5fb94d"
      }
    }
  }
}
```

The app (`uv run python webapp/sitting.py`) and the standalone page both write
that file for you and open the pull request; a reader with no clone lands on
GitHub's editor with it already filled in. The file is named for its own
digest, so an edited file no longer matches its name.

**Two names, because they answer two questions.** `submitted_by` is the account
that opens the pull request and answers for the sitting; contribution CI binds
it to that account, so it needs no roster line. `submitted_for` is who read the
case: the same login where you read it yourself, another login, or `anonymous`
where the reader takes part on no name of their own.

The digests above are the files as they were when this document was
generated. A submission covers the frameworks whose reference sets it carries
a matching digest for, and it stops covering one the moment that file changes.
CI checks that every finding of every set you read carries a mark, that the
digests match the tree, and that the pull request adds this one file and
nothing else. `tests/test_case_review.py` names the cases still waiting, and
derives that list from the merged submissions.
