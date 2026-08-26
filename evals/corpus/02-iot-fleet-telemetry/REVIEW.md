# Review sitting — is `02-iot-fleet-telemetry`'s reference list right?

`evals/BLESSING.md` step 6, over `evals/corpus/02-iot-fleet-telemetry`.

**Sensor fleet telemetry and firmware distribution** — domain `iot-fleet`.

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

## Part 1 — the system, and your own list

### System description (description)

Exactly what the service would receive.

> Telemetry platform for our deployed sensor fleet.
>
> We have a few thousand sensor nodes installed on customer sites. They are
> outside our physical control — a technician can be standing next to one with a
> laptop. Each node publishes readings over MQTT to the device gateway, which is
> an MQTT broker we run on GKE and expose to the internet because the nodes dial
> in from anywhere. Nodes authenticate to the broker with a pre-shared key. The
> key is per fleet, not per device. Nobody has rotated it since deployment.
>
> The gateway looks up the device in a device registry (Firestore) to check the
> key and to find out which customer the node belongs to. Readings the gateway
> accepts are forwarded onto Pub/Sub and picked up by the telemetry normalizer,
> a Python consumer in our analytics network, which writes them into the
> telemetry lake in BigQuery. The lake has site addresses and occupancy patterns
> in it, so it is customer data.
>
> Firmware updates work the other way round: nodes poll a Cloud Storage bucket
> for a new image and install what they find. The bucket is public read, because
> making the nodes authenticate to it was awkward. I do not know whether the
> nodes check a signature on the image before installing it.
>
> Field technicians service nodes over a local serial console. I don't know what
> authentication that console has, if any.
>
> Our own fleet operators look at dashboards over BigQuery from the corporate
> network, signed in with company SSO.

### What the model says is in it

Not part of the question, but the records cite these names, so you need them.

**External entities**

| id | kind | zone |
|---|---|---|
| entity:sensor-node | external-system | boundary:field-network |
| entity:field-technician | human | boundary:field-network |
| entity:fleet-operator | human | boundary:corporate-network |

**Processes**

| id | exposure | interface | zone | technology |
|---|---|---|---|---|
| process:device-gateway | internet-facing | non-web | boundary:ingest-edge | MQTT broker on GKE |
| process:telemetry-normalizer | internal | non-web | boundary:analytics-core | Python Pub/Sub consumer |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:device-registry | boundary:ingest-edge | unknown | confidential |
| store:telemetry-lake | boundary:analytics-core | unknown | customer data |
| store:firmware-bucket | boundary:ingest-edge | unknown | public |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:sensor-node-to-device-gateway:publish-readings | entity:sensor-node | process:device-gateway | MQTT | fleet-wide pre-shared key, shared by every device and never rotated | unknown |
| flow:device-gateway-to-device-registry:look-up-device | process:device-gateway | store:device-registry | Firestore API | unknown | unknown |
| flow:device-gateway-to-telemetry-normalizer:forward-readings | process:device-gateway | process:telemetry-normalizer | Pub/Sub | unknown | unknown |
| flow:telemetry-normalizer-to-telemetry-lake:load-readings | process:telemetry-normalizer | store:telemetry-lake | BigQuery API | unknown | unknown |
| flow:sensor-node-to-firmware-bucket:poll-firmware | entity:sensor-node | store:firmware-bucket | HTTPS | none; the bucket is public read | unknown |
| flow:field-technician-to-sensor-node:local-service-session | entity:field-technician | entity:sensor-node | local serial console | unknown | unknown |
| flow:fleet-operator-to-telemetry-lake:query-dashboards | entity:fleet-operator | store:telemetry-lake | BigQuery API | company SSO | unknown |

**Trust boundaries**

| id | kind |
|---|---|
| boundary:field-network | network |
| boundary:ingest-edge | network |
| boundary:analytics-core | network |
| boundary:corporate-network | network |

**Assumptions**

