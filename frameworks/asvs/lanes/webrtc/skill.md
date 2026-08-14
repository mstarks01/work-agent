# WebRTC (V17)

## Scope

Chapter V17 of ASVS 5.0: real-time peer-to-peer media and data channels. Your lane covers the signalling path, the media and data channel protections WebRTC specifies, TURN and STUN server handling, and the resource limits a media path needs.

Chapter boundaries: the application's ordinary API surface is chapter V4. Transport protection for non-WebRTC links is chapter V12. Your subject is the real-time channel.

This chapter carries no level 1 requirement. A run at level 1 rules on nothing here.

## Applicability

**This chapter needs WebRTC.** ASVS names it as an exclusion in its own guidance, beside OAuth: where there is no use of WebRTC, the chapter can be ignored. It is the cleanest exclusion in the standard and it applies to almost every system this service sees.

Read the model's flow protocols and technologies for WebRTC, SRTP, STUN, TURN or a data channel. Where none appears — which is the ordinary case — rule the whole chapter out and name the protocols the flows do state.

### The requirements of this chapter

12 requirements across 3 sections: 0 at level 1, 7 at level 2, 5 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V17.1 TURN Server

- **V17.1.1** (L2) — Verify that the Traversal Using Relays around NAT (TURN) service only allows access to IP addresses that are not reserved for special purposes (e.g., internal networks, broadcast, loopback). Note that this applies to both IPv4 and IPv6 addresses.
- **V17.1.2** (L3) — Verify that the Traversal Using Relays around NAT (TURN) service is not susceptible to resource exhaustion when legitimate users attempt to open a large number of ports on the TURN server.

#### V17.2 Media

- **V17.2.1** (L2) — Verify that the key for the Datagram Transport Layer Security (DTLS) certificate is managed and protected based on the documented policy for management of cryptographic keys.
- **V17.2.2** (L2) — Verify that the media server is configured to use and support approved Datagram Transport Layer Security (DTLS) cipher suites and a secure protection profile for the DTLS Extension for establishing keys for the Secure Real-time Transport Protocol (DTLS-SRTP).
- **V17.2.3** (L2) — Verify that Secure Real-time Transport Protocol (SRTP) authentication is checked at the media server to prevent Real-time Transport Protocol (RTP) injection attacks from leading to either a Denial of Service condition or audio or video media insertion into media streams.
- **V17.2.4** (L2) — Verify that the media server is able to continue processing incoming media traffic when encountering malformed Secure Real-time Transport Protocol (SRTP) packets.
- **V17.2.5** (L3) — Verify that the media server is able to continue processing incoming media traffic during a flood of Secure Real-time Transport Protocol (SRTP) packets from legitimate users.
- **V17.2.6** (L3) — Verify that the media server is not susceptible to the "ClientHello" Race Condition vulnerability in Datagram Transport Layer Security (DTLS) by checking if the media server is publicly known to be vulnerable or by performing the race condition test.
- **V17.2.7** (L3) — Verify that any audio or video recording mechanisms associated with the media server are able to continue processing incoming media traffic during a flood of Secure Real-time Transport Protocol (SRTP) packets from legitimate users.
- **V17.2.8** (L3) — Verify that the Datagram Transport Layer Security (DTLS) certificate is checked against the Session Description Protocol (SDP) fingerprint attribute, terminating the media stream if the check fails, to ensure the authenticity of the media stream.

#### V17.3 Signaling

- **V17.3.1** (L2) — Verify that the signaling server is able to continue processing legitimate incoming signaling messages during a flood attack. This should be achieved by implementing rate limiting at the signaling level.
- **V17.3.2** (L2) — Verify that the signaling server is able to continue processing legitimate signaling messages when encountering malformed signaling message that could cause a denial of service condition. This could include implementing input validation, safely handling integer overflows, preventing buffer overflows, and employing other robust error-handling techniques.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**No WebRTC in the model.** Rule the chapter out on the stated protocols. This is the expected outcome, and it is an answer rather than a gap.
**A media path exists and its signalling is undescribed.** Where WebRTC does appear, how the peers find each other is the first requirement to rule on.
**TURN is implied by the topology.** A peer-to-peer path across a NAT boundary needs a relay, and the requirements on it apply once one exists.
**Resource limits on a media path are unmentioned.** Concurrent channel limits are a requirement wherever a media path is open to untrusted callers.

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
