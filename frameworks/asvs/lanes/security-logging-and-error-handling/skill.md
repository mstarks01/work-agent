# Security Logging and Error Handling (V16)

## Scope

Chapter V16 of ASVS 5.0: what the application records and what it reveals when something goes wrong. Your lane covers which security events are logged, what a log entry may and may not contain, the protection of log data itself, and error handling that neither leaks internals nor fails open.

Chapter boundaries: who may read a log is chapter V8. Encrypting a log store is chapter V11 and V14. Your subject is the record of what happened.

This chapter carries no level 1 requirement. A run at level 1 rules on nothing here, and that is the standard's ranking rather than a judgement about logging.

## Applicability

This chapter applies to every application, at level 2 and above. Its evidence in the model is a log store where one exists, and the flows that reach it.

Where the input describes no logging at all, that is a stated absence for some requirements and an open question for others — the standard asks both that events are logged and that logs are protected, and a system with no log answers the first and not the second. Keep them apart.

### The requirements of this chapter

17 requirements across 5 sections: 0 at level 1, 16 at level 2, 1 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V16.1 Security Logging Documentation

- **V16.1.1** (L2) — Verify that an inventory exists documenting the logging performed at each layer of the application's technology stack, what events are being logged, log formats, where that logging is stored, how it is used, how access to it is controlled, and for how long logs are kept.

#### V16.2 General Logging

- **V16.2.1** (L2) — Verify that each log entry includes necessary metadata (such as when, where, who, what) that would allow for a detailed investigation of the timeline when an event happens.
- **V16.2.2** (L2) — Verify that time sources for all logging components are synchronized, and that timestamps in security event metadata use UTC or include an explicit time zone offset. UTC is recommended to ensure consistency across distributed systems and to prevent confusion during daylight saving time transitions.
- **V16.2.3** (L2) — Verify that the application only stores or broadcasts logs to the files and services that are documented in the log inventory.
- **V16.2.4** (L2) — Verify that logs can be read and correlated by the log processor that is in use, preferably by using a common logging format.
- **V16.2.5** (L2) — Verify that when logging sensitive data, the application enforces logging based on the data's protection level. For example, it may not be allowed to log certain data, such as credentials or payment details. Other data, such as session tokens, may only be logged by being hashed or masked, either in full or partially.

#### V16.3 Security Events

- **V16.3.1** (L2) — Verify that all authentication operations are logged, including successful and unsuccessful attempts. Additional metadata, such as the type of authentication or factors used, should also be collected.
- **V16.3.2** (L2) — Verify that failed authorization attempts are logged. For L3, this must include logging all authorization decisions, including logging when sensitive data is accessed (without logging the sensitive data itself).
- **V16.3.3** (L2) — Verify that the application logs the security events that are defined in the documentation and also logs attempts to bypass the security controls, such as input validation, business logic, and anti-automation.
- **V16.3.4** (L2) — Verify that the application logs unexpected errors and security control failures such as backend TLS failures.

#### V16.4 Log Protection

- **V16.4.1** (L2) — Verify that all logging components appropriately encode data to prevent log injection.
- **V16.4.2** (L2) — Verify that logs are protected from unauthorized access and cannot be modified.
- **V16.4.3** (L2) — Verify that logs are securely transmitted to a logically separate system for analysis, detection, alerting, and escalation. The aim is to ensure that if the application is breached, the logs are not compromised.

#### V16.5 Error Handling

- **V16.5.1** (L2) — Verify that a generic message is returned to the consumer when an unexpected or security-sensitive error occurs, ensuring no exposure of sensitive internal system data such as stack traces, queries, secret keys, and tokens.
- **V16.5.2** (L2) — Verify that the application continues to operate securely when external resource access fails, for example, by using patterns such as circuit breakers or graceful degradation.
- **V16.5.3** (L2) — Verify that the application fails gracefully and securely, including when an exception occurs, preventing fail-open conditions such as processing a transaction despite errors resulting from validation logic.
- **V16.5.4** (L3) — Verify that a "last resort" error handler is defined which will catch all unhandled exceptions. This is both to avoid losing error details that must go to log files and to ensure that an error does not take down the entire application process, leading to a loss of availability.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Events are logged without a stated set.** Which security events reach the log is the chapter's core requirement, and "we log everything" does not answer it.
**Log content is unconstrained in the telling.** Whether credentials or classified data reach the log is its own requirement, and one an input rarely settles.
**A log store exists with no stated protection.** Cite the store and its `encryption_at_rest` and `data_classification` attributes directly.
**Error handling is unmentioned.** Whether an error reveals internals, and whether a failed control fails closed, are requirements that apply to every system.

## Guardrails

- **Rule the requirement, do not restate it.** A claim whose description repeats the published text has said nothing about this system. Name the fact of *this* model that makes the requirement apply, and what the input does or does not show about it.
- **Unknown is not absent.** When an attribute reads `unknown`, the control is unverified. Write the ruling conditionally, cite the element and the attribute, and let the critic mark it needs-info. An attribute reading `none` is the opposite: the submitter answered, so write that ruling plainly.
- **Never report a pass.** The input carries prose, not source code or configuration, so "this requirement is satisfied" is not a conclusion available to you. Where the input describes a control that looks sufficient, say what it describes and what remains unverified.
- **Never use the word compliance.** This run rules on applicability, and a level-filtered run covers a subset of the standard. Neither is a compliance result.
- **Stay in the model.** Reference only element IDs the System Model carries. A requirement about a coding practice has no position in the graph — leave `affected_element_ids` empty rather than reaching for the nearest element.
- **One ruling per requirement.** Do not merge two requirements whose subjects are close: the standard separated them, and a reader cites them separately.

## Mitigations

This record carries no mitigations, and that is a decision rather than an omission: **the requirement text is the remedy**. A reader who wants to know what to do reads the requirement your claim cites, in the published standard, at the version your claim's ID names.

So do not write a countermeasure into the description. What belongs there is what the requirement's subject looks like *in this system* — which element, which attribute, which stated fact — because that is what the standard's text cannot supply and what makes the citation actionable.

Where a ruling is needs-info, write **the question**: the one fact the submitter could supply that would settle it.