- `store:firmware-bucket` — The firmware bucket accepts unauthenticated reads from anywhere. (basis: Described as "public read" and polled by devices that hold no credential for it.)
- `store:telemetry-lake` — The telemetry lake holds personal data about customer sites. (basis: Stated to contain "site addresses and occupancy patterns", described as customer data.)

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

## Part 2 — the 8 recorded ASVS records

The narrower question, per record: **does this requirement apply to this system, and does the input show it satisfied?** An ASVS claim rules applicability and never a pass.


### authentication

**A1.** `V6.3.2` — Every sensor node authenticates with one fleet-wide pre-shared key that is never rotated.

- cites: `entity:sensor-node`, `process:device-gateway`, `flow:sensor-node-to-device-gateway:publish-readings`
- tier: must-find
- recorded note: A shared machine credential is stated outright, so the ruling is plain.

> mark:


### authorization

**A2.** `V8.2.1` — Nothing states what restricts the device gateway's access to the device registry.

- cites: `process:device-gateway`, `store:device-registry`, `flow:device-gateway-to-device-registry:look-up-device`
- tier: must-find
- recorded note: authentication on the flow is unknown; the requirement applies and stays open.

> mark:

**A3.** `V8.3.1` — Nothing states which layer enforces a tenant boundary on telemetry writes.

- cites: `process:telemetry-normalizer`, `store:telemetry-lake`
- tier: expected
- recorded note: The normalizer writes every fleet's readings and no enforcing layer is named.

> mark:


### oauth-and-oidc

**A4.** `V10.4.4` — Company SSO is named for operator dashboards and nothing says which grant it uses.

- cites: `entity:fleet-operator`, `store:telemetry-lake`, `flow:fleet-operator-to-telemetry-lake:query-dashboards`
- tier: must-find
- recorded note: tech:oauth fires on the SSO mention. The chapter may apply and the input never settles it.

> mark:


### cryptography

**A5.** `V11.3.2` — No cipher is stated for either the device registry or the telemetry lake at rest.

- cites: `store:device-registry`, `store:telemetry-lake`
- tier: must-find
- recorded note: Both carry encryption_at_rest unknown against confidential and customer data.

> mark:


### secure-communication

**A6.** `V12.2.1` — The MQTT publish path from the field to an internet-facing broker states no transport protection.

- cites: `entity:sensor-node`, `process:device-gateway`, `flow:sensor-node-to-device-gateway:publish-readings`
- tier: must-find
- recorded note: encryption_in_transit is unknown on a crossing into an internet-facing element.

> mark:


### encoding-and-sanitization

**A7.** `V1.2.4` — Nothing says how the normalizer builds the queries that load readings into BigQuery.

- cites: `process:telemetry-normalizer`, `store:telemetry-lake`
- tier: expected
- recorded note: tech:database fires on the BigQuery store.

> mark:


### data-protection

**A8.** `V14.2.1` — Firmware is fetched from a publicly readable bucket with no stated integrity or access control.

- cites: `entity:sensor-node`, `store:firmware-bucket`, `flow:sensor-node-to-firmware-bucket:poll-firmware`
- tier: expected
- recorded note: The bucket is stated public read, so the access half is settled.

> mark:

## Part 3 — the 18 recorded STRIDE threats

Only after your own list exists.

For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.


### spoofing

**1.** An attacker who extracts the fleet-wide pre-shared key from any one node publishes readings to the gateway as any other device in the fleet.

- cites: `flow:sensor-node-to-device-gateway:publish-readings`, `entity:sensor-node`
- tier: must-find · severity: high/high · verb: `impersonate`
- recorded note: The defining finding of this case: one shared, never-rotated key across physically exposed devices.

> mark:

**2.** An attacker with physical access presents themselves as a field technician on the node's serial console, whose authentication is unverified.

- cites: `flow:field-technician-to-sensor-node:local-service-session`, `entity:sensor-node`
- tier: must-find · severity: medium/high · verb: `impersonate`
- recorded note: Physical exposure is stated in the source; console authentication is unknown, so it must be reported unverified.

