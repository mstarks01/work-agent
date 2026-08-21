# Real-Time Media and the Connection That Carries It

## When this applies

The model names WebRTC, a peer connection, a STUN or TURN server, SDP, a data channel, or a voice, video or screen-sharing feature. Chapter V17 covers it, and it is the chapter most likely to be absent from a system entirely.

## What to look for

- **Signalling is where authorization happens.** The peer connection is negotiated over a channel the application controls, and that negotiation is what decides who may join. The requirement attaches to the signalling path, not to the media.
- **Media protection is not optional in this chapter.** DTLS for the handshake and SRTP for the media are the expected shape, and a description naming a call feature without naming either leaves the requirement applicable and unsettled.
- **A TURN server relays traffic.** Where direct connection fails, media flows through infrastructure the operator runs, which brings its own authentication and resource requirements — an open relay is usable by anyone who finds it.
- **A data channel is an application input.** Anything arriving over one is untrusted input, so V2's requirements apply to it exactly as they do to a form post.
- **Peer addresses leak.** A direct connection reveals network addresses to the other party, which is a privacy consideration the chapter names.
- **Most systems answer no.** If nothing in the model carries real-time media, the honest ruling is that this chapter does not apply, recorded as such rather than raised conditionally.

## Guardrails

- Analysis knowledge, not evidence. Cite the element or prose that named the media feature.
- Rule applicability, never a pass. A named library does not confirm how the signalling authenticates.
- The web surface that hosts the call is V3; the identity of the caller is V6. The connection itself is here.
