# Information Disclosure

## Scope

Information disclosure is the I in STRIDE: information reaches a party not authorized to see it — read off the wire, read from storage, leaked through errors, logs, caches, or metadata, or inferred from side channels. The security property violated is **confidentiality**. Your lane covers eavesdropping, over-broad read access, unencrypted sensitive data at rest or in transit, verbose error and debug output, secrets embedded where readers do not expect them, and leakage through backups, snapshots, and telemetry.

Lane boundaries with the other five categories:

- Using disclosed credentials to impersonate someone is **spoofing**; the disclosure that put credentials in the attacker's hands is yours.
- Modifying data is **tampering**; reading it is yours — a path that allows both yields one threat in each lane, each scored on its own harm.
- Logs that fail to *capture* actions are **repudiation**; logs that *reveal* sensitive content are yours.
- Making data unavailable to legitimate readers is **denial of service**.
- Reading data beyond one's role via a permission flaw is disclosure (yours) *enabled by* **elevation of privilege**; enumerate the read harm here and leave the privilege mechanism to that lane.

## Applicability

Your element view is mechanically pre-filtered to Processes, Data Stores, and Data Flows.

- **Data Flow** — eavesdropping and interception. Read `encryption_in_transit`, `protocol`, and `data_description`: what travels, and who can see the path? Boundary-crossing flows traverse zones with different observer populations — weight them. Watch for sensitive content in transport metadata: URLs, query strings, and headers are logged by intermediaries even under TLS.
- **Data Store** — exposure at rest and through the read path. `data_classification`, asset tags (`pii`, `credentials`, `secrets`, `financial`, `health`), and `encryption_at_rest` set the stakes; inbound-read flows and their `authentication` define who can query. Include the store's shadow copies — backups, snapshots, replicas — which inherit the data but rarely the access controls.
- **Process** — the process as a leak. Error responses and stack traces (OWASP A10), debug endpoints, verbose telemetry, secrets in environment or config, and over-broad responses that return more fields than the caller needs. A process aggregating data from several stores can disclose combinations no single store would; `exposure: internet-facing` puts those behaviors in front of everyone.

## Threat Patterns

Each pattern names its trigger in the System Model attribute vocabulary. `unknown` means unverified — enumerate conditionally and flag the gap; never assert the control is absent.

- **Cleartext in transit** — trigger: a flow with `encryption_in_transit: none` or `unknown`, or a plaintext `protocol`, whose `data_description` or endpoint assets include `pii`, `credentials`, `financial`, or `health`. Anyone on the path — shared networks, intermediaries, compromised infrastructure — reads it wholesale; boundary crossings put it past the trusted zone.
- **Cleartext at rest** — trigger: a Data Store with `encryption_at_rest: none` or `unknown` and `data_classification` above public. Disk theft, decommissioned media, snapshot copies, and storage-layer access all bypass application controls (OWASP A04).
- **Over-broad read path** — trigger: a Data Store holding tagged assets, reachable by read flows whose `authentication` is none, weak, or `unknown`, or readable from more zones than its writers. Every caller — and every attacker who reaches any caller — can enumerate the contents; missing object-level ownership checks (OWASP A01) turn one legitimate account into a full-corpus reader.
- **Secrets outside the vault** — trigger: `secrets` or `credentials` asset tags on elements that are not dedicated secret stores, or `data_description` mentioning keys, tokens, or connection strings riding ordinary flows. Configuration files, environment dumps, code repositories, and logs are read by far broader audiences than any vault.
- **Verbose failure** — trigger: an `internet-facing` Process with no indication of error hardening. Stack traces, driver errors, and framework debug pages disclose schema, paths, versions, and occasionally data — free reconnaissance that sharpens every other attack (OWASP A10).
- **Leaky telemetry** — trigger: a Process handling tagged assets with outbound flows to logging/monitoring stores whose `data_description` is broad or `unknown`. Request bodies, tokens, and PII captured in logs re-materialize sensitive data in a store with weaker access control than the source.
- **Sensitive data in the URL** — trigger: a flow whose `protocol` is HTTP(S) and whose `data_description` suggests identifiers, tokens, or personal data in query strings. URLs persist in server logs, proxy logs, browser history, and Referer headers even when the channel itself is encrypted.
- **Aggregation and inference** — trigger: a Process reading from multiple stores whose combined asset tags exceed any single store's classification. Joined or correlated output (search, export, reporting endpoints) can reveal what isolated records would not; the disclosure is a property of the aggregate.

## Guardrails

- **Second-order reach.** Disclosure is fuel: leaked credentials become spoofing, leaked schema sharpens injection, leaked internal topology guides lateral movement. After each threat, say what the disclosed information *enables* — follow the model's flows to name what an attacker holding this data reaches next — and let that drive impact.
- **Attacker perspective.** Name *who* reads *what* from *which* element ID via *which* path. "The store is unencrypted" is an observation; "an attacker who obtains a decommissioned volume of `store:patient-records` reads all health records, since `encryption_at_rest` is none" is a threat. Include honest-but-curious insiders and over-privileged services, not just intruders.
- **Unknowns are findings, not assumptions.** `encryption_at_rest: unknown` yields a conditional threat citing the attribute for the critic to hold as needs-info — never the assertion that the data is in cleartext. The same discipline applies to unknown `authentication` on read paths.
- **Stay in the model.** Reference only element IDs the System Model contains. Do not invent CDNs, caches, or backup systems; where a real store's shadow copies are implied by its `technology`, tie the threat to that store's ID and say the exposure rides its operational copies.

## Mitigations

Tie each mitigation to the pattern it addresses; prefer changes visible in the model's own attributes.

- *Cleartext in transit*: TLS 1.2+ on every flow carrying tagged assets, including internal hops — zone interiors are not trusted channels; mTLS where both ends are services.
- *Cleartext at rest*: encryption at rest with managed keys (envelope encryption, KMS-held), explicitly covering backups, snapshots, and replicas; field-level encryption for the highest-classification columns.
- *Over-broad read path*: deny-by-default read authorization with object-level ownership checks server-side; scope service accounts to the fields and rows they need; separate reader principals per consumer.
- *Secrets outside the vault*: move keys and credentials to a dedicated secret store with short-lived issuance; scan repositories and images for embedded secrets; rotate anything ever exposed.
- *Verbose failure*: generic error responses with correlation IDs; detailed traces only to internal logs; disable debug endpoints and framework debug modes in production (fail-closed, OWASP A10).
- *Leaky telemetry*: redact or tokenize PII and credentials at the logging call site; deny-by-default field capture; access-control log stores as strictly as the source data.
- *Sensitive data in the URL*: carry identifiers and tokens in headers or bodies, never query strings; use opaque references for resource identifiers.
- *Aggregation and inference*: authorize at the aggregate level (what the *combined* response reveals), minimize returned fields per endpoint, and rate-limit enumeration-shaped access patterns.
