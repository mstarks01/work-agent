# Authentication (V6)

## Scope

Chapter V6 of ASVS 5.0: how the application establishes who a caller is. Your lane covers password rules and storage, multi-factor and out-of-band authentication, credential recovery, lifecycle events like registration and change, general authentication security including anti-automation on the login path, and the documentation the standard requires of an authentication design.

Chapter boundaries: what happens once identity is established is chapter V7. What a caller is permitted to do is chapter V8. Federated identity through OAuth or OIDC is chapter V10. Your subject is the act of proving identity.

## Applicability

**This chapter needs the application to authenticate somebody.** A pipeline with no interactive caller and no credential does not answer it. Inside the chapter the tests are finer, and the largest group needs a *password* specifically: ten of its level 1 requirements do not apply to a system that authenticates only with certificates or federated tokens.

Read the model's `DataFlow.authentication` values, its `ExternalEntity` kinds, and any `assets` tag naming credentials. An `authentication` reading `unknown` means the question is open, never that the control is absent — that distinction decides between a ruling you write conditionally and one you do not write at all.

### The requirements of this chapter

47 requirements across 8 sections: 13 at level 1, 22 at level 2, 12 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V6.1 Authentication Documentation

- **V6.1.1** (L1) — Verify that application documentation defines how controls such as rate limiting, anti-automation, and adaptive response, are used to defend against attacks such as credential stuffing and password brute force. The documentation must make clear how these controls are configured and prevent malicious account lockout.
- **V6.1.2** (L2) — Verify that a list of context-specific words is documented in order to prevent their use in passwords. The list could include permutations of organization names, product names, system identifiers, project codenames, department or role names, and similar.
- **V6.1.3** (L2) — Verify that, if the application includes multiple authentication pathways, these are all documented together with the security controls and authentication strength which must be consistently enforced across them.

#### V6.2 Password Security

- **V6.2.1** (L1) — Verify that user set passwords are at least 8 characters in length although a minimum of 15 characters is strongly recommended.
- **V6.2.2** (L1) — Verify that users can change their password.
- **V6.2.3** (L1) — Verify that password change functionality requires the user's current and new password.
- **V6.2.4** (L1) — Verify that passwords submitted during account registration or password change are checked against an available set of, at least, the top 3000 passwords which match the application's password policy, e.g. minimum length.
- **V6.2.5** (L1) — Verify that passwords of any composition can be used, without rules limiting the type of characters permitted. There must be no requirement for a minimum number of upper or lower case characters, numbers, or special characters.
- **V6.2.6** (L1) — Verify that password input fields use type=password to mask the entry. Applications may allow the user to temporarily view the entire masked password, or the last typed character of the password.
- **V6.2.7** (L1) — Verify that "paste" functionality, browser password helpers, and external password managers are permitted.
- **V6.2.8** (L1) — Verify that the application verifies the user's password exactly as received from the user, without any modifications such as truncation or case transformation.
- **V6.2.9** (L2) — Verify that passwords of at least 64 characters are permitted.
- **V6.2.10** (L2) — Verify that a user's password stays valid until it is discovered to be compromised or the user rotates it. The application must not require periodic credential rotation.
- **V6.2.11** (L2) — Verify that the documented list of context specific words is used to prevent easy to guess passwords being created.
- **V6.2.12** (L2) — Verify that passwords submitted during account registration or password changes are checked against a set of breached passwords.

#### V6.3 General Authentication Security

- **V6.3.1** (L1) — Verify that controls to prevent attacks such as credential stuffing and password brute force are implemented according to the application's security documentation.
- **V6.3.2** (L1) — Verify that default user accounts (e.g., "root", "admin", or "sa") are not present in the application or are disabled.
- **V6.3.3** (L2) — Verify that either a multi-factor authentication mechanism or a combination of single-factor authentication mechanisms, must be used in order to access the application. For L3, one of the factors must be a hardware-based authentication mechanism which provides compromise and impersonation resistance against phishing attacks while verifying the intent to authenticate by requiring a user-initiated action (such as a button press on a FIDO hardware key or a mobile phone). Relaxing any of the considerations in this requirement requires a fully documented rationale and a comprehensive set of mitigating controls.
- **V6.3.4** (L2) — Verify that, if the application includes multiple authentication pathways, there are no undocumented pathways and that security controls and authentication strength are enforced consistently.
- **V6.3.5** (L3) — Verify that users are notified of suspicious authentication attempts (successful or unsuccessful). This may include authentication attempts from an unusual location or client, partially successful authentication (only one of multiple factors), an authentication attempt after a long period of inactivity or a successful authentication after several unsuccessful attempts.
- **V6.3.6** (L3) — Verify that email is not used as either a single-factor or multi-factor authentication mechanism.
- **V6.3.7** (L3) — Verify that users are notified after updates to authentication details, such as credential resets or modification of the username or email address.
- **V6.3.8** (L3) — Verify that valid users cannot be deduced from failed authentication challenges, such as by basing on error messages, HTTP response codes, or different response times. Registration and forgot password functionality must also have this protection.

#### V6.4 Authentication Factor Lifecycle and Recovery

