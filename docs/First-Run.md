# First run

This guide gets a real analysis running locally. Work Agent has no offline demo
mode: the local app calls the models you configure.

## 1. Install

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/mstarks01/work-agent.git
cd work-agent
uv sync
```

## 2. Select models and provide credentials

Work Agent uses two model tiers:

| Tier | Used for |
| --- | --- |
| `base` | Extracting the system model and repairing it once if validation fails. |
| `strong` | Framework analyzers, framework review, and one review retry if needed. |

The shipped [`config/model_tiers.toml`](../config/model_tiers.toml) deliberately
selects neither tier. Choose a vendor and model for both. The following pairs
are the reference pairs declared in `analysis_service.conformance.REFERENCE_MODELS`:

<!-- every-vendor -->

| Vendor | `base` | `strong` | Credentials read by the code |
| --- | --- | --- | --- |
| Anthropic | `claude-sonnet-4-6` | `claude-opus-5` | `ANALYSIS_ANTHROPIC_API_KEY` |
| Bedrock | `global.anthropic.claude-sonnet-4-6` | `global.anthropic.claude-opus-5` | `ANALYSIS_BEDROCK_API_KEY`, `ANALYSIS_BEDROCK_REGION` |
| Gemini | `gemini-2.5-flash` | `gemini-2.5-pro` | `ANALYSIS_GEMINI_API_KEY` |
| OpenAI | `gpt-4o-2024-08-06` | `gpt-5.6-sol` | `ANALYSIS_OPENAI_API_KEY` |
| OpenRouter | `anthropic/claude-sonnet-4.6` | `anthropic/claude-opus-4.7` | `ANALYSIS_OPENROUTER_API_KEY` |
| Vertex AI | `gemini-2.5-flash` | `gemini-2.5-pro` | `ANALYSIS_VERTEX_PROJECT`, `ANALYSIS_VERTEX_LOCATION` |

<!-- /every-vendor -->

“Reference pair” means the repository's offline capability check knows these
model names. It does not mean CI has successfully called them or that they are
recommended for your cost and quality needs.

Add one pair to `config/model_tiers.toml`. For example:

```toml
[tiers.base]
vendor = "openai"
model = "gpt-4o-2024-08-06"

[tiers.strong]
vendor = "openai"
model = "gpt-5.6"
```

Then export the credentials for the vendor or vendors you selected.

### Anthropic

```sh
export ANALYSIS_ANTHROPIC_API_KEY=sk-ant-...
```

### Bedrock

Bedrock needs its client library, which is an optional extra:

```sh
uv sync --extra bedrock
```

Then choose a credential mode. Bedrock is the one vendor that offers two, so
`config/model_tiers.toml` has to say which one you use:

```toml
[credentials]
bedrock = "api_key"    # or "iam"
```

Under `api_key`, export the key and the region:

```sh
export ANALYSIS_BEDROCK_API_KEY=...
export ANALYSIS_BEDROCK_REGION=us-east-1
```

An AWS short-term Bedrock key lasts twelve hours and nothing here refreshes it,
so rotate the variable before it expires.

Under `iam`, export the region alone:

```sh
export ANALYSIS_BEDROCK_REGION=us-east-1
```

Work Agent then passes no credential, and boto3's own chain resolves the
identity — an attached role, `AWS_PROFILE`, or SSO.

Both reference models carry a `global.` prefix. That names a cross-Region
inference profile, which is how AWS serves recent Claude generations: the plain
`anthropic.claude-opus-5` has no on-demand endpoint at all, and
`anthropic.claude-sonnet-4-6` has one in eu-west-2 alone. A `global.` profile
routes to commercial Regions only. Pick `us.`, `eu.`, `au.` or `jp.` instead
when data residency binds you to one geography, and in GovCloud pick a profile
the model publishes there.

Under `iam`, the identity needs `bedrock:InvokeModel` on the inference profile
**and** on the underlying foundation model in every Region the profile routes
to. AWS states the second half outright: "When you specify an inference profile
in the `Resource` field in the first statement, you must also specify the
foundation model in each Region associated with it."

### Gemini

```sh
export ANALYSIS_GEMINI_API_KEY=AIza...
```

This is the Gemini Developer API, with a key from Google AI Studio. It serves
the same models as Vertex AI. Select `gemini` when you hold a key and no Google
Cloud project. Select `vertex` when the platform supplies the identity.

### OpenAI

```sh
export ANALYSIS_OPENAI_API_KEY=sk-...
```

### OpenRouter

```sh
export ANALYSIS_OPENROUTER_API_KEY=sk-or-...
```

OpenRouter is an aggregator: one endpoint in front of many providers' models.
Its model identifier carries the provider, so a model name has a slash in it —
`anthropic/claude-opus-4.7`. Write the whole slug as the model, and the code
builds the route `openrouter/anthropic/claude-opus-4.7`.

OpenRouter may serve one slug from more than one upstream provider. Two runs of
one configuration can therefore reach different backends. Measured on
2026-09-11: `anthropic/claude-opus-4.7` is fronted by 8 endpoints across 5
providers, and one Llama slug by 12 endpoints whose numeric formats differ.
Nothing stops you running an analysis that way. A **Baseline** may not be named
after an OpenRouter route, because a Baseline's value is that its runs are
comparable.

OpenRouter states what it charged for each call, and what that figure covers
depends on how your account reaches the upstream provider. So declare the
arrangement in `config/model_tiers.toml`:

```toml
[charges]
openrouter = "direct"    # or "own_upstream_key"
```

Use `direct` when you pay OpenRouter for the call — a key issued by OpenRouter,
spending OpenRouter credit. Each node's record then carries the charge beside
its token counts. Use `own_upstream_key` when OpenRouter routes the call under a
provider key of your own: it then charges a routing fee and the provider charges
you for the tokens, so the figure it reports is a fraction of the cost and
nothing records it. `ANALYSIS_MODEL_CHARGES_OPENROUTER` sets the same value from
the environment.

If you declare the wrong one, the first call says so and the run stops: every
OpenRouter response states which arrangement served it, and a figure recorded
under the wrong one is either a twentieth of a cost or a cost thrown away.

An OpenRouter response repeats the slug you asked for rather than naming the
build that answered, so your report's served model tells you nothing the
requested model did not. It does name the **organisation** that answered —
`DeepInfra`, `Claude Platform on AWS` — and each node run records that under
`served_upstream`. It is evidence and not identity: no fingerprint binds it,
because the vocabulary is OpenRouter's and a rename there would move every
blessed hash for nothing. A sweep's tier summary reports every upstream that
answered, which is how you see a spread that the served model hides.

Every other vendor here either names the build or carries no build to name, and
none names an upstream: a direct route's upstream is the vendor itself. See
[Configuration](Configuration.md#models-and-vendors) for what that costs a
blessed fingerprint.

### Vertex AI

```sh
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project your-gcp-project

