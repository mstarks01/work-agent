# Self-contained Tokens (V9)

## Scope

Chapter V9 of ASVS 5.0: tokens that carry their own claims. Your lane covers the verification of a self-contained token — the algorithms accepted, the signature check, the audience and issuer claims, expiry — and the parts of a token's content a verifier must not trust.

Chapter boundaries: how a token was obtained is chapter V6 or chapter V10. How a session is held is chapter V7. What a permission claim means is chapter V8. Your subject is the token's own verification.

## Applicability

**This chapter needs a self-contained token.** A system using opaque reference tokens or session cookies alone does not answer it, and that is a clean exclusion.

Read the model's `authentication` values and technology fields for a JWT, a JWS, a JWE, a bearer token or an OIDC ID token. Where none appears, rule the chapter out and name the credential the model does state.

### The requirements of this chapter

7 requirements across 2 sections: 4 at level 1, 3 at level 2, 0 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V9.1 Token source and integrity

- **V9.1.1** (L1) — Verify that self-contained tokens are validated using their digital signature or MAC to protect against tampering before accepting the token's contents.
- **V9.1.2** (L1) — Verify that only algorithms on an allowlist can be used to create and verify self-contained tokens, for a given context. The allowlist must include the permitted algorithms, ideally only either symmetric or asymmetric algorithms, and must not include the 'None' algorithm. If both symmetric and asymmetric must be supported, additional controls will be needed to prevent key confusion.
- **V9.1.3** (L1) — Verify that key material that is used to validate self-contained tokens is from trusted pre-configured sources for the token issuer, preventing attackers from specifying untrusted sources and keys. For JWTs and other JWS structures, headers such as 'jku', 'x5u', and 'jwk' must be validated against an allowlist of trusted sources.

#### V9.2 Token content

- **V9.2.1** (L1) — Verify that, if a validity time span is present in the token data, the token and its content are accepted only if the verification time is within this validity time span. For example, for JWTs, the claims 'nbf' and 'exp' must be verified.
- **V9.2.2** (L2) — Verify that the service receiving a token validates the token to be the correct type and is meant for the intended purpose before accepting the token's contents. For example, only access tokens can be accepted for authorization decisions and only ID Tokens can be used for proving user authentication.
- **V9.2.3** (L2) — Verify that the service only accepts tokens which are intended for use with that service (audience). For JWTs, this can be achieved by validating the 'aud' claim against an allowlist defined in the service.
- **V9.2.4** (L2) — Verify that, if a token issuer uses the same private key for issuing tokens to different audiences, the issued tokens contain an audience restriction that uniquely identifies the intended audiences. This will prevent a token from being reused with an unintended audience. If the audience identifier is dynamically provisioned, the token issuer must validate these audiences in order to make sure that they do not result in audience impersonation.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**A token is named and its verification is not.** Which algorithms the verifier accepts, and whether it rejects `none`, is the sharpest requirement here and one prose never carries.
**Audience and issuer go unmentioned.** A token accepted without an audience check is a token any holder can replay across services. The requirement applies wherever a token appears.
**Expiry is assumed from the library.** The input says a token is used; it does not say what its lifetime is or whether expiry is enforced.
**Opaque tokens only.** Where the input names a session cookie or a reference token, rule this chapter out on that fact.

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
