# Proving Who the Caller Is

## When this applies

The model shows the application establishing identity — a login, an API key, a certificate, a federated assertion — or names a password anywhere. Chapter V6 covers the act of proving identity, and nothing that happens after it.

## What to look for

- **Passwords bring their own section, and only if there is a password.** Length, composition, breach-checking, change and recovery are password requirements. A system authenticating only with certificates, device tokens or a federated assertion does not answer them, and saying so is a ruling.
- **Read the length and composition rules off the roster, not from habit.** 5.0 reversed advice many descriptions still repeat: it forbids composition rules and periodic rotation rather than requiring them. A stated 90-day rotation policy is therefore a ruling against a requirement, not evidence for one. The chapter's own text beside this note carries the numbers; do not carry them in your head.
- **Recovery is an authentication path.** Password reset, account recovery and support-desk overrides authenticate somebody, so they carry the chapter's requirements. A description of a reset email is squarely in scope.
- **Anti-automation sits on the login path.** Rate limiting and lockout behaviour are requirements here, together with the rule that lockout must not become a denial-of-service against the account holder.
- **Factors are counted, not assumed.** A description saying "SSO" says nothing about how many factors the identity provider required. That is a ruling on an open question, not a confirmed multi-factor control.
- **Documentation first.** V6's opening section asks for the authentication design to be documented, including the multiple pathways most systems accumulate.

## Guardrails

- Analysis knowledge, not evidence. Cite the flow's `authentication` value, an entity kind, or the submitter's own words.
- Rule applicability, never a pass. "Passwords are hashed with bcrypt" does not verify the cost factor, the salt or the comparison, and none of them is in the material.
- What the caller may *do* once proved is V8. What carries the proof afterwards is V7.
