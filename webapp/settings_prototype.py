"""PROTOTYPE for ticket #1762 on map #1760: three layouts of the Settings page.

Throwaway. This module lives only on the branch ``prototype/settings-page-1762``
and never merges. It answers one question: what does the Settings page look
like, and how does it behave?

Three variants of the Settings page, switchable via ``?variant=A|B|C``, and two
scenarios via ``?state=ready|first-run``, on ``/settings`` in the first-run app.

The data is a stub. A save runs no loader: the client script copies three real
loader messages, so that a refusal reads as it will in the built page. Nothing
is written to disk.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from analysis_service.vendors import CREDENTIAL_MODE_NOTES
from webapp.page import Grants, client_script, render, response, script_json

_GRANTS = Grants(script=True, style=True)

_TIERS = ("base", "strong", "review")
_VENDORS = ["vertex", "anthropic", "openai", "bedrock", "gemini", "openrouter"]
_VENDOR_NAMES = {
    "vertex": "Vertex",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "bedrock": "Bedrock",
    "gemini": "Gemini",
    "openrouter": "OpenRouter",
}

#: Real loader messages, captured from ``Deployment.from_env`` on 2026-10-16.
LOADER_MESSAGES = {
    "no-vendor": (
        "config/model_tiers.toml: no vendor selected for tier(s) {tiers}. This"
        " service ships no default vendor, so each tier must name one before it"
        " can run. Supported vendors: vertex, anthropic, openai, bedrock, gemini,"
        " openrouter. Set a (vendor, model) pair per tier in the file, or set"
        " ANALYSIS_MODEL_<TIER>_VENDOR + ANALYSIS_MODEL_<TIER>_MODEL. See"
        " docs/First-Run.md step 2."
    ),
    "no-model": (
        "ANALYSIS_MODEL_{TIER}_VENDOR is set without ANALYSIS_MODEL_{TIER}_MODEL;"
        " a vendor without its model is not a usable selection"
    ),
    "no-mode": (
        "config/model_tiers.toml: 1 validation error for ModelTierConfig\n"
        "  Value error, vendor '{vendor}' allows more than one credential mode"
        " ({modes}), so credentials.{vendor} must declare which one this"
        " deployment uses [type=value_error, input_value={'version': 9, ...},"
        " input_type=dict]"
    ),
    "no-credential": (
        "vendor '{vendor}' needs {var}; it is unset or empty (credential mode:"
        " {mode})"
    ),
}


def _model_suggestions() -> dict[str, list[str]]:
    """Each vendor's chat models that call tools, from the pinned litellm map.

    A list of suggestions, not a closed set: the map lags each new model, and
    the model gate accepts a model the map does not name.
    """
    import analysis_service.model_gate  # noqa: F401  pins the local map first
    import litellm

    from analysis_service.vendors import vendor_for

    prefixes = {"vertex": "vertex_ai", "bedrock": "bedrock"}
    models = {}
    for vendor in _VENDORS:
        prefix = prefixes.get(vendor, vendor)
        names = set()
        for name, info in litellm.model_cost.items():
            usable = (
                info.get("mode") == "chat"
                and (info.get("litellm_provider") or "").startswith(prefix)
                and info.get("supports_function_calling")
                and "*" not in name
                and not name.startswith("ft:")
            )
            if not usable:
                continue
            bare = name.removeprefix(f"{prefix}/").removeprefix(f"{vendor}/")
            try:
                vendor_for(vendor).validate_model(bare, "prototype")
            except ValueError:
                continue
            names.add(bare)
        models[vendor] = sorted(names)
    return models


_MODELS = _model_suggestions()


#: An example value for each credential field that is not a key, from
#: docs/First-Run.md. The page module of ADR 0091 holds this fact.
_EXAMPLES = {
    "ANALYSIS_VERTEX_PROJECT": "your-gcp-project",
    "ANALYSIS_VERTEX_LOCATION": "us-central1",
    "ANALYSIS_BEDROCK_REGION": "us-east-1",
}


#: The note and setup command for each vendor in ``iam`` mode. The page module
#: of ADR 0091 holds this fact; docs/First-Run.md gives the commands.
_IAM_NOTES = {
    "vertex": [
        "Uses your Google login, so this page stores no key. Run this once on"
        " this machine:",
        "gcloud auth application-default login",
    ],
    "bedrock": [
        "Uses your AWS identity, so this page stores no key: an attached role,"
        " AWS_PROFILE or SSO. For SSO, run:",
        "aws sso login",
    ],
}


def _credential_settings(ready: bool) -> list[dict[str, object]]:
    """Each vendor's credential fields, read from its ``VENDORS`` row.

    A vendor with more than one mode gets a mode field first. Each variable
    carries the modes that read it, so the page shows it only under those.
    """
    from analysis_service.vendors import vendor_for

    entries = []
    for vendor in _VENDORS:
        row = vendor_for(vendor)
        modes = [mode.value for mode in row.credential_modes]
        if len(modes) > 1:
            entries.append(
                _setting(
                    f"credentials.{vendor}.mode",
                    "Credentials",
                    f"{_VENDOR_NAMES[vendor]} credential mode",
                    "choice",
                    f"ANALYSIS_MODEL_CREDENTIALS_{vendor.upper()}",
                    options=modes,
                    vendor=vendor,
                )
            )
        variables = {}
        for mode, source in row.credentials.items():
            for entry in source.env:
                variables.setdefault(entry.var, (entry.secret, []))[1].append(
                    mode.value
                )
        for var, (secret, var_modes) in variables.items():
            words = var.removeprefix(f"ANALYSIS_{vendor.upper()}_")
            words = words.removesuffix("_FILE").replace("_", " ").lower()
            words = words.replace("api", "API")
            stored = ready and vendor == "anthropic"
            entries.append(
                _setting(
                    f"credentials.{vendor}.{words.replace(' ', '_').lower()}",
                    "Credentials",
                    f"{_VENDOR_NAMES[vendor]} {words}",
                    "secret" if secret else "text",
                    var,
                    "set" if stored else "",
                    vendor=vendor,
                    modes=var_modes,
                    example=_EXAMPLES.get(var),
                    source="page" if stored else "default",
                )
            )
    return entries


def _vendor_modes() -> dict[str, list[str]]:
    from analysis_service.vendors import vendor_for

    return {
        vendor: [mode.value for mode in vendor_for(vendor).credential_modes]
        for vendor in _VENDORS
    }


def _setting(key, group, label, kind, env, value="", **extra):
    """One stub entry, in the shape the page module of ADR 0091 gives."""
    return {
        "key": key,
        "group": group,
        "label": label,
        "kind": kind,
        "env": env,
        "value": value,
        "mark": "changes",
        "source": "default",
        **extra,
    }


def _settings(state: str) -> list[dict[str, object]]:
    ready = state == "ready"
    chosen = {"base": "claude-sonnet-5-5", "strong": "claude-opus-5-5"}
    entries = []
    for tier in _TIERS:
        model = chosen.get(tier, "") if ready else ""
        entries += [
            _setting(
                f"tiers.{tier}.vendor",
                "Models",
                f"{tier.capitalize()} tier vendor",
                "choice",
                f"ANALYSIS_MODEL_{tier.upper()}_VENDOR",
                "anthropic" if model else "",
                options=_VENDORS,
                optional=tier == "review",
                source="page" if model else "default",
            ),
            _setting(
                f"tiers.{tier}.model",
                "Models",
                f"{tier.capitalize()} tier model",
                "text",
                f"ANALYSIS_MODEL_{tier.upper()}_MODEL",
                model,
                optional=tier == "review",
                source="page" if model else "default",
            ),
        ]
    entries += _credential_settings(ready)
    entries += [
        _setting(
            "frameworks.carried",
            "Frameworks",
            "Frameworks this deployment carries",
            "multi",
            "ANALYSIS_FRAMEWORKS_FILE",
            ["asvs", "stride"],
            options=["asvs", "stride"],
            source="file",
            file="config/frameworks.toml",
        ),
        _setting(
            "store.job",
            "Job storage",
            "Job store",
            "choice",
            "ANALYSIS_JOB_STORE",
            "memory",
            options=["memory", "sql"],
            restart=True,
        ),
        _setting(
            "store.database.url",
            "Job storage",
            "Database URL",
            "text",
            "ANALYSIS_DATABASE_URL",
            restart=True,
            when=["store.job", "sql"],
        ),
        _setting(
            "store.object",
            "Job storage",
            "Object store",
            "choice",
            "ANALYSIS_OBJECT_STORE",
            options=["filesystem", "s3"],
            restart=True,
            when=["store.job", "sql"],
        ),
        _setting(
            "store.object.root",
            "Job storage",
            "Object store directory",
            "text",
            "ANALYSIS_OBJECT_ROOT",
            restart=True,
            when=["store.object", "filesystem"],
        ),
        _setting(
            "passes.assertions",
            "Optional passes",
            "Assertion pass",
            "bool",
            "ANALYSIS_ASSERTIONS",
            True,
        ),
        _setting(
            "passes.capability_reader",
            "Optional passes",
            "Capability reader",
            "bool",
            "ANALYSIS_CAPABILITY_READER",
            True,
        ),
        _setting(
            "passes.compact_extraction",
            "Optional passes",
            "Compact extraction (experiment)",
            "bool",
            "ANALYSIS_COMPACT_EXTRACTION",
            False,
        ),
        _setting(
            "resilience.attempts",
            "Resilience and limits",
            "Attempts for each model call",
            "number",
            "ANALYSIS_RETRY_ATTEMPTS",
            3,
            source="file",
            file="config/resilience.toml",
        ),
        _setting(
            "resilience.timeout_ms",
            "Resilience and limits",
            "Timeout for each call (ms)",
            "number",
            "ANALYSIS_TIMEOUT_MS",
            300000,
            source="file",
            file="config/resilience.toml",
        ),
        _setting(
            "resilience.max_active_jobs",
            "Resilience and limits",
            "Analyses at the same time",
            "number",
            "ANALYSIS_MAX_ACTIVE_JOBS",
            2 if ready else 3,
            source="outside" if ready else "file",
            file="config/resilience.toml",
        ),
        _setting(
            "resilience.max_sources",
            "Resilience and limits",
            "Sources for each analysis",
            "number",
            "ANALYSIS_MAX_SOURCES",
            10,
            source="file",
            file="config/resilience.toml",
        ),
        _setting(
            "sampling.base.max_output_tokens",
            "Sampling",
            "Base tier output limit (tokens)",
            "number",
            "ANALYSIS_SAMPLING_BASE_MAX_OUTPUT_TOKENS",
            16384,
            source="file",
            file="config/sampling.toml",
            mark="shows",
            reason="A change makes each run uncertified until a sweep measures it.",
        ),
        _setting(
            "tiers.review_independence",
            "Review independence",
            "Review independence",
            "text",
            "",
            "shared",
            source="file",
            file="config/model_tiers.toml",
            mark="shows",
            reason="A change makes each run uncertified until a sweep measures it.",
        ),
    ]
    return entries


def mount(app: FastAPI) -> None:
    """Add the PROTOTYPE route to the first-run app."""

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_prototype(request: Request) -> HTMLResponse:
        state = request.query_params.get("state", "ready")
        if state not in ("ready", "first-run"):
            state = "ready"
        payload = {
            "state": state,
            "engine_error": (
                None
                if state == "ready"
                else LOADER_MESSAGES["no-vendor"].format(tiers="['base', 'strong']")
            ),
            "settings": _settings(state),
            "messages": LOADER_MESSAGES,
            "settings_dir": "~/.config/work-agent/settings",
            "models": _MODELS,
            "vendor_modes": _vendor_modes(),
            "iam_notes": _IAM_NOTES,
            "mode_notes": {mode.value: note for mode, note in CREDENTIAL_MODE_NOTES.items()},
        }
        return response(
            render(
                _PAGE,
                _GRANTS,
                script=client_script("settings_prototype.js"),
                payload=script_json(payload),
                workspace_style=_WORKSPACE_STYLE,
            )
        )


_WORKSPACE_STYLE = (Path(__file__).parent / "static" / "workspace.css").read_text(
    encoding="utf-8"
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings (prototype)</title>
<style nonce="__CSP_NONCE__"><!--workspace_style-->
  .proto-bar { position:fixed; left:50%; bottom:16px; transform:translateX(-50%);
    display:flex; gap:8px; align-items:center; padding:6px 10px; z-index:10;
    background:#111; color:#fff; border-radius:999px;
    box-shadow:0 6px 24px rgba(0,0,0,.35); font-size:13px; }
  .proto-bar button { min-height:30px; padding:4px 10px; background:#333;
    color:#fff; border-color:#555; }
  .proto-bar b { min-width:15rem; text-align:center; }
  .proto-state { margin:48px 0 96px; font-size:12px; }
  .proto-state pre { white-space:pre-wrap; overflow-wrap:anywhere;
    background:var(--sidebar); padding:12px; border-radius:7px; }
  .tag { font-size:11px; padding:1px 7px; border-radius:999px;
    border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
  .tag.outside { border-color:var(--accent); color:var(--accent); }
  .tag.restart { border-color:var(--error); color:var(--error); }
  .tag.not { border-color:var(--error); color:var(--error); }
  .notice { border-left:3px solid var(--accent); background:var(--soft);
    padding:10px 14px; margin:16px 0; border-radius:4px; }
  .refusal { border-left:3px solid var(--error); background:var(--error-bg);
    padding:10px 14px; margin:16px 0; border-radius:4px; }
  .refusal code, .notice code { overflow-wrap:anywhere; }
  .row { display:grid; grid-template-columns:minmax(0,15rem) minmax(0,1fr);
    gap:6px 16px; align-items:center; padding:10px 0;
    border-bottom:1px solid var(--line); }
  .row .meta { grid-column:2; display:flex; gap:6px; flex-wrap:wrap;
    font-size:12px; color:var(--muted); }
  .row input[type=text], .row input[type=password], .row input[type=number],
  .row select { width:100%; padding:8px; }
  .row.locked { opacity:.75; }
  .group-nav { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 24px; }
  .group-nav a { font-size:13px; padding:4px 10px; border:1px solid var(--line);
    border-radius:999px; text-decoration:none; }
  .savebar { position:sticky; bottom:64px; background:var(--canvas);
    border:1px solid var(--line); border-radius:9px; padding:10px 14px;
    display:flex; align-items:center; gap:12px; margin-top:24px;
    box-shadow:0 4px 16px rgba(0,0,0,.08); }
  .savebar span { flex:1; }
  .steps { list-style:none; padding:0; margin:0; display:grid; gap:12px; }
  .step { border:1px solid var(--line); border-radius:9px; padding:14px 16px; }
  .step header { display:flex; align-items:center; gap:10px; }
  .step header h2 { margin:0; font-size:16px; flex:1; }
  .step .num { width:26px; height:26px; border-radius:50%; display:grid;
    place-items:center; background:var(--line); font-size:13px; }
  .step.done .num { background:var(--accent); color:var(--on-accent); }
  .step .body { margin-top:10px; }
  .grid-c { display:grid; grid-template-columns:minmax(0,1fr) 19rem; gap:20px;
    align-items:start; }
  @media(max-width:1000px) { .grid-c { grid-template-columns:minmax(0,1fr); } }
  table.settings { display:table; width:100%; border-collapse:collapse;
    font-size:13px; }
  table.settings th, table.settings td { text-align:left; padding:6px 8px;
    border-bottom:1px solid var(--line); vertical-align:middle; }
  table.settings td code { font-size:11px; color:var(--muted); }
  table.settings input, table.settings select { width:100%; padding:5px; }
  table.settings tr.changed { background:var(--soft); }
  table.settings input[list] { min-width:12rem; }
  .table-wrap { overflow-x:auto; }
  .multi { position:relative; }
  .multi summary { list-style:none; cursor:pointer; border:1px solid var(--line);
    border-radius:7px; padding:5px 26px 5px 8px; background:var(--canvas);
    position:relative; min-width:12rem; }
  .multi summary::-webkit-details-marker { display:none; }
  .multi summary::after { content:"\\25BE"; position:absolute; right:9px; }
  .multi-menu { position:absolute; z-index:5; left:0; right:0; margin-top:4px;
    background:var(--canvas); border:1px solid var(--line); border-radius:7px;
    box-shadow:0 6px 18px rgba(0,0,0,.12); padding:6px; }
  .multi-menu label { display:flex; align-items:center; gap:8px; padding:6px 8px;
    border-radius:5px; }
  table.settings .multi-menu input { width:auto; margin:0; }
  table.settings input[type=checkbox] { width:auto; margin:0; }
  .hint.saved { color:var(--accent); }
  .hint.missing { color:var(--error); }
  table.settings .hint code { font-size:12px; color:var(--text); }
  .multi-menu label:hover { background:var(--soft); }
  table.settings small { display:block; }
  table.settings tr.groupline th { background:var(--sidebar); font-size:12px; }
  .pending { position:sticky; top:16px; border:1px solid var(--line);
    border-radius:9px; padding:14px; font-size:13px; }
  .pending ul { padding-left:18px; }
</style>
</head>
<body>
<div class="workspace">
  <aside class="sidebar">
    <a class="brand" href="/">Work Agent</a>
    <nav aria-label="Workspace"><a class="new-analysis" href="/">New analysis</a><a href="/">Analyses</a><a href="/settings" aria-current="page">Settings</a></nav>
    <div class="workspace-identity"><span class="avatar" aria-hidden="true">ME</span><span>Personal workspace<small>Only you · this computer</small></span></div>
  </aside>
  <div class="workspace-body">
    <header class="breadcrumb">Personal workspace <span aria-hidden="true">/</span> <span>Settings</span></header>
    <main id="main"><div id="variant"></div><details class="proto-state" open><summary>PROTOTYPE state: what a save would write</summary><pre id="proto-state"></pre></details></main>
  </div>
</div>
<div class="proto-bar" role="toolbar" aria-label="Prototype switcher">
  <button id="prev" type="button" aria-label="Previous variant">←</button>
  <b id="variant-label"></b>
  <button id="next" type="button" aria-label="Next variant">→</button>
  <button id="scenario" type="button"></button>
</div>
<script nonce="__CSP_NONCE__">const PROTO = <!--payload-->;
<!--script--></script>
</body>
</html>
"""


if __name__ == "__main__":
    # PROTOTYPE only: serve the stub page alone on the network, for a headless
    # host. The first-run app itself stays on loopback; this app has no other
    # route, reads no setting and writes nothing.
    import argparse
    import socket

    import uvicorn
    from fastapi.responses import PlainTextResponse
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from webapp.page import LOOPBACK_HOSTS, SecurityHeaders

    parser = argparse.ArgumentParser()
    parser.add_argument("hosts", nargs="*", help="extra names of this host")
    parser.add_argument(
        "--client", action="append", default=[], help="a client IP to accept"
    )
    args = parser.parse_args()

    port = 8017
    hosts = [*LOOPBACK_HOSTS, socket.gethostname(), *args.hosts]
    clients = {"127.0.0.1", *args.client}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def accept_listed_clients(request: Request, call_next):
        if request.client is None or request.client.host not in clients:
            return PlainTextResponse("This client is not accepted.", status_code=403)
        return await call_next(request)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(SecurityHeaders)
    mount(app)
    print(f"Settings prototype on port {port}: hosts {hosts}, clients {clients}")
    uvicorn.run(
        app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=False
    )
