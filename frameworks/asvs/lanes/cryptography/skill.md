# Cryptography (V11)

## Scope

Chapter V11 of ASVS 5.0: the cryptography the application performs. Your lane covers algorithm and mode selection, key generation, storage and rotation, random number generation, and the requirements on encrypting data the application holds.

Chapter boundaries: cryptography on the wire is chapter V12. What a token's signature proves is chapter V9. Which data deserves protection is chapter V14. Your subject is the primitive and the key.

## Applicability

**This chapter needs the application to encrypt or sign something.** A system that terminates TLS at a load balancer and encrypts nothing itself answers little of it.

Read the model's `encryption_at_rest` and `encryption_in_transit` attributes and any technology naming a key manager or an HSM. `unknown` on either is the open question that most rulings here rest on; `none` is the submitter answering it. Keep the two apart — they lead to different rulings.

### The requirements of this chapter

24 requirements across 7 sections: 3 at level 1, 11 at level 2, 10 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V11.1 Cryptographic Inventory and Documentation

- **V11.1.1** (L2) — Verify that there is a documented policy for management of cryptographic keys and a cryptographic key lifecycle that follows a key management standard such as NIST SP 800-57. This should include ensuring that keys are not overshared (for example, with more than two entities for shared secrets and more than one entity for private keys).
- **V11.1.2** (L2) — Verify that a cryptographic inventory is performed, maintained, regularly updated, and includes all cryptographic keys, algorithms, and certificates used by the application. It must also document where keys can and cannot be used in the system, and the types of data that can and cannot be protected using the keys.
- **V11.1.3** (L3) — Verify that cryptographic discovery mechanisms are employed to identify all instances of cryptography in the system, including encryption, hashing, and signing operations.
- **V11.1.4** (L3) — Verify that a cryptographic inventory is maintained. This must include a documented plan that outlines the migration path to new cryptographic standards, such as post-quantum cryptography, in order to react to future threats.

#### V11.2 Secure Cryptography Implementation

- **V11.2.1** (L2) — Verify that industry-validated implementations (including libraries and hardware-accelerated implementations) are used for cryptographic operations.
- **V11.2.2** (L2) — Verify that the application is designed with crypto agility such that random number, authenticated encryption, MAC, or hashing algorithms, key lengths, rounds, ciphers and modes can be reconfigured, upgraded, or swapped at any time, to protect against cryptographic breaks. Similarly, it must also be possible to replace keys and passwords and re-encrypt data. This will allow for seamless upgrades to post-quantum cryptography (PQC), once high-assurance implementations of approved PQC schemes or standards are widely available.
- **V11.2.3** (L2) — Verify that all cryptographic primitives utilize a minimum of 128-bits of security based on the algorithm, key size, and configuration. For example, a 256-bit ECC key provides roughly 128 bits of security where RSA requires a 3072-bit key to achieve 128 bits of security.
- **V11.2.4** (L3) — Verify that all cryptographic operations are constant-time, with no 'short-circuit' operations in comparisons, calculations, or returns, to avoid leaking information.
- **V11.2.5** (L3) — Verify that all cryptographic modules fail securely, and errors are handled in a way that does not enable vulnerabilities, such as Padding Oracle attacks.

#### V11.3 Encryption Algorithms

- **V11.3.1** (L1) — Verify that insecure block modes (e.g., ECB) and weak padding schemes (e.g., PKCS#1 v1.5) are not used.
- **V11.3.2** (L1) — Verify that only approved ciphers and modes such as AES with GCM are used.
- **V11.3.3** (L2) — Verify that encrypted data is protected against unauthorized modification preferably by using an approved authenticated encryption method or by combining an approved encryption method with an approved MAC algorithm.
- **V11.3.4** (L3) — Verify that nonces, initialization vectors, and other single-use numbers are not used for more than one encryption key and data-element pair. The method of generation must be appropriate for the algorithm being used.
- **V11.3.5** (L3) — Verify that any combination of an encryption algorithm and a MAC algorithm is operating in encrypt-then-MAC mode.

#### V11.4 Hashing and Hash-based Functions

- **V11.4.1** (L1) — Verify that only approved hash functions are used for general cryptographic use cases, including digital signatures, HMAC, KDF, and random bit generation. Disallowed hash functions, such as MD5, must not be used for any cryptographic purpose.
- **V11.4.2** (L2) — Verify that passwords are stored using an approved, computationally intensive, key derivation function (also known as a "password hashing function"), with parameter settings configured based on current guidance. The settings should balance security and performance to make brute-force attacks sufficiently challenging for the required level of security.
- **V11.4.3** (L2) — Verify that hash functions used in digital signatures, as part of data authentication or data integrity are collision resistant and have appropriate bit-lengths. If collision resistance is required, the output length must be at least 256 bits. If only resistance to second pre-image attacks is required, the output length must be at least 128 bits.
- **V11.4.4** (L2) — Verify that the application uses approved key derivation functions with key stretching parameters when deriving secret keys from passwords. The parameters in use must balance security and performance to prevent brute-force attacks from compromising the resulting cryptographic key.

#### V11.5 Random Values

- **V11.5.1** (L2) — Verify that all random numbers and strings which are intended to be non-guessable must be generated using a cryptographically secure pseudo-random number generator (CSPRNG) and have at least 128 bits of entropy. Note that UUIDs do not respect this condition.
- **V11.5.2** (L3) — Verify that the random number generation mechanism in use is designed to work securely, even under heavy demand.

#### V11.6 Public Key Cryptography

- **V11.6.1** (L2) — Verify that only approved cryptographic algorithms and modes of operation are used for key generation and seeding, and digital signature generation and verification. Key generation algorithms must not generate insecure keys vulnerable to known attacks, for example, RSA keys which are vulnerable to Fermat factorization.
- **V11.6.2** (L3) — Verify that approved cryptographic algorithms are used for key exchange (such as Diffie-Hellman) with a focus on ensuring that key exchange mechanisms use secure parameters. This will prevent attacks on the key establishment process which could lead to adversary-in-the-middle attacks or cryptographic breaks.

#### V11.7 In-Use Data Cryptography

- **V11.7.1** (L3) — Verify that full memory encryption is in use that protects sensitive data while it is in use, preventing access by unauthorized users or processes.
- **V11.7.2** (L3) — Verify that data minimization ensures the minimal amount of data is exposed during processing, and ensure that data is encrypted immediately after use or as soon as feasible.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Encryption is named without its algorithm.** "Encrypted at rest" says a control exists and nothing about the cipher, the mode or the key length. Each is a separate requirement.
**Key management is unmentioned.** Where a key exists, its generation, storage and rotation are requirements, and prose describing encryption almost never reaches them.
**Randomness is assumed.** Requirements about secure random generation apply wherever a token, a key or a nonce is produced, and are open by default.
**A managed service is offered as the answer.** A named cloud key service settles the storage question and not the algorithm one. Say which half it answered.

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
