# Context

Glossary for the STRIDE threat-modeling service. The canonical system model is the extraction agent's output and every downstream agent's input.

## Glossary

- **System Model** — the canonical, structured representation of the system under analysis, produced by the extraction agent from semi-structured front-end text. The single source all analyst agents consume.
- **Element** — any node or edge in the System Model. Exactly five types (classic DFD-based STRIDE-per-element taxonomy): External Entity, Process, Data Store, Data Flow, Trust Boundary.
- **External Entity** — an actor outside the system's control: users, third-party systems.
- **Process** — running code or a component that transforms data.
- **Data Store** — where data rests: databases, buckets, queues-at-rest.
- **Data Flow** — a directed connection carrying data between two elements. One flow per interaction; direction = who initiates, the response rides implicitly. An independent reverse-direction interaction (webhook, push) is its own flow.
- **Trust Boundary** — a named trust zone (flat, no nesting): network zones, auth boundaries, privilege levels. Every External Entity, Process, and Data Store belongs to exactly one zone via `trust_zone`.
- **Boundary Crossing** — a derived fact, never extracted: a Data Flow crosses a boundary iff its endpoints' zones differ. The highest-signal STRIDE input, computed mechanically so it cannot contradict the model.
- **Asset** — something worth protecting. Not an element type: modeled as tags on Elements from a small controlled vocabulary (`credentials`, `pii`, `financial`, `health`, `secrets`, `business-critical-data`, `availability-critical`, `reputation`; config-extendable). Feeds impact scoring and skill selection; system-specific detail lives in the element's own name/description.
- **Unknown** — a legal attribute value meaning the input neither stated nor allowed inference of a fact. Analysts treat an unknown security control as unverified, never as present or absent.
- **Assumption** — a value extraction inferred with a stated basis. Recorded on the System Model's top-level assumptions list (assumption, element ID, basis) as well as in the attribute itself. Unknown = not stated; Assumption = inferred on the record. Never a silent guess.
- **Threat** — a STRIDE finding. Always traceable to the Element(s) it affects.
- **Analyst** — one of six parallel agents, each owning a single STRIDE category across all applicable Elements. Drafts complete threat entries (description, element refs, severity, mitigations).
- **Critic** — the single strong-model agent that reviews the merged union of analyst output in one pass: per-threat Verdicts, cross-category dedupe, severity calibration. Grounded in System Model facts, never free-form reflection.
- **Verdict** — the critic's per-threat ruling: confirm, reject (with reason, kept for audit), or needs-info (kept, flagged, tied to unknown attributes).
- **Valid System Model** — one that passes the mechanical well-formedness gate (unique typed IDs, referential integrity, zone membership, legal enum values). Analysts only ever see valid models. An invalid extraction gets one Repair Pass — validator errors fed back to the extraction agent — then the job fails with a structured error. Never silent auto-repair.
- **Source Excerpt** — a short quote from the user's input text that an Element was extracted from. Carried by every Element for traceability (threat → element → user's own words) and extraction evals.
- **Element ID** — a typed, human-readable slug, unique within one System Model (one job): `<type>:<normalized-name>`, e.g. `process:auth-service`. Data Flows use `flow:<source>-to-<dest>:<label>`. Threats reference Elements by these IDs.