- **V6.4.1** (L1) — Verify that system generated initial passwords or activation codes are securely randomly generated, follow the existing password policy, and expire after a short period of time or after they are initially used. These initial secrets must not be permitted to become the long term password.
- **V6.4.2** (L1) — Verify that password hints or knowledge-based authentication (so-called "secret questions") are not present.
- **V6.4.3** (L2) — Verify that a secure process for resetting a forgotten password is implemented, that does not bypass any enabled multi-factor authentication mechanisms.
- **V6.4.4** (L2) — Verify that if a multi-factor authentication factor is lost, evidence of identity proofing is performed at the same level as during enrollment.
- **V6.4.5** (L3) — Verify that renewal instructions for authentication mechanisms which expire are sent with enough time to be carried out before the old authentication mechanism expires, configuring automated reminders if necessary.
- **V6.4.6** (L3) — Verify that administrative users can initiate the password reset process for the user, but that this does not allow them to change or choose the user's password. This prevents a situation where they know the user's password.

#### V6.5 General Multi-factor authentication requirements

- **V6.5.1** (L2) — Verify that lookup secrets, out-of-band authentication requests or codes, and time-based one-time passwords (TOTPs) are only successfully usable once.
- **V6.5.2** (L2) — Verify that, when being stored in the application's backend, lookup secrets with less than 112 bits of entropy (19 random alphanumeric characters or 34 random digits) are hashed with an approved password storage hashing algorithm that incorporates a 32-bit random salt. A standard hash function can be used if the secret has 112 bits of entropy or more.
- **V6.5.3** (L2) — Verify that lookup secrets, out-of-band authentication code, and time-based one-time password seeds, are generated using a Cryptographically Secure Pseudorandom Number Generator (CSPRNG) to avoid predictable values.
- **V6.5.4** (L2) — Verify that lookup secrets and out-of-band authentication codes have a minimum of 20 bits of entropy (typically 4 random alphanumeric characters or 6 random digits is sufficient).
- **V6.5.5** (L2) — Verify that out-of-band authentication requests, codes, or tokens, as well as time-based one-time passwords (TOTPs) have a defined lifetime. Out of band requests must have a maximum lifetime of 10 minutes and for TOTP a maximum lifetime of 30 seconds.
- **V6.5.6** (L3) — Verify that any authentication factor (including physical devices) can be revoked in case of theft or other loss.
- **V6.5.7** (L3) — Verify that biometric authentication mechanisms are only used as secondary factors together with either something you have or something you know.
- **V6.5.8** (L3) — Verify that time-based one-time passwords (TOTPs) are checked based on a time source from a trusted service and not from an untrusted or client provided time.

#### V6.6 Out-of-Band authentication mechanisms

- **V6.6.1** (L2) — Verify that authentication mechanisms using the Public Switched Telephone Network (PSTN) to deliver One-time Passwords (OTPs) via phone or SMS are offered only when the phone number has previously been validated, alternate stronger methods (such as Time based One-time Passwords) are also offered, and the service provides information on their security risks to users. For L3 applications, phone and SMS must not be available as options.
- **V6.6.2** (L2) — Verify that out-of-band authentication requests, codes, or tokens are bound to the original authentication request for which they were generated and are not usable for a previous or subsequent one.
- **V6.6.3** (L2) — Verify that a code based out-of-band authentication mechanism is protected against brute force attacks by using rate limiting. Consider also using a code with at least 64 bits of entropy.
- **V6.6.4** (L3) — Verify that, where push notifications are used for multi-factor authentication, rate limiting is used to prevent push bombing attacks. Number matching may also mitigate this risk.

#### V6.7 Cryptographic authentication mechanism

- **V6.7.1** (L3) — Verify that the certificates used to verify cryptographic authentication assertions are stored in a way protects them from modification.
- **V6.7.2** (L3) — Verify that the challenge nonce is at least 64 bits in length, and statistically unique or unique over the lifetime of the cryptographic device.

#### V6.8 Authentication with an Identity Provider

- **V6.8.1** (L2) — Verify that, if the application supports multiple identity providers (IdPs), the user's identity cannot be spoofed via another supported identity provider (eg. by using the same user identifier). The standard mitigation would be for the application to register and identify the user using a combination of the IdP ID (serving as a namespace) and the user's ID in the IdP.
- **V6.8.2** (L2) — Verify that the presence and integrity of digital signatures on authentication assertions (for example on JWTs or SAML assertions) are always validated, rejecting any assertions that are unsigned or have invalid signatures.
- **V6.8.3** (L2) — Verify that SAML assertions are uniquely processed and used only once within the validity period to prevent replay attacks.
- **V6.8.4** (L2) — Verify that, if an application uses a separate Identity Provider (IdP) and expects specific authentication strength, methods, or recentness for specific functions, the application verifies this using the information returned by the IdP. For example, if OIDC is used, this might be achieved by validating ID Token claims such as 'acr', 'amr', and 'auth_time' (if present). If the IdP does not provide this information, the application must have a documented fallback approach that assumes that the minimum strength authentication mechanism was used (for example, single-factor authentication using username and password).

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Authentication is named without its mechanism.** `authentication: unknown` on a flow from a human entity puts every requirement in this chapter into needs-info at once. Rule them one at a time and name the same attribute in each.
**A password is named and its rules are not.** Minimum length, breach screening, storage algorithm and rotation policy are separate requirements. The input almost never carries any of them, and each is its own ruling.
**Multi-factor is stated as absent.** `none` is the submitter answering the question. Write that ruling plainly rather than conditionally.
**No password anywhere.** A service authenticating only with mutual TLS or a federated token rules out the password requirements. Name the mechanism the model states, and rule them out on it.
**Recovery and lifecycle go unmentioned.** Password reset, account recovery and credential change are requirements here even where the input describes only the login path.

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
