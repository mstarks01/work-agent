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

## Part 1 — the system

### System description (description)

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
| process:telemetry-normalizer | unknown | non-web | boundary:analytics-core | Python Pub/Sub consumer |

**Data stores**

| id | zone | at rest | classification |
|---|---|---|---|
| store:device-registry | boundary:ingest-edge | unknown | confidential |
| store:telemetry-lake | boundary:analytics-core | unknown | confidential |
| store:firmware-bucket | boundary:ingest-edge | unknown | public |
| store:pub-sub | boundary:ingest-edge | unknown | unknown |

**Data flows**

| id | source | destination | protocol | authentication | in transit |
|---|---|---|---|---|---|
| flow:entity:sensor-node>process:device-gateway>publish-readings | entity:sensor-node | process:device-gateway | MQTT | fleet-wide pre-shared key, shared by every device and never rotated | unknown |
| flow:process:device-gateway>store:device-registry>look-up-device | process:device-gateway | store:device-registry | Firestore API | unknown | unknown |
| flow:process:telemetry-normalizer>store:telemetry-lake>load-readings | process:telemetry-normalizer | store:telemetry-lake | BigQuery API | unknown | unknown |
| flow:entity:sensor-node>store:firmware-bucket>poll-firmware | entity:sensor-node | store:firmware-bucket | HTTPS | none; the bucket is public read | unknown |
| flow:entity:field-technician>entity:sensor-node>local-service-session | entity:field-technician | entity:sensor-node | local serial console | unknown | unknown |
| flow:entity:fleet-operator>store:telemetry-lake>query-dashboards | entity:fleet-operator | store:telemetry-lake | BigQuery API | company SSO | unknown |
| flow:process:device-gateway>store:pub-sub>forward-readings | process:device-gateway | store:pub-sub | unknown | unknown | unknown |
| flow:process:telemetry-normalizer>store:pub-sub>pick-up-readings | process:telemetry-normalizer | store:pub-sub | unknown | unknown | unknown |

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
- `store:telemetry-lake` — The telemetry lake holds confidential data under the scheme in prompts/extract.md. (basis: The source says the lake has "site addresses and occupancy patterns" in it and calls it customer data. Both describe identifiable customer premises and when they are occupied, so disclosure harms the customers described.)
- `store:pub-sub` — Pub/Sub sits in the ingest edge rather than the analytics network. (basis: The source places the normalizer "in our analytics network" and places the broker nowhere. It associates the broker with the gateway's onward hop — "Readings the gateway accepts are forwarded onto Pub/Sub" — so it takes the gateway's zone. Either placement leaves exactly one crossing on this path.)
- `entity:field-technician` — field technician's trust_zone is boundary:field-network, which the schema requires and no source states. (basis: A local service session supports proximity to the node. It does not establish membership in a common network across customer sites.)
- `store:device-registry` — device registry's trust_zone is boundary:ingest-edge, which the schema requires and no source states. (basis: Device-registry function and confidential classification do not establish ingest-edge membership. No placement evidence is supplied.)
- `store:pub-sub` — Pub/Sub's trust_zone is boundary:ingest-edge, which the schema requires and no source states. (basis: An existing assumption label establishes how a claim is represented, not whether it is defensible. No supporting placement evidence is supplied.)
- `entity:sensor-node` — sensor node's trust_zone is boundary:field-network, which the schema requires and no source states. (basis: Customer-site installation supports a field grouping. Preserve separate site boundaries where relevant; this infers no shared connectivity or trust.)
- `process:device-gateway` — device gateway's trust_zone is boundary:ingest-edge, which the schema requires and no source states. (basis: An internet-facing ingest broker supports an edge-role abstraction. GKE hosting and internet exposure do not establish a particular network perimeter.)
- `store:telemetry-lake` — telemetry lake sits in boundary:analytics-core, which the schema requires and no source states. (basis: An analytics role does not establish network membership. The zone is retained because the schema requires one, not because the source places it.)
- `store:firmware-bucket` — firmware bucket sits in boundary:ingest-edge, which the schema requires and no source states. (basis: No sentence places it. The zone is retained because the schema requires one. Public-read access is not the justification: it is an access policy and supports no placement.)

**Reviewed aliases** — other names a reader ruled identify the same element, each with the words in the source that support it. An extraction using one is named differently, not wrong.

- `store:firmware-bucket — Cloud Storage bucket` — Supported shorthand for the bucket supplying firmware images. Source: > nodes poll a Cloud Storage bucket
- `boundary:analytics-core — analytics network` — The source explicitly places the consumer in the analytics network. Source: > a Python consumer in our analytics network

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

## Part 2 — the 13 recorded STRIDE threats

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

**1.** An attacker who extracts the fleet-wide pre-shared key from any one node publishes readings to the gateway as any other device in the fleet.

