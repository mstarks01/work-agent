# Sensitive Data That Reaches the Client

## When this applies

The model shows a process serving a browser or another client the operator does not control, and something in the system holds data worth protecting. Chapter V14 asks what stops that data from being sent, cached or retained where it should not be.

## What to look for

- **Sent is not the same as displayed.** An API returning a whole record and a frontend rendering three fields of it has still sent the whole record. The requirement is about what leaves the server.
- **Caching is a requirement, not a performance setting.** Responses carrying personal or financial data need directives that stop a shared cache, a proxy or the browser from retaining them.
- **Client-side storage is retention.** Local storage, session storage, IndexedDB and a service-worker cache all keep data after the tab closes, and each is its own ruling.
- **URLs leak by design.** Data in a query string reaches logs, referrer headers, browser history and analytics. A description of a link carrying an identifier or a token is squarely in scope.
- **Classification decides which requirements apply.** The chapter turns on the data being sensitive, so the ruling needs the model's own `data_classification` or the submitter's description of what is held.
- **Documented decisions.** V14 asks the organization to document which data is sensitive and how it is handled — a requirement a system can fail while every technical control is present.

## Guardrails

- Analysis knowledge, not evidence. Cite the store's classification, the flow's data description, or the prose that named the data.
- Rule applicability, never a pass. A stated redaction does not confirm which fields it covers.
- Encryption of the stored copy is V11's primitive question; the transport is V12. What reaches the client belongs here.
