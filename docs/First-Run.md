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

| Vendor | `base` | `strong` | Credentials read by the code |
| --- | --- | --- | --- |
| Anthropic | `claude-sonnet-4-6` | `claude-opus-5` | `ANALYSIS_ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o` | `gpt-5.6` | `ANALYSIS_OPENAI_API_KEY` |
| Vertex AI | `gemini-2.5-flash` | `gemini-2.5-pro` | `ANALYSIS_VERTEX_PROJECT`, `ANALYSIS_VERTEX_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` |

“Reference pair” means the repository's offline capability check knows these
model names. It does not mean CI has successfully called them or that they are
recommended for your cost and quality needs.

Add one pair to `config/model_tiers.toml`. For example:

```toml
[tiers.base]
vendor = "openai"
model = "gpt-4o"

[tiers.strong]
vendor = "openai"
model = "gpt-5.6"
```

Then export the credentials for the vendor or vendors you selected.

### Anthropic

```sh
export ANALYSIS_ANTHROPIC_API_KEY=sk-ant-...
```

### OpenAI

```sh
export ANALYSIS_OPENAI_API_KEY=sk-...
```

### Vertex AI

```sh
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project your-gcp-project

export ANALYSIS_VERTEX_PROJECT=your-gcp-project
export ANALYSIS_VERTEX_LOCATION=us-central1
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/gcloud/application_default_credentials.json"
```

The Vertex identity needs `roles/aiplatform.user`. Work Agent requires the ADC
file path explicitly; it does not search the usual gcloud location.

The tiers may use different vendors. In that case, set credentials for each
vendor a tier the node map binds selects — the shipped map binds `base` and
`strong`, so `review` needs neither a selection nor a credential until you move
criticism onto it, and the loader asks for both at that edit. You may also select models through the matching
`ANALYSIS_MODEL_BASE_{VENDOR,MODEL}`, `ANALYSIS_MODEL_STRONG_{VENDOR,MODEL}` and
`ANALYSIS_MODEL_REVIEW_{VENDOR,MODEL}` environment variables. See
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
