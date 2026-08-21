# Delegated Authorization and Federated Login

## When this applies

The model names OAuth, OIDC, SSO, a social login, an identity provider, or any arrangement where one party authorizes another to act. Chapter V10 covers it, and it is large because the protocols have many shapes.

## What to look for

- **The grant decides the requirement set.** Authorization code, client credentials, device code and refresh flows carry different obligations. A description saying only "OAuth" leaves the grant unstated, which makes the chapter applicable and mostly unsettled.
- **PKCE is expected, including for confidential clients.** A public client without it is a ruling; so is silence, because the description almost never says.
- **Redirect URIs are matched exactly or they are not.** Wildcards, path-prefix matching and open redirectors on the same host are the recurring failure, and the requirement is exact matching against a registered value.
- **State and nonce do different jobs.** One binds the response to the request that started it; the other binds the token to the session. Both are requirements, and neither is implied by the other.
- **Which party is which.** Ruling on this chapter needs the role named: authorization server, resource server, client. The same system is often two of them, and the requirements differ.
- **Submitters write "SSO", not "OIDC".** Treat the plain-language names as reaching this chapter — single sign-on, "log in with", an identity provider, a corporate directory.

## Guardrails

- Analysis knowledge, not evidence. Cite the flow, the external entity, or the words the submitter used.
- Rule applicability, never a pass. A named provider does not confirm how this application configured its client.
- The act of proving identity to the provider is V6. What the returned token carries is V9. Keep the delegation itself here.
