# What Carries Identity After the Login

## When this applies

The model holds a session, or carries a token the application validates for itself — a JWT, a signed cookie, a SAML assertion, an API token. Chapters V7 and V9 split on where the truth lives: in the server's store, or inside the token.

## What to look for

- **A new token at every privilege change.** Login, re-authentication, step-up and role change each require a fresh session identifier. Fixation is the failure this prevents, and a description of a login rarely says.
- **Termination is plural.** Logout, idle timeout, absolute timeout and administrative revocation are separate requirements. A stated logout button answers one of four.
- **Self-contained means revocation is hard.** A token the server validates without a lookup cannot be withdrawn before it expires unless something extra exists. That tension is the reason V9 is its own chapter, and it is worth a ruling whenever a token appears.
- **Algorithm and key are requirements.** Which algorithms are accepted, whether `none` is refused, and whether the key is bound to an issuer are V9 requirements. A description naming JWT and nothing else leaves all of them open.
- **Audience, issuer and expiry get validated or they do not.** A token accepted by more than one service raises the question of whether each checks it was meant for them.
- **Binding.** Whether the token is tied to a client, a device or a sender is its own requirement, and it is what separates a stolen token from a useless one.

## Guardrails

- Analysis knowledge, not evidence. Ground the ruling in the element or flow that carries the token.
- Rule applicability, never a pass. A named library does not confirm which algorithms it was configured to accept.
- Where the token is *stored in a browser* is V3 and V14; what it *proves* is here.
