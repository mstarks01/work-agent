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
| process:model-server | unknown | unknown | boundary:model-network | GPU inference server |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:model-registry-bucket | boundary:model-network | unknown | unknown |
| store:redis-feature-store | boundary:model-network | unknown | confidential |
| store:inference-log | boundary:serving-edge | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:calling-service>process:inference-gateway>submit-inference-request | entity:calling-service | process:inference-gateway | unknown | per-team API key in a header; operator reports never having expired a key | unknown |
| flow:process:inference-gateway>process:model-server>forward-request | process:inference-gateway | process:model-server | unknown | none; accepted by network position | unknown |
| flow:process:model-server>store:model-registry-bucket>load-artifact | process:model-server | store:model-registry-bucket | object storage API | model server's own service account | unknown |
| flow:process:model-server>store:redis-feature-store>read-features | process:model-server | store:redis-feature-store | Redis protocol | unknown | unknown |
| flow:process:inference-gateway>store:inference-log>write-request-log | process:inference-gateway | store:inference-log | BigQuery API | unknown | unknown |
| flow:entity:ml-engineer>store:model-registry-bucket>publish-artifact | entity:ml-engineer | store:model-registry-bucket | object storage API | unknown; possibly a shared group account | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:public-internet | network |
| boundary:serving-edge | network |
| boundary:model-network | network |

**Recorded notes** — hedges, probed gaps and source disagreements live here, so read them before the sets.

- `process:model-server` — The source says the model network is meant to be reachable only from the gateway, which is an intended restriction; nothing states whether the server itself can be reached from outside, so exposure stays unknown.
- `flow:process:model-server>store:redis-feature-store>read-features` — The source says Redis has no password on it, which establishes the absence of password authentication and not the absence of every mechanism.

**Assumptions**

