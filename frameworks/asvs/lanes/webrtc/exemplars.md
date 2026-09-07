# WebRTC Exemplars

Two drafts against exemplar system A. This chapter carries no level 1 requirement, and ASVS names it as one an operator may ignore where there is no WebRTC — which is the ordinary case. Both drafts are exclusions, and both ground the absence in `absent_elements`: the term is the thing the model does not have, and the service confirms no element names it. Neither reaches for a quote about an unrelated part of the system, because a quote about what *is* there grounds nothing about what is not.

## V17.2.1 — This system carries no DTLS certificate to manage a key for

The exclusion, resting on the terms no element in the model names.

```json
{
  "requirement": "2.1",
  "needs_evidence": "",
  "title": "This system carries no DTLS certificate to manage a key for",
  "description": "V17.2.1 asks that the private key behind a DTLS certificate is managed under the documented key-management policy. It does not apply here. The five flows in this model state HTTPS, HTTPS POST, gRPC, the PostgreSQL wire protocol and an HTTPS append; none is a WebRTC session, and no element's technology names SRTP, STUN, TURN or a data channel. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "webrtc",
    "srtp"
  ],
  "quotes": []
}
```

## V17.3.1 — No WebRTC signalling server exists to rate-limit

A second requirement excluded on the same fact, stated once rather than argued again.

```json
{
  "requirement": "3.1",
  "needs_evidence": "",
  "title": "No WebRTC signalling server exists to rate-limit",
  "description": "V17.3.1 asks that a signalling server keeps serving legitimate signalling messages under a flood, by rate limiting at the signalling layer. It does not apply here, on the same stated fact that rules out V17.2.1: no flow in this model is a WebRTC session and no element is a peer, a relay or a signalling server. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "absent_elements": [
    "webrtc",
    "signalling"
  ],
  "quotes": []
}
```
