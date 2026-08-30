# WebRTC Exemplars

Two drafts against exemplar system A. This chapter carries no level 1 requirement, and ASVS names it as one an operator may ignore where there is no WebRTC — which is the ordinary case. Both drafts are exclusions, and both name what the model states rather than what it omits.

## V17.2.1 — This system carries no WebRTC media path

The exclusion, resting on the protocols the five flows state.

```json
{
  "requirement": "2.1",
  "needs_evidence": "",
  "title": "This system carries no WebRTC media path",
  "description": "V17.2.1 governs the protection of a WebRTC media stream. It does not apply here. The five flows in this model state HTTPS, HTTPS POST, gRPC, the PostgreSQL wire protocol and an HTTPS append; none is a WebRTC session, and no element's technology names SRTP, STUN, TURN or a data channel. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "The web API hands each transfer to the ledger service over gRPC",
      "source_label": "Payments platform notes"
    }
  ]
}
```

## V17.3.1 — No WebRTC signalling path exists to protect

A second requirement excluded on the same fact, stated once rather than argued again.

```json
{
  "requirement": "3.1",
  "needs_evidence": "",
  "title": "No WebRTC signalling path exists to protect",
  "description": "V17.3.1 governs the signalling channel two WebRTC peers use to find each other. It does not apply here, on the same stated fact that rules out V17.2.1: no flow in this model is a WebRTC session and no element is a peer, a relay or a signalling server. The requirement has no subject in this system.",
  "affected_element_ids": [],
  "evidence_refs": [],
  "quotes": [
    {
      "text": "They submit payments through the web API, which is the only thing we expose to the internet.",
      "source_label": "Payments platform notes"
    }
  ]
}
```
