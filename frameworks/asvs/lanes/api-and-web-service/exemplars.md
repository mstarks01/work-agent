# API and Web Service Exemplars

Two drafts against exemplar system A. The second rules a requirement out on a protocol the model states, which is the shape most exclusions take in this lane.

## V4.1.1 — The web API's accepted methods and content types are undescribed

The API exists — the model says so through the flows' protocols — and nothing says which HTTP methods it accepts.

```json
{
  "requirement": "1.1",
  "needs_evidence": "config",
  "title": "The web API's accepted methods and content types are undescribed",
  "description": "V4.1.1 asks that the application accepts only the HTTP methods it needs and rejects the rest. It applies here because `process:web-api` is `internet-facing` and `flow:customer-to-web-api:submit-payment` and `flow:payments-provider-to-web-api:settlement-webhook` both speak HTTPS to it, so an HTTP surface exists. The notes describe two callers and the payloads they send, and never describe the method or content-type policy on either endpoint. The requirement applies and the input does not settle it.",
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
  "description": "V4.4.1 governs the WebSocket handshake and the transport under it. It does not apply here. The model carries five flows and their stated protocols are HTTPS, HTTPS POST, gRPC, the PostgreSQL wire protocol and an HTTPS append; none of them is a WebSocket, and no element's technology names one. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "websocket"
  ],
  "quotes": []
}
```
