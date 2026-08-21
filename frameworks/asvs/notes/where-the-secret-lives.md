# Where the Secret Lives

## When this applies

The model names a secret, a credential, an API key, a vault, an environment variable, a service account or an application configuration store. Chapter V13 covers how the running system is configured and how its secrets are held.

## What to look for

- **A secret in configuration is still a secret.** An environment variable, a config file, a deployment manifest and a CI variable are all storage, and each carries the requirement that it not be readable by more parties than need it.
- **Rotation is a separate requirement from storage.** A named vault answers where; it does not answer how often, or what happens when somebody leaves.
- **Shared accounts collapse attribution.** One credential used by several components, or by several people, is a configuration fact with consequences in two other chapters — it is also why the logging chapter cannot attribute an action.
- **Unnecessary surface.** Default accounts, sample applications, debug endpoints, directory listings and verbose banners are configuration requirements, and descriptions rarely mention them either way.
- **Separation between environments.** Whether production secrets differ from those in test and development is a requirement, and a description of a shared pipeline is a fair place to ask.
- **The build is configuration too.** How a component is configured at deploy time is in scope here even when the component itself is third-party.

## Guardrails

- Analysis knowledge, not evidence. Cite the store, the flow's `authentication` value, or the prose naming the secret.
- Rule applicability, never a pass. A named secret manager does not confirm what is in it or who can read it.
- The cryptographic strength of a key is V11. Whether the key is *held* correctly is here.
