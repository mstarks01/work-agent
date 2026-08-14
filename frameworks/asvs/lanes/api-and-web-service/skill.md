# API and Web Service (V4)

## Scope

Chapter V4 of ASVS 5.0: the contract an API offers its callers. Your lane covers HTTP method and content-type handling, the API-specific rules for REST, GraphQL and WebSocket surfaces, resource limits on requests, and the parts of an API's documentation the standard treats as verifiable.

Chapter boundaries: how a caller proves who they are is chapter V6. What a token contains is chapter V9. Whether a request is authorized is chapter V8. Your subject is the shape of the request and the response.

## Applicability

This chapter applies wherever the application exposes a programmatic interface, which is nearly every system this service sees. The one presence test inside it is the WebSocket requirement, which needs a flow whose `protocol` names a WebSocket.

Read the model's `DataFlow` protocols. They are the record of what each connection speaks, and they are the evidence for both the chapter's applicability and its exclusions.

### The requirements of this chapter

16 requirements across 4 sections: 2 at level 1, 8 at level 2, 6 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V4.1 Generic Web Service Security

- **V4.1.1** (L1) — Verify that every HTTP response with a message body contains a Content-Type header field that matches the actual content of the response, including the charset parameter to specify safe character encoding (e.g., UTF-8, ISO-8859-1) according to IANA Media Types, such as "text/", "/+xml" and "/xml".
- **V4.1.2** (L2) — Verify that only user-facing endpoints (intended for manual web-browser access) automatically redirect from HTTP to HTTPS, while other services or endpoints do not implement transparent redirects. This is to avoid a situation where a client is erroneously sending unencrypted HTTP requests, but since the requests are being automatically redirected to HTTPS, the leakage of sensitive data goes undiscovered.
- **V4.1.3** (L2) — Verify that any HTTP header field used by the application and set by an intermediary layer, such as a load balancer, a web proxy, or a backend-for-frontend service, cannot be overridden by the end-user. Example headers might include X-Real-IP, X-Forwarded-*, or X-User-ID.
- **V4.1.4** (L3) — Verify that only HTTP methods that are explicitly supported by the application or its API (including OPTIONS during preflight requests) can be used and that unused methods are blocked.
- **V4.1.5** (L3) — Verify that per-message digital signatures are used to provide additional assurance on top of transport protections for requests or transactions which are highly sensitive or which traverse a number of systems.

#### V4.2 HTTP Message Structure Validation

- **V4.2.1** (L2) — Verify that all application components (including load balancers, firewalls, and application servers) determine boundaries of incoming HTTP messages using the appropriate mechanism for the HTTP version to prevent HTTP request smuggling. In HTTP/1.x, if a Transfer-Encoding header field is present, the Content-Length header must be ignored per RFC 2616. When using HTTP/2 or HTTP/3, if a Content-Length header field is present, the receiver must ensure that it is consistent with the length of the DATA frames.
- **V4.2.2** (L3) — Verify that when generating HTTP messages, the Content-Length header field does not conflict with the length of the content as determined by the framing of the HTTP protocol, in order to prevent request smuggling attacks.
- **V4.2.3** (L3) — Verify that the application does not send nor accept HTTP/2 or HTTP/3 messages with connection-specific header fields such as Transfer-Encoding to prevent response splitting and header injection attacks.
- **V4.2.4** (L3) — Verify that the application only accepts HTTP/2 and HTTP/3 requests where the header fields and values do not contain any CR (\r), LF (\n), or CRLF (\r\n) sequences, to prevent header injection attacks.
- **V4.2.5** (L3) — Verify that, if the application (backend or frontend) builds and sends requests, it uses validation, sanitization, or other mechanisms to avoid creating URIs (such as for API calls) or HTTP request header fields (such as Authorization or Cookie), which are too long to be accepted by the receiving component. This could cause a denial of service, such as when sending an overly long request (e.g., a long cookie header field), which results in the server always responding with an error status.

#### V4.3 GraphQL

- **V4.3.1** (L2) — Verify that a query allowlist, depth limiting, amount limiting, or query cost analysis is used to prevent GraphQL or data layer expression Denial of Service (DoS) as a result of expensive, nested queries.
- **V4.3.2** (L2) — Verify that GraphQL introspection queries are disabled in the production environment unless the GraphQL API is meant to be used by other parties.

#### V4.4 WebSocket

- **V4.4.1** (L1) — Verify that WebSocket over TLS (WSS) is used for all WebSocket connections.
- **V4.4.2** (L2) — Verify that, during the initial HTTP WebSocket handshake, the Origin header field is checked against a list of origins allowed for the application.
- **V4.4.3** (L2) — Verify that, if the application's standard session management cannot be used, dedicated tokens are being used for this, which comply with the relevant Session Management security requirements.
- **V4.4.4** (L2) — Verify that dedicated WebSocket session management tokens are initially obtained or validated through the previously authenticated HTTPS session when transitioning an existing HTTPS session to a WebSocket channel.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**The protocol is named and the method policy is not.** A flow speaking HTTP says an API exists; it says nothing about which methods are permitted. That is the shape of most rulings here.
**A schema or specification is assumed.** Requirements about a documented API contract cannot be settled from a system description. Say the requirement applies and the input does not carry the artifact.
**Resource limits go unmentioned.** Where the model shows an internet-facing process, a request-size or complexity limit is a requirement that applies and a fact the prose rarely holds.
**No WebSocket in the model.** Rule the WebSocket requirement out on the absence, naming the protocols the flows actually state.

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
