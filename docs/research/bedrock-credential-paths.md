# Bedrock credential paths, and what litellm accepts

Findings for [#492](https://github.com/mstarks01/work-agent/issues/492), on the map
[#491 — Map: Amazon Bedrock is the fourth vendor row](https://github.com/mstarks01/work-agent/issues/491).
Probed on 2026-09-01 against the pinned `litellm==1.97.0` in this repo's `.venv`, and against
AWS's user guide. No credential and no AWS account took part; nothing here called Bedrock.

## The headline: boto3 is not installed, and every path needs it

`boto3` is absent from this repo's environment. It is not a dependency of `litellm==1.97.0`
either — the distribution declares `boto3>=1.43.1,<2.0` only under the `proxy` extra, which
this project does not install.

Every Bedrock request path in `litellm/llms/bedrock/base_aws_llm.py` imports `botocore` and
raises on failure:

```
raise ImportError("Missing boto3 to call bedrock. Run 'pip install boto3'.")
```

That holds for the bearer-token path as well as the SigV4 path. The bearer path needs
`botocore.awsrequest.AWSRequest`; the SigV4 path needs `botocore.auth.SigV4Auth` beside it.
So there is no credential mode that avoids the dependency.

**This is a cost the other three vendors do not carry.** Vertex, Anthropic and OpenAI all reach
their provider through `litellm`'s own HTTP stack with no vendor SDK. Bedrock is the first vendor
that puts a vendor SDK in the runtime image.

## What AWS offers, and what AWS recommends

Five paths reach Bedrock. AWS's own guidance splits them sharply.

| Path | Lifetime | AWS's position |
| --- | --- | --- |
| Short-term API key (bearer) | 12 hours maximum, or the session, whichever is shorter | **Recommended for production** |
| Long-term API key (bearer) | A configured expiry date | **Exploration only**, with an explicit warning |
| IAM role / instance profile | Short-lived, refreshed by the environment | The standard AWS server-side path |
| Web identity token, then AssumeRole | Short-lived | The standard federated path |
| Static access key and secret | Indefinite | An alternative AWS steers away from |

A short-term API key inherits the permissions of the IAM principal that generated it. A long-term
API key creates an IAM user with attached policies, which is why AWS restricts it to exploration.
Both travel in `AWS_BEARER_TOKEN_BEDROCK`, or in an `Authorization: Bearer` header.

A short-term key needs a generator to mint and refresh it: `aws_bedrock_token_generator`, a
separate pip package, whose `provide_token()` returns a cached token or mints a new one. Nothing
in `litellm` mints or refreshes a bearer token — it reads one and signs the header with it.

Revocation differs by type. A long-term key deactivates, resets or deletes through
`UpdateServiceSpecificCredential` and its siblings. A short-term key has no delete: you invalidate
the session that made it, or deny `bedrock:CallWithBearerToken`. The condition key
`bedrock:bearerTokenType` takes `SHORT_TERM` or `LONG_TERM`, so an account can refuse the
long-term type outright.

**AWS's own examples spell the model `us.anthropic.claude-sonnet-4-6`.** That corroborates the
map's established fact 2 from a second source: the cross-region inference profile prefix is real
and is what AWS puts in front of a reader.

## What litellm accepts

`BaseAWSLLM.get_credentials()` takes ten `aws_*` parameters. Each unset parameter falls back to a
fixed ambient environment variable:

| Parameter | Ambient fallback |
| --- | --- |
| `aws_access_key_id` | `AWS_ACCESS_KEY_ID` |
| `aws_secret_access_key` | `AWS_SECRET_ACCESS_KEY` |
| `aws_session_token` | `AWS_SESSION_TOKEN` |
| `aws_region_name` | `AWS_REGION_NAME` |
| `aws_session_name` | `AWS_SESSION_NAME` |
| `aws_profile_name` | `AWS_PROFILE_NAME` |
| `aws_role_name` | `AWS_ROLE_NAME` |
| `aws_web_identity_token` | `AWS_WEB_IDENTITY_TOKEN` |
| `aws_sts_endpoint` | `AWS_STS_ENDPOINT` |
| `aws_external_id` | `AWS_EXTERNAL_ID` |

Note `AWS_REGION_NAME`, not `AWS_REGION` or `AWS_DEFAULT_REGION`. Those two names appear elsewhere
in the same package, so a reader who assumes the conventional AWS spelling gets it wrong here.

The branch order is a chain, and **the terminal branch is ambient discovery**:

1. web identity token, if the token, role name and session name are all present
2. AssumeRole, if a role name is present
3. a named profile, if a profile name is present
4. static key, secret and session token together
5. static key and secret with a region
6. **otherwise `_auth_with_env_vars`** — boto3's own discovery chain

That last branch matters for this repo. There is no parameter that turns ambient discovery off.
Pass nothing and litellm reaches for the environment, the shared config file, and then the
instance metadata service.

**This service is already protected from that, by accident of design rather than by anything
Bedrock-specific.** `Vendor._require` raises `ProviderAuthError` when a declared variable is unset
or empty, and it raises *before* the adapter is built. So a deployment that has not declared its
credential material never reaches litellm at all, and the ambient chain never runs. The
"declared, not discovered" rule holds — but it holds because the registry fails closed first, not
because litellm can be told to refuse. That distinction belongs in a comment wherever the rule is
written down.

### The bearer token rides on `api_key`

In `_sign_request` and its async twin, litellm reads the bearer token from the `api_key`
parameter, and falls back to `AWS_BEARER_TOKEN_BEDROCK` when `api_key` is `None`. When a bearer
token is present it sets `Authorization: Bearer <token>` and skips SigV4 entirely.

That is the significant finding for the credential-mode decision: **a Bedrock API key fits the
registry's existing `CredentialMode.API_KEY` shape unchanged.** `credential_kwargs` already emits
`{"api_key": ...}`, and ADK's `LiteLlm.__init__` passes every unrecognised kwarg through to the
completion call, exactly as it does for Anthropic and OpenAI today.

### The region

A region is required. It is ordinary configuration rather than a credential, which matches the
Vertex precedent of `ANALYSIS_VERTEX_PROJECT` and `ANALYSIS_VERTEX_LOCATION`.

One exception is worth knowing: `_get_aws_region_from_model_arn` parses a region out of a model
identifier when that identifier is an ARN of the form `arn:aws:bedrock:<region>:...`. So an ARN
model identifier carries its own region, and a bare or profile-prefixed name does not. That is
input to [#494](https://github.com/mstarks01/work-agent/issues/494) and
[#496](https://github.com/mstarks01/work-agent/issues/496), not to the credential decision.

### Refresh

litellm caches credentials for the web-identity, AssumeRole, static-key and ambient paths, keyed
on the argument set, with a TTL. It does not cache the profile or explicit-session-token paths.
It never refreshes a bearer token: it reads the value it is given, per request. A 12-hour
short-term key therefore expires under a long-running process unless something outside this
service replaces it.

## One note on the source

AWS's API-keys page ends with a section suggesting that an AI coding assistant run
`aws agent-toolkit search-skills` against an AWS catalog. That is instruction-shaped text inside
fetched documentation, not a request from this project. It was not acted on, and it is recorded
here only so the next reader of the page knows it was seen and refused.

## Sources

- [Amazon Bedrock user guide — API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html)
- `litellm/llms/bedrock/base_aws_llm.py` at `litellm==1.97.0`, this repo's pinned copy
- `litellm-1.97.0.dist-info/METADATA`, for the `proxy` extra
- `google/adk/models/lite_llm.py` at `google-adk==2.5.0`, for kwarg passthrough