- `flow:entity:sensor-node>process:device-gateway>publish-readings`, `entity:sensor-node`
- severity: high/high · verb: `use-credential`
- The defining finding of this case: one shared, never-rotated key across physically exposed devices. The attacker holds a key they were not issued, which is use-credential rather than impersonate.

> mark:

**2.** An attacker with physical access presents themselves as a field technician on the node's serial console, whose authentication is unverified.

- `flow:entity:field-technician>entity:sensor-node>local-service-session`, `entity:sensor-node`
- severity: medium/high · verb: `impersonate`
- Physical exposure is stated in the source; console authentication is unknown, so it must be reported unverified.

> mark:

**3.** An attacker who gains write access to the firmware bucket serves a node an image it installs as an official release, if the node checks no signature on what it downloads, which is unverified.

- `store:firmware-bucket`, `flow:entity:sensor-node>store:firmware-bucket>poll-firmware`
- severity: medium/high · verb: `forge`
- Origin authentication of the image, as distinct from the tampering entry about modifying an image in place. The poll runs over HTTPS, so the bucket itself is authenticated; what is unverified is whether the node checks a signature.

> mark:


### tampering

**4.** An attacker who can write to the firmware bucket plants a malicious image that every polling node installs, since image signature verification is unverified.

- `store:firmware-bucket`, `entity:sensor-node`
- severity: high/high · verb: `plant`
- Fleet-wide code execution; the source explicitly flags signature checking as unknown.

> mark:

**5.** An attacker who can write to the device registry reassigns a node to a different customer, redirecting or corrupting that customer's data.

- `store:device-registry`, `flow:process:device-gateway>store:device-registry>look-up-device`
- severity: low/high · verb: `alter`
- The registry is the authority for tenancy; its own access control is unverified.

> mark:


### repudiation

**6.** A customer disputes a reading attributed to their site and no per-device identity exists to establish which node actually sent it.

- `entity:sensor-node`, `store:telemetry-lake`
- severity: medium/medium · verb: `unattributable`
- Follows directly from a fleet-wide rather than per-device credential.

> mark:


### information-disclosure

**7.** An attacker who reaches the telemetry lake reads customer site addresses and occupancy patterns, whose protection at rest is unverified.

- `store:telemetry-lake`
- severity: medium/high · verb: `read`
- Occupancy data is unusually sensitive: it reveals when a physical site is empty.

> mark:

**8.** An attacker on the path between a node and the gateway reads readings and the presented key, because transport encryption on the MQTT session is unverified.

- `flow:entity:sensor-node>process:device-gateway>publish-readings`
- severity: medium/high · verb: `intercept`
- The same wire carries the credential and the data; a needs-info verdict on encryption is acceptable.

> mark:

**9.** An attacker downloads firmware images from the public bucket and studies what they hold, and nothing records whether an image carries a credential or logic meant to stay secret.

- `store:firmware-bucket`
- severity: high/medium · verb: `read`
- Public read is stated, not inferred. What the image contains is not, so the disclosure turns on that and pairs with the fleet-key finding.

> mark:

**10.** An attacker who reaches the device registry reads the key material and customer assignments it holds, whose protection at rest is unverified.

- `store:device-registry`
- severity: medium/high · verb: `read`
- The registry is tagged credentials; disclosure here is equivalent to fleet compromise.

> mark:


### denial-of-service

**11.** An attacker floods the internet-exposed MQTT broker with connections until genuine nodes can no longer publish readings.

- `process:device-gateway`, `flow:entity:sensor-node>process:device-gateway>publish-readings`
- severity: high/high · verb: `flood`
- The gateway is the single ingest point for the whole fleet.

> mark:

**12.** An attacker holding the fleet key floods the ingest path with readings until the normalizer falls behind and dashboards stop reflecting the fleet.

- `process:telemetry-normalizer`, `store:telemetry-lake`
- severity: medium/medium · verb: `flood`
- Cost-amplification against a metered analytics store as well as an availability effect.

> mark:


### elevation-of-privilege

**13.** An operator signed in for dashboards queries raw customer records beyond what their role needs, because no narrower grant on the lake is described.

- `entity:fleet-operator`, `store:telemetry-lake`
- severity: medium/medium · verb: `abuse-grant`
- SSO is stated but authorization scope is not; report as unverified rather than absent.

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
    "02-iot-fleet-telemetry": {
      "own_list": ["<what you wrote before the sets opened>"],
      "marks": {"<finding fingerprint>": "agree | reject | duplicate | unsure"},
      "missing": ["<what the recorded sets do not name>"],
      "notes": "<counts, and anything you would change>",
      "opened_digests": {
      "source.md": "fc745e273aff8be740a814f0a9b4a45d6f3c6fe39dc7c8efa2b879d4f270ac74",
      "model.json": "fbe6f9b1304f947a2efe454df07391e4a430acf808055be11c5b3bb7911995a4",
      "claims/stride.json": "3e1d15ee570a17f38fee52b864b31d9af5c5fbac64c51fb4f29f670a22b5db8d"
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