- `entity:ml-engineer` — The ML engineer is placed in the model network because the schema requires a zone; the source places neither the engineer nor the registry bucket. (basis: No statement locates the engineer or the bucket, and publishing to a bucket does not locate the publisher. The value is a placement the schema requires, not a source-backed fact.)
- `store:inference-log` — The inference log may contain personal data. (basis: Stated to hold raw prompts carrying "whatever the calling team's users typed"; whether that text is personal is not stated, so the tag records a possibility.)
- `store:redis-feature-store` — The Redis feature store holds confidential data under the scheme in prompts/extract.md. (basis: The source says it holds account age and spend bands per customer, which is customer-specific information; the classification is inferred under the scheme, not quoted.)
- `store:model-registry-bucket` — The model registry bucket is placed in the model network because the schema requires a zone; the source does not place it. (basis: Loading artifacts from a bucket does not establish co-location. The value is a placement the schema requires, not a source-backed fact.)
- `process:inference-gateway` — The inference gateway is placed in its own serving-edge zone because the schema requires a zone; the source names no such zone. (basis: Internet exposure does not establish a separate serving-edge network. The value is a placement the schema requires, not a source-backed fact.)
- `store:inference-log` — The inference log is placed in the serving-edge zone because the schema requires a zone; the source does not place it. (basis: Writing to BigQuery does not place it in the gateway's network. The value is a placement the schema requires, not a source-backed fact.)

**Reviewed aliases** — other names a reader ruled identify the same element, each with the words in the source that support it. An extraction using one is named differently, not wrong.

- `entity:calling-service — Other teams' backends` — The source names the callers as other teams' backends; the same slug covers the punctuation-normalized spelling. Ruled in the #961 step 3 review, an assistant-authored ruling the maintainer posted; it authorizes the aggregate actor's equivalence and not a merge of separately extracted teams. Source: > Other teams' backends call our inference gateway
- `entity:ml-engineer — ML engineers` — The source names the publishers as ML engineers, in the plural. Ruled in the #961 step 3 review, an assistant-authored ruling the maintainer posted; it authorizes the aggregate actor's equivalence and not a merge of separately extracted engineers. Source: > ML engineers publish new model artifacts to the registry bucket.
- `boundary:public-internet — internet` — The inferred internet interaction zone under the source's own word. 'External' is accepted only where its membership establishes the same meaning, which the membership rule reads; 'engineering' has no source support. Ruled by the maintainer on 2026-09-16 (#961 step 6). Source: > that we expose on the internet
- `entity:calling-service — other team's backend` — The singular spelling of the ruled aggregate actor. 'Calling team's user' is not this element: the backend makes the API call, and its users supply content that may appear in prompts. Ruled by the maintainer on 2026-09-16 (#961 step 6). Source: > Other teams' backends call our inference gateway

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

## Part 2 — the 9 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### api-and-web-service

**A1.** `V4.1.1` — The inference gateway's response content types and method policy are never described.

- `process:inference-gateway`
- An internet-facing web surface exists and its contract is unstated.

> mark:


### file-handling

**A2.** `V5.2.1` — Nothing records what size or type limits apply to a model artifact an engineer publishes into the registry.

- `entity:ml-engineer`, `store:model-registry-bucket`, `flow:entity:ml-engineer>store:model-registry-bucket>publish-artifact`
- An artifact upload path exists; feature:file-upload has a subject here.

> mark:

**A3.** `V5.2.2` — Nothing states what validates a published artifact before the model server loads it.

- `store:model-registry-bucket`, `flow:entity:ml-engineer>store:model-registry-bucket>publish-artifact`
- The registry is read by an internal server, so an unvalidated artifact is executed content.

> mark:


### self-contained-tokens

**A4.** `V9.1.1` — The per-team API key carries no claims, so this chapter does not apply to the gateway.

- `entity:calling-service`, `process:inference-gateway`, `flow:entity:calling-service>process:inference-gateway>submit-inference-request`
- The stated credential is an opaque key rather than a self-contained token; the exclusion is the answer.

> mark:


### configuration

**A5.** `V13.2.1` — The calling service authenticates to the inference gateway with a per-team API key that the operator has never expired, an unchanging credential on a backend link.

- `entity:calling-service`, `flow:entity:calling-service>process:inference-gateway>submit-inference-request`
- Stated outright, so the ruling is plain. V6.2.10 is about user passwords and forbids forced rotation, so it was the wrong home for this fact.

> mark:


### authorization

**A6.** `V8.2.1` — The model server accepts forwarded requests on network position with no stated permission check.

- `process:inference-gateway`, `process:model-server`, `flow:process:inference-gateway>process:model-server>forward-request`
- authentication is stated as none, so the ruling is plain.

> mark:


### cryptography

**A7.** `V11.3.2` — No cipher is stated for the feature store or the inference log at rest.

- `store:redis-feature-store`, `store:inference-log`
- Both are confidential with encryption_at_rest unknown.

> mark:


### secure-communication

**A8.** `V12.3.3` — The gateway to model server link states neither a protocol nor transport protection.

- `process:inference-gateway`, `process:model-server`, `flow:process:inference-gateway>process:model-server>forward-request`
- protocol and encryption_in_transit are both unknown on an internal crossing. The submitter can state what protects this link, and in this corpus they do: case 05 states transport absent on every internal link and that reference reads gap-from-prose. A property a description settles when it is written down is settled by a description when it is not, so the primary route is prose and the configuration is the alternate.

> mark:


### configuration

**A9.** `V13.3.2` — Nothing states what limits the model server's registry service account.

- `process:model-server`, `store:model-registry-bucket`
- A service account is named and its scope is not.

> mark:

## Part 3 — the 16 recorded STRIDE threats

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

**1.** An attacker who obtains a calling team's API key calls the inference gateway as that team for as long as the key stays valid, and no key has ever been expired.

- `flow:entity:calling-service>process:inference-gateway>submit-inference-request`, `entity:calling-service`
- severity: high/high · verb: `use-credential`
- Bearer credential on an internet-facing endpoint. No key has ever been expired, so how long a stolen one stays valid is unverified.

> mark:

**2.** Any workload inside the model network submits inference requests posing as the gateway, which the model server accepts on network position alone.

- `flow:process:inference-gateway>process:model-server>forward-request`, `process:model-server`
- severity: medium/high · verb: `impersonate`
- Stated absence of authentication behind a boundary that is only 'meant to be' closed.

> mark:

**3.** An attacker publishes a model artifact under a shared group account with no individual identity behind it.

- `flow:entity:ml-engineer>store:model-registry-bucket>publish-artifact`, `entity:ml-engineer`
- severity: medium/high · verb: `use-credential`
- Publish authentication is unknown and possibly shared; report as unverified.

> mark:


### tampering

**4.** An attacker who can write to the registry swaps the model artifact and the model server loads it without any integrity verification.

- `store:model-registry-bucket`, `flow:process:model-server>store:model-registry-bucket>load-artifact`
- severity: high/high · verb: `plant`
- The defining supply-chain finding of this case; the source states verification is absent.

> mark:

**5.** An attacker with model-network access writes to the unauthenticated Redis feature store and changes the features a decision is made on.

- `store:redis-feature-store`, `flow:process:model-server>store:redis-feature-store>read-features`
- severity: medium/high · verb: `alter`
- No password is a stated fact, not an unknown; poisoning features silently changes inference output.

> mark:

**6.** An attacker inside the model network alters request payloads in flight between the gateway and the model server, if that link carries no transport protection, which is unverified.

- `flow:process:inference-gateway>process:model-server>forward-request`
- severity: medium/medium · verb: `alter-in-transit`
- Same flow as the spoofing entry; the lane difference is modifying content versus assuming identity. The flow states no authentication, and its encryption is unknown rather than absent.

> mark:


### repudiation

**7.** Nobody can establish which engineer published a given model artifact, because publication runs through a shared account.

- `store:model-registry-bucket`, `entity:ml-engineer`
- severity: medium/medium · verb: `unattributable`
- Model provenance is the audit trail that matters here; a shared account destroys it.

> mark:

**8.** A calling team disputes a request the log attributes to them, and a shared long-lived API key cannot establish who actually sent it.

- `store:inference-log`, `entity:calling-service`
- severity: medium/medium · verb: `unattributable`
- The log records the key's team, not an actor; distinct from disclosure findings about the same store.

> mark:


### information-disclosure

**9.** An attacker who obtains a read grant on the inference log reads the raw end-user prompts the source says it retains, and nothing records who holds that grant.

- `store:inference-log`
- severity: medium/high · verb: `read`
- A debugging store that silently became the most sensitive data collection in the system.

> mark:

**10.** An attacker with model-network access reads per-customer account age and spend bands from the unauthenticated feature store.

- `store:redis-feature-store`
- severity: medium/high · verb: `read`
- Stated absence of authentication over data tagged pii and financial.

> mark:

**11.** A caller crafts a request that makes the model emit customer features belonging to a different tenant.

- `process:model-server`, `flow:entity:calling-service>process:inference-gateway>submit-inference-request`
- severity: medium/high · verb: `elicit`
- Model-mediated disclosure: the gateway authenticates the team but nothing scopes which customers' features a request may pull. Review sitting 01 struck 'or training data' from this claim: no training pipeline exists in this model, and the label set already rules training-time attacks out of scope for this case, so the claim graded the tool against a fact the model does not hold.

> mark:

**12.** An attacker on the internal path reads end-user text out of forwarded requests, because transport encryption there is unverified.

- `flow:process:inference-gateway>process:model-server>forward-request`
- severity: medium/medium · verb: `intercept`
- Boundary crossing from serving edge into model network with no stated protection.

> mark:


### denial-of-service

**13.** A caller holding a valid key floods the gateway with inference requests and exhausts the shared GPU capacity behind it.

- `process:inference-gateway`, `flow:entity:calling-service>process:inference-gateway>submit-inference-request`
- severity: high/high · verb: `flood`
- GPU capacity is the scarce, expensive resource; no quota or rate limit per key is described.

> mark:

**14.** An attacker with model-network access flushes or fills the unauthenticated Redis store, stalling every request that needs features.

- `store:redis-feature-store`, `process:model-server`
- severity: medium/high · verb: `disable`
- An unauthenticated cache is as easy to destroy as to read.

> mark:

**15.** An attacker deletes or corrupts the registry artifact so the model server cannot start after a restart.

- `store:model-registry-bucket`, `process:model-server`
- severity: low/high · verb: `delete`
- Startup dependency with no fallback described.

> mark:


### elevation-of-privilege

**16.** A caller uses its key to reach models or capabilities its team was never entitled to, because the key authenticates without scoping what it may invoke.

- `entity:calling-service`, `process:inference-gateway`
- severity: medium/medium · verb: `abuse-grant`
- Authorization scope on a multi-tenant gateway is unspecified; report as unverified.

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
      "model.json": "4f4193d4b0435635054b15c8671a2184acac80c9377c98a0b41ad0064e926f1a",
      "claims/asvs.json": "0cb9e82391d0647fccc20a8d022b8e3a6287058ee2086b86999f1bab22cf7254",
      "claims/stride.json": "4b06c341425cf393763433d12f7f1da917a689e465e4233aeb1c8566c9f8a3b3"
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
