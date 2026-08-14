# HTTP and API Security

## When this applies

The System Model carries flows over HTTP/HTTPS, REST, gRPC, GraphQL or webhooks, or a Process marked `exposure: internet-facing`.

## What to look for

Read these against the flow and element attributes the model actually carries. Each line names the attribute that triggers it.

- **Authorization is per-object, not per-route.** A flow whose `authentication` names a mechanism says who the caller is, never what they may reach. Where one endpoint serves records belonging to different callers, ask what binds the record to the identity — an ID in the path or body is a request parameter, not a claim. This is the most common real finding in an API model and it is invisible in the `authentication` attribute, which is why it has to be asked rather than read.
- **Bearer credentials in the wrong place.** `authentication` describing an API key, token or session cookie: ask where it travels. A credential in a query string lands in access logs, proxies and `Referer` headers; one in a cookie without `SameSite` is replayable by any origin the browser will talk to.
- **Server-side request forgery.** A Process whose `data_description` or `technology` shows it fetches a caller-supplied URL, renders a caller-supplied document, or proxies. Its outbound flows are the attacker's reach: SSRF converts an internet-facing process into a caller inside the trust zone, so score impact on `reachable_from` the process, including metadata endpoints the model may not name.
- **Webhooks and callbacks.** An inbound flow from an `external-system` entity: signature verification, replay windows and delivery-order assumptions are all things a model states or leaves `unknown`. An unsigned callback is an unauthenticated write from the internet with the shape of an integration.
- **Content-type and parser reach.** `protocol` or `data_description` naming XML, YAML, or file upload: parsers reach further than the endpoint does — external entities, archive extraction paths, image and document processing.
- **Rate limiting and pagination.** An `internet-facing` Process with no stated throttling: unauthenticated endpoints, expensive queries and unbounded page sizes are amplification against everything `reachable_from` it.
- **Error and header surface.** Verbose errors distinguish "no such account" from "wrong password", which is enumeration; missing HSTS and permissive CORS on a browser-facing flow widen who can originate authenticated requests.
- **Versioned and forgotten endpoints.** Where the model names more than one API version or a legacy path, the old one usually kept the old authorization.

## Guardrails

- This pack is **analysis knowledge, not evidence.** Nothing here is a fact about the system under analysis. A finding is still grounded in the submitter's words, an `unknown` attribute, or a derived crossing — never in a pattern named above.
- Do not assert a control is missing because this pack lists it. If the model is silent, the attribute is `unknown` and the finding is conditional.
- Do not file a threat about a component the System Model does not contain. A gateway, CDN or WAF that "would normally" sit in front of this API is not in the model and is not yours to assume in either direction.