export ANALYSIS_VERTEX_PROJECT=your-gcp-project
export ANALYSIS_VERTEX_LOCATION=us-central1
```

The Vertex identity needs `roles/aiplatform.user`. Work Agent passes no
credential to Vertex: it passes the project and the location, and Google's own
Application Default Credentials chain resolves the identity. The `gcloud`
command above writes the file that chain finds on a workstation. On GKE or Cloud
Run, bind a service account instead and set no file at all.

The tiers may use different vendors. In that case, set credentials for each
vendor a tier the node map binds selects — the shipped map binds `base` and
`strong`, so `review` needs neither a selection nor a credential until you move
criticism onto it, and the loader asks for both at that edit. You may also select models through the matching
`ANALYSIS_MODEL_BASE_{VENDOR,MODEL}`, `ANALYSIS_MODEL_STRONG_{VENDOR,MODEL}` and
`ANALYSIS_MODEL_REVIEW_{VENDOR,MODEL}` environment variables, and a credential
mode through `ANALYSIS_MODEL_CREDENTIALS_{VENDOR}` and a charge arrangement
through `ANALYSIS_MODEL_CHARGES_{VENDOR}`. See
[Configuration](Configuration.md#model-overrides-deploy-time-no-image-rebuild)
for the exact override rules.

### Check the selection

This command uses the pinned LiteLLM model map locally. It makes no network
request and needs no credentials:

```sh
uv run python -m analysis_service.conformance
```

It reports whether the known provider mapping accepts the exposed sampling
settings, supports native schema-constrained output, and has enough output
capacity. `unknown` means the pinned map has no answer for that model; it does
not mean supported or unsupported.

After setting credentials, run the end-to-end smoke check:

```sh
uv run python -m analysis_service.smoke
```

This is the first command in the guide that proves the selected providers
actually answer. It runs the included small system through the real graph and
therefore incurs model charges.

## 3. Start the local app

```sh
uv run python webapp/main.py
```

Open <http://127.0.0.1:8000>.

The app is a local demonstration, not a production service. The implementation
hard-codes `127.0.0.1:8000`, performs no user authentication, allows one active
run, and keeps at most 20 recent runs in process memory. A restart loses them.

If configuration or credentials are missing, the app shows a diagnostic page
instead of the analysis form.

## 4. Run the included example

1. Click **Load example**. This loads
   [`examples/orders.md`](../examples/orders.md).
2. Select at least one framework.
3. For ASVS, select level 1, 2, or 3.
4. Click **Analyze**.

The selected frameworks affect both the answer and the cost. STRIDE runs six
lane analyzers. ASVS runs 17. Each framework also runs its own reviewer, and a
malformed review may cause one additional review call. Extraction is shared
when both frameworks are selected.

Start with these parts of the result:

- **System model:** verify that the actors, components, stores, flows, and trust
  zones match the source. Every later result depends on this extraction.
- **Grounds:** check whether each finding rests on relevant source text or a
  real fact derived from the model.
- **Verdict:** `confirmed`, `needs-info`, or `rejected`. For ASVS, `confirmed`
  means a requirement applies and the input does not show it satisfied; it is
  not a failed compliance test.
- **Marks:** look for repaired quotes, dropped claims, unresolved evidence, or
  unresolved references. These show where one proposal was degraded or removed
  without discarding the entire analysis.
- **Provenance:** see which model route was requested, which model identifier
  the provider returned, and which sampling fingerprint each call produced.
  These values support auditing; they do not establish that a finding is right.

Then replace the example with your own description. State security controls
that are actually known. When the input does not state an attribute, extraction
uses `unknown`; code turns a claim resting on such an attribute into
`needs-info` rather than treating the control as missing.

## Next steps

- Use the [Integration guide](Integration-Guide.md) to embed the in-process
  `Engine`, write effective sources, and handle completed, rejected, and failed
  outcomes.
- Use the [HTTP API](HTTP-API.md) when you need bearer authentication,
  asynchronous jobs, and a front end separated from the engine process.
- Use the [Report schema](Report-Schema.md) when building a report consumer.
- Use [Configuration](Configuration.md) for model constraints, sampling,
  resilience, certification policy, and input limits.
