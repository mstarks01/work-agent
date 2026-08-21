# Algorithms, Modes and Who Holds the Key

## When this applies

The model states that something is encrypted, hashed or signed — at rest, in transit, or in a field — or names a key, a secret or a vault. Chapter V11 covers the cryptography itself; chapter V12 covers the channel.

## What to look for

- **"Encrypted" is not an answer.** The requirement asks for the algorithm, the mode and the key length. AES alone leaves the mode open, and mode is where ECB fails.
- **Authenticated encryption is the requirement.** A mode that provides confidentiality without integrity is a ruling, because ciphertext that can be altered undetected is the failure the requirement names.
- **Hashing splits by purpose.** A password needs a memory-hard function with a work factor; an integrity check needs a collision-resistant hash; a token needs a keyed MAC. Reading one requirement against the wrong purpose produces a wrong ruling.
- **Randomness is its own requirement.** Tokens, salts, nonces and identifiers must come from a cryptographically secure generator, and descriptions almost never say which.
- **Key custody is most of the chapter.** Generation, storage, rotation, separation by purpose and destruction are separate requirements. A named vault answers storage and leaves the rest open.
- **Deprecated primitives.** MD5, SHA-1, DES, RC4 and RSA below the current floor are rulings against the requirement wherever the input names them.

## Guardrails

- Analysis knowledge, not evidence. Cite `encryption_at_rest`, `encryption_in_transit`, or the submitter's own words about the algorithm.
- Rule applicability, never a pass. A named strong algorithm does not confirm the key length, the mode, or where the key lives.
- Transport protection is V12 and stored-data protection is V14. What the primitive *is* belongs here.