> mark:

**3.** An attacker serves a node a firmware image that impersonates an official release, because the node authenticates neither the bucket nor the image's origin.

- cites: `store:firmware-bucket`, `flow:sensor-node-to-firmware-bucket:poll-firmware`
- tier: expected · severity: medium/high · verb: `forge`
- recorded note: Origin authentication, as distinct from the tampering entry about modifying an image in place.

> mark:


### tampering

**4.** An attacker who can write to the firmware bucket plants a malicious image that every polling node installs, since image signature verification is unverified.

- cites: `store:firmware-bucket`, `entity:sensor-node`
- tier: must-find · severity: high/high · verb: `plant`
- recorded note: Fleet-wide code execution; the source explicitly flags signature checking as unknown.

> mark:

**5.** An attacker holding the fleet key injects fabricated readings that the normalizer loads into the lake as genuine customer data.

- cites: `flow:sensor-node-to-device-gateway:publish-readings`, `store:telemetry-lake`
- tier: must-find · severity: high/medium · verb: `forge`
- recorded note: Data integrity downstream of a spoofable device identity; distinct from the spoofing lane's identity claim.

> mark:

**6.** An attacker who can write to the device registry reassigns a node to a different customer, redirecting or corrupting that customer's data.

- cites: `store:device-registry`, `flow:device-gateway-to-device-registry:look-up-device`
- tier: expected · severity: low/high · verb: `alter`
- recorded note: The registry is the authority for tenancy; its own access control is unverified.

> mark:


### repudiation

**7.** A customer disputes a reading attributed to their site and no per-device identity exists to establish which node actually sent it.

- cites: `entity:sensor-node`, `store:telemetry-lake`
- tier: must-find · severity: medium/medium · verb: `unattributable`
- recorded note: Follows directly from a fleet-wide rather than per-device credential.

> mark:

**8.** A technician denies having made a configuration change on a node, and the unauthenticated local console records no actor to contradict them.

- cites: `flow:field-technician-to-sensor-node:local-service-session`
- tier: expected · severity: medium/medium · verb: `unattributable`
- recorded note: No logging is described anywhere on the service path.

> mark:


### information-disclosure

**9.** An attacker who reaches the telemetry lake reads customer site addresses and occupancy patterns, whose protection at rest is unverified.

- cites: `store:telemetry-lake`
- tier: must-find · severity: medium/high · verb: `read`
- recorded note: Occupancy data is unusually sensitive: it reveals when a physical site is empty.

> mark:

**10.** An attacker on the path between a node and the gateway reads readings and the presented key, because transport encryption on the MQTT session is unverified.

- cites: `flow:sensor-node-to-device-gateway:publish-readings`
- tier: must-find · severity: medium/high · verb: `intercept`
- recorded note: The same wire carries the credential and the data; a needs-info verdict on encryption is acceptable.

> mark:

**11.** An attacker downloads firmware images from the public bucket and reverse-engineers them to recover embedded fleet credentials or logic.

- cites: `store:firmware-bucket`
- tier: expected · severity: high/medium · verb: `read`
- recorded note: Public read is stated, not inferred; pairs with the fleet-key finding.

> mark:

**12.** An attacker who reaches the device registry reads the key material and customer assignments it holds, whose protection at rest is unverified.

- cites: `store:device-registry`
- tier: expected · severity: medium/high · verb: `read`
- recorded note: The registry is tagged credentials; disclosure here is equivalent to fleet compromise.

> mark:


### denial-of-service

**13.** An attacker floods the internet-exposed MQTT broker with connections until genuine nodes can no longer publish readings.

- cites: `process:device-gateway`, `flow:sensor-node-to-device-gateway:publish-readings`
- tier: must-find · severity: high/high · verb: `flood`
- recorded note: The gateway is the single ingest point for the whole fleet and is tagged availability-critical.

