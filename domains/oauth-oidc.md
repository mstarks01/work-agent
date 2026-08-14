# OAuth 2.x and OpenID Connect

## When this applies

The System Model names OAuth, OIDC, OpenID, an identity provider or broker, single sign-on, or JWT/bearer access tokens in a flow's `authentication` or an element's `technology`.

## What to look for

- **Authentication is not authorization, and OAuth is neither.** An OAuth access token says a resource owner granted a scope; it does not say who is calling, and an ID token says who signed in but not what they may do. A model whose `authentication` reads "OIDC" has stated the protocol and left both questions open — ask which of them the receiving element actually enforces.
- **Token validation at the receiver.** For every flow presenting a JWT, the receiver either verifies signature, issuer, audience and expiry, or it does not. Unverified `aud` is the classic confused-deputy: a token minted for one service replayed against another that trusts the same issuer. `alg: none` and key-confusion attacks live here too.
- **Where the token can be replayed.** A bearer token is a bearer credential — possession is identity. Ask its lifetime, its refresh behaviour and whether anything binds it to the caller (mTLS, DPoP, sender constraint). An hour-long token in a log is an hour of impersonation.
- **The redirect surface.** Authorization-code flows fail at the edges: unregistered or wildcard `redirect_uri`, a missing or unbound `state`, and public clients without PKCE all let an attacker complete someone else's authorization. Where the model shows a browser-facing client, these are the flows to ask about.
- **Broker and federation trust.** An identity broker relays assertions between parties. Ask what it validates on the way in and what it re-signs on the way out: a broker that accepts an unverified upstream assertion mints a genuine downstream identity from a forged one, and every relying party inherits that.
- **Scope and consent drift.** Long-lived grants, refresh tokens that outlive employment, and scopes that grew for one integration and stayed. A `secrets`-tagged element issuing tokens for many consumers concentrates all of it.
- **Logout and session lifetime.** Single sign-on rarely means single sign-out; a revoked account with a live token keeps acting.
- **Client credentials as machine identity.** A `client_secret` shared across deployments is a shared machine identity: every holder is the same principal in every audit line.

## Guardrails

- This pack is **analysis knowledge, not evidence.** Naming a protocol failure mode here does not make it a fact about this system; ground the finding in what the submitter said, an `unknown` attribute, or a derived crossing.
- The model rarely states which OAuth grant is in use. Where it does not, that is `unknown` — write the threat conditionally and let the critic mark it needs-info rather than assuming implicit flow or assuming PKCE.
