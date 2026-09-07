# API and Web Service Exemplars

Two drafts against exemplar system A. The second rules a requirement out on a protocol the model states, which is the shape most exclusions take in this lane.

## V4.1.1 — The web API's response content types are never described

The API exists — the model says so through the flows' protocols — and nothing says what its responses declare themselves to be.

```json
{
  "requirement": "1.1",
  "needs_evidence": "code",
  "title": "The web API's response content types are never described",
  "description": "V4.1.1 asks that each response carrying a body labels it with a Content-Type that matches what the body actually holds, charset included. It applies here because `process:web-api` is `internet-facing` and `flow:customer-to-web-api:submit-payment` and `flow:payments-provider-to-web-api:settlement-webhook` both speak HTTPS to it, so HTTP responses exist. The notes describe two callers and the payloads they send, and never describe what the web API sends back or how it labels a response. The requirement applies and the input does not settle it; the response handling in `process:web-api` would.",
  "affected_element_ids": [
    "process:web-api",
    "flow:customer-to-web-api:submit-payment",
    "flow:payments-provider-to-web-api:settlement-webhook"
  ],
  "evidence_refs": [
    "crossing:flow:payments-provider-to-web-api:settlement-webhook"
  ],
  "quotes": []
}
```
## V4.4.1 — No WebSocket surface exists in this system

The WebSocket requirement, ruled out on the stated protocols. Note what the draft cites: not the absence of the word, but the protocols the five flows actually name.

```json
{
  "requirement": "4.1",
  "needs_evidence": "",
  "title": "No WebSocket surface exists in this system",
  "description": "V4.4.1 asks that every WebSocket connection runs over TLS. It does not apply here. The model carries five flows and their stated protocols are HTTPS, HTTPS POST, gRPC, the PostgreSQL wire protocol and an HTTPS append; none of them is a WebSocket, and no element's technology names one. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "websocket"
  ],
  "quotes": []
}
```
