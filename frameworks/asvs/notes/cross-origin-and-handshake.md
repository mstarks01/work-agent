# Cross-Origin Callers and Upgraded Connections

## When this applies

The model shows a service answering a caller from another origin, or carrying a WebSocket. Chapters V3 and V4 ask which origins are trusted and what authenticates a connection that outlives the request that opened it.

## What to look for

- **An allow-list is the requirement, and `*` is the failure.** Reflecting the caller's `Origin` header back is the common shape and is equivalent to allowing everything. A description naming partner integrations or a separate frontend domain puts this in scope.
- **Credentials change the stakes.** Cross-origin requests that carry cookies or authorization headers are a different requirement from ones that do not, because the browser will attach ambient authority.
- **A WebSocket handshake is where authorization happens.** After the upgrade there is no per-request header to check, so the requirement is that the handshake authenticates and authorizes, and that the origin is verified there.
- **The upgrade does not inherit the page's checks.** A socket opened from an authenticated page is a separate connection; the requirement asks what the socket itself established.
- **Message-level rules still apply.** Input reaching the application over a socket is input, so V2 applies to it exactly as it does to a form post.

## Guardrails

- Analysis knowledge, not evidence. Ground the finding in the flow, the protocol or the prose that named the integration.
- Rule applicability, never a pass. A stated allow-list does not confirm its contents.
- If nothing in the model answers a caller from another origin and no socket appears, record these as not applicable rather than raising a conditional ruling on a surface that is not there.