> mark:

**14.** An attacker publishes a firmware image that bricks every node that installs it, taking the fleet offline with no remote recovery path.

- cites: `entity:sensor-node`, `store:firmware-bucket`
- tier: expected · severity: medium/high · verb: `plant`
- recorded note: Availability consequence of the same unverified update path; the devices are physically remote.

> mark:

**15.** An attacker holding the fleet key floods the ingest path with readings until the normalizer falls behind and dashboards stop reflecting the fleet.

- cites: `process:telemetry-normalizer`, `store:telemetry-lake`
- tier: expected · severity: medium/medium · verb: `flood`
- recorded note: Cost-amplification against a metered analytics store as well as an availability effect.

> mark:


### elevation-of-privilege

**16.** An attacker turns an unsigned firmware update into code execution on every node, escalating from bucket write access to control of the physical fleet.

- cites: `entity:sensor-node`, `flow:sensor-node-to-firmware-bucket:poll-firmware`
- tier: must-find · severity: high/high · verb: `escalate`
- recorded note: The escalation framing of the firmware finding: privilege gained, not just data changed.

> mark:

**17.** An attacker who compromises one physically accessible node uses its fleet-wide credential to act as the whole fleet against the ingest edge.

- cites: `entity:sensor-node`, `process:device-gateway`
- tier: must-find · severity: high/high · verb: `escalate`
- recorded note: One-device compromise to fleet-scope privilege; the shared key is the escalation mechanism.

> mark:

**18.** An operator signed in for dashboards queries raw customer records beyond what their role needs, because no narrower grant on the lake is described.

- cites: `entity:fleet-operator`, `store:telemetry-lake`
- tier: expected · severity: medium/medium · verb: `abuse-grant`
- recorded note: SSO is stated but authorization scope is not; report as unverified rather than absent.

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

**Counts first**, kept apart per framework: how many `agree`, `doubt`, `dup`
per part, and how many of your own items are missing from either set.

- **Few doubts, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several doubts** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sitting.** Save this filled document as
`REVIEW-<your GitHub login>.md` beside the original — the filled copy is the
evidence, and the generated `REVIEW.md` stays derived and unfilled. Append
this entry to `reviews` in `evals/corpus/02-iot-fleet-telemetry/case.json`, which is what
`tests/test_case_review.py` reads:

```json
  "reviews": [
    {
      "reviewer": "<your GitHub login>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {"file": "source.md", "sha256": "fc745e273aff8be740a814f0a9b4a45d6f3c6fe39dc7c8efa2b879d4f270ac74"},
        {"file": "model.json", "sha256": "a7a4531b7a0ce193c061b7ee21f11932c79877f6ec690703ed58f1f786ffa6dd"},
        {"file": "claims/asvs.json", "sha256": "46b707d5b61403266d00c4b20064276836603f4ffb274457553d84546bfff682"},
        {"file": "claims/stride.json", "sha256": "39a3252c4363877aae9fd96d47759363dcc76f5ed9349beb81eed55c4ac9db5f"}
      ],
      "document": "REVIEW-<your GitHub login>.md",
      "notes": "<counts, and anything you changed>"
    }
  ],
```

The digests above are the files as they were when this document was
generated. If the sitting changed a file — a claim edit is a normal outcome —
recompute that file's digest (`sha256sum <file>`) before you commit: the
entry signs the bytes that merge.

If this case is named in `UNREVIEWED` in `tests/test_case_review.py`, delete
its line. That list names the cases nobody has read, so it is only accurate
while a reviewed case comes off it. A case not named there is new, and merges
with this entry from the start.

`tests/test_case_review.py` checks that `read` covers every framework the
case declares, that every digest matches, that the `document` file exists,
and that the reviewer has a line in `evals/review/voters.toml` — a first-time
contributor adds their own, standing `contributor`. Then
`python -m evals.harness.run submit sitting` opens the PR.
