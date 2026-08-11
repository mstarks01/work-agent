# Writes Into a Store, and Who Reads Them Later

## When this applies

A flow writes into a data store and the caller's identity is unverified, or the write path's own integrity is unstated.

## What to look for

- **The second reader is the target.** Data written by an attacker is rarely dangerous where it lands; it is dangerous where something else consumes it — a rendering layer, a rules engine, a report, a scheduler, another service's import job. Follow the store to its readers before deciding the severity.
- **Stored content that becomes instruction.** Configuration rows, templates, feature flags, allow-lists, scheduled job definitions and message bodies are all data that something later executes or obeys. A write into any of them is a control-flow question wearing a data question's clothes.
- **What the write can overwrite.** An unconstrained write path usually reaches more rows than the caller's own: another tenant's record, an audit row, a prior version, a status field that gates a workflow.
- **Whether anything detects it afterwards.** Checksums, append-only tables, versioning and out-of-band reconciliation are what make a tampered row recoverable. Their absence turns a write into a permanent one.
- **Ingest paths that bypass the application.** Bulk loaders, ETL jobs, restore procedures and direct database access often skip whatever validation the service performs, and the model usually draws them as ordinary flows.

## Guardrails

- Analysis knowledge, not evidence. What a store *could* feed is a question for this model, not a fact carried in from here.
- Name the downstream consumer in the finding. A tampering threat that stops at "an attacker can write to the table" is an observation about a control, and the critic will treat it as one.
- Writing data you should not is tampering; reading data you should not is information disclosure; doing either as somebody else is spoofing first.
