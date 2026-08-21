# What Reaches the Log, and What Must Not

## When this applies

The model names logging, an audit trail, telemetry, alerting or error handling. Chapter V16 covers both halves: recording enough to reconstruct what happened, and keeping the wrong things out of the record.

## What to look for

- **Two requirements pulling opposite ways.** Security-relevant events must be recorded; credentials, tokens, session identifiers and personal data must not be. A description of verbose logging is a lead on the second as much as on the first.
- **Attribution needs an identity.** A log that records an action without the caller who took it satisfies the letter and not the purpose. A shared account upstream is what usually makes this impossible, which is why this chapter and V13 often raise together.
- **Alerting is named separately from logging.** 5.0 renamed the corresponding Top 10 category to say so. Logs nobody reads and nothing watches are a ruling on the alerting requirement.
- **Errors are the other half of the chapter.** What a failure returns to the caller — a stack trace, an internal path, a database message — is a requirement here, and it is distinct from what gets logged.
- **Fail closed.** Whether a failure in a security control leaves access open or shut is this chapter's subject, and a description of a fallback path is a fair place to ask.
- **Protecting the log itself.** Whether entries can be altered or deleted by the party they describe is a requirement.

## Guardrails

- Analysis knowledge, not evidence. Cite the element or prose that named the logging or the error behaviour.
- Rule applicability, never a pass. A named log pipeline does not confirm what it captures or what it redacts.
- Sensitive data reaching a *client* is V14. Sensitive data reaching a *log* is here.
