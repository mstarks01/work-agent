"""PROTOTYPE for ticket #2118 on map #2111: three layouts of the Controls surfaces.

Throwaway. This module lives only on the branch ``prototype/controls-2118``
and never merges. It answers one question: how do the Controls page, the
submission offer and the report section look?

Three variants, switchable via ``?variant=A|B|C``, each with three views via
``?view=controls|offer|report``: the Controls page, the offer on the analysis
form, and the Controls section of a report.

The data is a stub held in the browser. Nothing reaches a store, and a
submission runs no job. The client script copies the rules that #2114 to #2117
settle: a row names one unit from a package's closed set, a scope matches by
element ID equality through the name and then the aliases, a Control fills
only an open fact, and the critic rules on each row per finding.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from webapp.page import (
    WORKSPACE_STYLE,
    Grants,
    client_script,
    render,
    response,
    script_json,
)

_GRANTS = Grants(script=True, style=True)

#: The units a row may name (#2114): a sample of the ASVS 5.0.0 catalog at its
#: three grains, and the six STRIDE lanes. The built page reads ``units``
#: from each Framework Package.
_UNITS = {
    "ASVS": {
        "version": "5.0.0",
        "units": [
            {"id": "V2", "grain": "chapter", "name": "Validation and Business Logic"},
            {"id": "V2.4", "grain": "section", "name": "Anti-automation"},
            {
                "id": "V2.4.1",
                "grain": "requirement",
                "name": "Anti-automation controls protect against excessive calls to application functions.",
            },
            {"id": "V6", "grain": "chapter", "name": "Authentication"},
            {"id": "V6.2", "grain": "section", "name": "Password Security"},
            {
                "id": "V6.2.1",
                "grain": "requirement",
                "name": "User set passwords are at least 8 characters in length.",
            },
            {
                "id": "V6.3",
                "grain": "section",
                "name": "General Authentication Security",
            },
            {"id": "V8", "grain": "chapter", "name": "Authorization"},
            {"id": "V11", "grain": "chapter", "name": "Cryptography"},
            {
                "id": "V11.2",
                "grain": "section",
                "name": "Secure Cryptography Implementation",
            },
            {"id": "V12", "grain": "chapter", "name": "Secure Communication"},
            {
                "id": "V12.1",
                "grain": "section",
                "name": "General TLS Security Guidance",
            },
            {
                "id": "V12.1.1",
                "grain": "requirement",
                "name": "Only the latest recommended versions of the TLS protocol are enabled.",
            },
            {
                "id": "V16",
                "grain": "chapter",
                "name": "Security Logging and Error Handling",
            },
        ],
    },
    "STRIDE": {
        "version": "1",
        "units": [
            {"id": "spoofing", "grain": "lane", "name": "Spoofing"},
            {"id": "tampering", "grain": "lane", "name": "Tampering"},
            {"id": "repudiation", "grain": "lane", "name": "Repudiation"},
            {
                "id": "information-disclosure",
                "grain": "lane",
                "name": "Information disclosure",
            },
            {"id": "denial-of-service", "grain": "lane", "name": "Denial of service"},
            {
                "id": "elevation-of-privilege",
                "grain": "lane",
                "name": "Elevation of privilege",
            },
        ],
    },
}

#: The element types a scope may name (#2115), and the facts each one leaves
#: open to a Control. The built page reads the open facts through
#: ``open_attribute``; the stub lists the control attributes of each type.
_SCOPE_TYPES = [
    {
        "type": "application",
        "label": "The whole application",
        "facts": ["security_logging", "rate_limiting", "secrets_management"],
    },
    {
        "type": "boundary",
        "label": "A trust boundary (zone)",
        "facts": ["network_filtering", "rate_limiting"],
    },
    {
        "type": "process",
        "label": "A process",
        "facts": ["authentication", "rate_limiting", "input_validation", "tls_version"],
    },
    {
        "type": "store",
        "label": "A data store",
        "facts": ["encryption_at_rest", "backup_access"],
    },
    {"type": "entity", "label": "An external entity", "facts": ["authentication"]},
]

#: The evidence classes the ASVS record reads in ``needs_evidence`` (#2113).
_EVIDENCE_CLASSES = [
    {
        "id": "config",
        "label": "Configuration",
        "hint": "A configuration file, a policy or a deployment manifest.",
    },
    {"id": "code", "label": "Code", "hint": "Source code or a build definition."},
    {
        "id": "people",
        "label": "People",
        "hint": "A procedure, a pentest report or a training record.",
    },
]

#: The new model a submission extracted, as the offer reads it. ``zone`` is
#: the stated trust zone; ``inferred`` marks a zone the service chose, and an
#: empty zone is an unplaced element (ADR 0039).
_MODEL = {
    "boundaries": [
        {"id": "boundary:dmz", "name": "DMZ"},
        {"id": "boundary:internal-network", "name": "Internal network"},
        {"id": "boundary:office", "name": "Office"},
    ],
    "elements": [
        {"id": "entity:shopper", "type": "entity", "name": "Shopper", "zone": ""},
        {
            "id": "process:web-portal",
            "type": "process",
            "name": "Web portal",
            "zone": "DMZ",
        },
        {
            "id": "process:api-gateway",
            "type": "process",
            "name": "API gateway",
            "zone": "DMZ",
        },
        {
            "id": "process:billing-api",
            "type": "process",
            "name": "Billing API",
            "zone": "Internal network",
        },
        {
            "id": "process:reporting-job",
            "type": "process",
            "name": "Reporting job",
            "zone": "Internal network",
            "inferred": True,
        },
        {
            "id": "process:admin-console",
            "type": "process",
            "name": "Admin console",
            "zone": "",
        },
        {
            "id": "store:orders-db",
            "type": "store",
            "name": "Orders DB",
            "zone": "Internal network",
        },
        {
            "id": "store:backups",
            "type": "store",
            "name": "Backups",
            "zone": "Internal network",
            "stated": {"encryption_at_rest": "AES-256 at the volume"},
        },
    ],
}

#: The Controls an owner keeps. One name holds markup, to show that the page
#: puts every name into a text node and never through ``innerHTML``.
_SEED = [
    {
        "id": "control-7d2e1b9c4a0f4e6b8c3d5a7f9e1b2c4d",
        "name": "Edge WAF and rate limit",
        "scope": {"type": "boundary", "name": "DMZ", "aliases": ["edge", "perimeter"]},
        "facts": [
            {
                "attribute": "network_filtering",
                "value": "Cloud WAF with the managed OWASP rule set",
                "detail": "",
            },
            {
                "attribute": "rate_limiting",
                "value": "100 requests a minute for each client IP",
                "detail": "The health endpoint is exempt.",
            },
        ],
        "rows": [
            {"framework": "ASVS", "unit": "V2.4", "version": "5.0.0"},
            {"framework": "STRIDE", "unit": "denial-of-service", "version": "1"},
        ],
        "evidence": [
            {
                "name": "waf-policy.yaml",
                "class": "config",
                "size": 2140,
                "excerpt": {
                    "lines": "12-19",
                    "text": "rate_limit:\n  window: 60s\n  requests: 100\n  key: client_ip\n  exempt:\n    - /healthz\nmanaged_rules: owasp-crs-4.3",
                },
            },
        ],
        "updated": "2026-10-21T10:12:00Z",
    },
    {
        "id": "control-3b8f0c2d6e1a4f7b9d2c4e6a8b0d1f3e",
        "name": "Orders DB encrypted at rest",
        "scope": {"type": "store", "name": "Orders DB", "aliases": ["orders database"]},
        "facts": [
            {
                "attribute": "encryption_at_rest",
                "value": "AES-256 through the cloud KMS key orders-prod",
                "detail": "",
            },
        ],
        "rows": [
            {"framework": "ASVS", "unit": "V11.2", "version": "5.0.0"},
            {"framework": "STRIDE", "unit": "information-disclosure", "version": "1"},
        ],
        "evidence": [
            {
                "name": "terraform/rds.tf",
                "class": "code",
                "size": 5320,
                "excerpt": {
                    "lines": "44-47",
                    "text": "storage_encrypted = true\nkms_key_id        = aws_kms_key.orders_prod.arn",
                },
            },
        ],
        "updated": "2026-10-28T16:40:00Z",
    },
    {
        "id": "control-9e4c6a1b3d5f4e8a0b2c4d6e8f0a1b3c",
        "name": "SSO for staff <b>(Okta)</b>",
        "scope": {
            "type": "process",
            "name": "Admin console",
            "aliases": ["back office"],
        },
        "facts": [
            {
                "attribute": "authentication",
                "value": "Okta SSO with a hardware key as the second factor",
                "detail": "",
            },
        ],
        "rows": [
            {"framework": "ASVS", "unit": "V6.2.1", "version": "5.0.0"},
            {"framework": "ASVS", "unit": "V6.3", "version": "5.0.0"},
            {"framework": "STRIDE", "unit": "spoofing", "version": "1"},
        ],
        "evidence": [
            {
                "name": "Access policy 2026.pdf",
                "class": "people",
                "size": 88210,
                "excerpt": {
                    "lines": "page 3",
                    "text": "All staff sign in to internal tools through Okta. A FIDO2 key is required for the admin console.",
                },
            },
        ],
        "updated": "2026-10-30T09:05:00Z",
    },
    {
        "id": "control-1f5a7c9e2b4d4f6a8c0e2a4c6e8a0c2e",
        "name": "Central security logging",
        "scope": {"type": "application", "name": "", "aliases": []},
        "facts": [
            {
                "attribute": "security_logging",
                "value": "Every service ships auth and admin events to the SIEM",
                "detail": "",
            },
        ],
        "rows": [
            {"framework": "ASVS", "unit": "V16", "version": "5.0.0"},
            {"framework": "STRIDE", "unit": "repudiation", "version": "1"},
        ],
        "evidence": [],
        "updated": "2026-10-15T12:00:00Z",
    },
]

_LIMITS = {
    "max_controls": 50,
    "name_chars": 200,
    "value_chars": 1000,
    "max_evidence_bytes": 102400,
}


def mount(app: FastAPI) -> None:
    """Add the PROTOTYPE route to an app."""

    @app.get("/controls", response_class=HTMLResponse)
    async def controls_prototype(request: Request) -> HTMLResponse:
        payload = {
            "units": _UNITS,
            "scope_types": _SCOPE_TYPES,
            "evidence_classes": _EVIDENCE_CLASSES,
            "model": _MODEL,
            "seed": _SEED,
            "limits": _LIMITS,
        }
        return response(
            render(
                _PAGE,
                _GRANTS,
                script="\n".join(
                    client_script(name)
                    for name in ("sidebar.js", "controls_prototype.js")
                ),
                payload=script_json(payload),
                workspace_style=WORKSPACE_STYLE,
                sidebar=_SIDEBAR,
            )
        )


_SIDEBAR = (
    (Path(__file__).parent / "sidebar.html")
    .read_text(encoding="utf-8")
    .replace(
        '<a href="/settings">',
        '<a id="controls-nav" href="/controls"><svg class="nav-icon" aria-hidden="true"'
        ' viewBox="0 0 24 24"><path d="M12,1L3,5V11C3,16.55 6.84,21.74 12,23C17.16,21.74'
        " 21,16.55 21,11V5L12,1M12,7C13.4,7 14.8,8.1 14.8,9.5V11C15.4,11 16,11.6 16,12.3V15.8"
        "C16,16.4 15.4,17 14.7,17H9.2C8.6,17 8,16.4 8,15.7V12.2C8,11.6 8.6,11 9.2,11V9.5"
        "C9.2,8.1 10.6,7 12,7M12,8.2C11.2,8.2 10.5,8.7 10.5,9.5V11H13.5V9.5C13.5,8.7 12.8,8.2"
        ' 12,8.2Z"/></svg><span class="nav-label">Controls</span></a><a href="/settings">',
    )
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Controls (prototype)</title>
<style nonce="__CSP_NONCE__"><!--workspace_style-->
  .proto-bar { position:fixed; left:50%; bottom:16px; transform:translateX(-50%);
    display:flex; gap:8px; align-items:center; padding:6px 10px; z-index:20;
    background:#111; color:#fff; border-radius:999px; flex-wrap:wrap;
    box-shadow:0 6px 24px rgba(0,0,0,.35); font-size:13px; max-width:96vw; }
  .proto-bar button, .proto-bar select { min-height:30px; padding:4px 10px;
    background:#333; color:#fff; border-color:#555; }
  .proto-bar b { min-width:14rem; text-align:center; }
  .proto-state { margin:48px 0 120px; font-size:12px; }
  .proto-state pre { white-space:pre-wrap; overflow-wrap:anywhere;
    background:var(--sidebar); padding:12px; border-radius:7px; }
  .refusal { border-left:3px solid var(--error); background:var(--error-bg);
    padding:10px 14px; margin:12px 0; border-radius:4px; }
  .notice { border-left:3px solid var(--accent); background:var(--soft);
    padding:10px 14px; margin:12px 0; border-radius:4px; }
  .count-line { display:flex; justify-content:space-between; align-items:baseline;
    gap:12px; flex-wrap:wrap; }
  .tag { font-size:11px; padding:1px 7px; border-radius:999px;
    border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
  .tag.applied { border-color:#2a7; color:#2a7; }
  .tag.refused { border-color:var(--error); color:var(--error); }
  .tag.no-finding, .tag.stated { border-color:var(--line); color:var(--muted); }
  .tag.written, .tag.matched { border-color:var(--accent); color:var(--accent); }
  .tag.changed { border-color:#c80; color:#c80; }
  .grid-table { width:100%; border-collapse:collapse; font-size:14px; }
  .grid-table th, .grid-table td { text-align:left; padding:8px;
    border-bottom:1px solid var(--line); vertical-align:top; }
  .grid-table td.name { overflow-wrap:anywhere; max-width:20rem; }
  .grid-table td.num { text-align:right; white-space:nowrap; }
  .grid-table td.act { width:1%; }
  .grid-table td.act button { display:block; }
  .table-wrap { overflow-x:auto; }
  .fanout li.fact { display:block; }
  .editor { display:grid; gap:14px; border:1px solid var(--line);
    border-radius:9px; padding:16px 18px; margin:16px 0 24px; }
  .editor h2 { margin:0; font-size:17px; }
  .editor input[type=text], .editor select, .editor textarea { width:100%; padding:8px; }
  .editor .line { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,2fr);
    gap:10px; align-items:start; }
  .editor fieldset { border:1px solid var(--line); border-radius:8px; padding:10px 14px; }
  .editor legend { font-weight:600; padding:0 4px; }
  .rows-list, .facts-list, .evidence-list { list-style:none; padding:0; margin:6px 0;
    display:grid; gap:6px; }
  .rows-list li, .facts-list li, .evidence-list li { display:grid;
    grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center;
    padding:6px 8px; border:1px solid var(--line); border-radius:6px; font-size:13px; }
  .rows-list .u, .facts-list .u, .evidence-list .u { overflow-wrap:anywhere; }
  .add-line { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,2fr) auto;
    gap:8px; align-items:end; }
  .add-line.three { grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) auto; }
  .aliases { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .master { display:grid; grid-template-columns:minmax(0,18rem) minmax(0,1fr);
    gap:20px; align-items:start; }
  @media(max-width:1000px) { .master { grid-template-columns:minmax(0,1fr); } }
  .master-list { list-style:none; padding:0; margin:0; display:grid; gap:6px; }
  .master-list button.row { width:100%; text-align:left; display:grid; gap:3px;
    padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:var(--canvas); }
  .master-list button.row[aria-pressed=true] { border-color:var(--accent); background:var(--soft); }
  .master-list .n { overflow-wrap:anywhere; font-weight:600; }
  .master-list .m { font-size:12px; color:var(--muted); }
  .detail { border:1px solid var(--line); border-radius:9px; padding:14px 18px; }
  .detail h2 { overflow-wrap:anywhere; margin-top:0; font-size:18px; }
  .tabs { display:flex; gap:4px; border-bottom:1px solid var(--line); margin:8px 0 14px; }
  .tabs button { border:0; border-bottom:2px solid transparent; background:none;
    padding:8px 12px; border-radius:0; }
  .tabs button[aria-selected=true] { border-bottom-color:var(--accent); font-weight:600; }
  .group { margin:18px 0; }
  .group h2 { font-size:16px; margin:0 0 6px; display:flex; gap:8px; align-items:baseline; }
  .group ul { list-style:none; padding:0; margin:0; display:grid; gap:4px; }
  .group li { padding:8px 10px; border:1px solid var(--line); border-radius:7px; }
  .group li summary { cursor:pointer; }
  .offer { border:1px solid var(--line); border-radius:8px; padding:8px 12px; margin:0 0 12px; }
  .offer .offer-row { display:grid; grid-template-columns:auto minmax(0,1fr); gap:8px;
    align-items:start; padding:8px 0; border-bottom:1px solid var(--line); }
  .offer .offer-row:last-of-type { border-bottom:0; }
  .offer .n { font-weight:600; overflow-wrap:anywhere; display:block; }
  .offer .m { font-size:12px; color:var(--muted); }
  .offer .why { font-size:12px; color:var(--error); }
  .offer .choice { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:4px; font-size:13px; }
  .fanout { list-style:none; padding:0; margin:4px 0 0; font-size:12px; display:grid; gap:2px; }
  .fanout li { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  .fanout .unticked { color:var(--muted); }
  .elements { list-style:none; padding:0; margin:0; display:grid; gap:6px; }
  .elements > li { border:1px solid var(--line); border-radius:8px; padding:8px 12px; }
  .elements .head { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
  .elements .head b { overflow-wrap:anywhere; }
  .elements .ctl { display:grid; grid-template-columns:auto minmax(0,1fr); gap:8px;
    align-items:start; padding:4px 0 4px 8px; font-size:13px; }
  .control-card { border:1px solid var(--line); border-radius:9px; padding:12px 16px; margin:10px 0; }
  .control-card h3 { margin:0 0 4px; font-size:16px; overflow-wrap:anywhere; }
  .control-card .scope-line { font-size:13px; color:var(--muted); margin:0 0 8px; }
  .chips { display:flex; gap:6px; flex-wrap:wrap; margin:6px 0; }
  .chip { font-size:12px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); }
  .chip.applied { background:#e6f6ee; border-color:#2a7; }
  .chip.refused { background:var(--error-bg); border-color:var(--error); }
  blockquote.excerpt { margin:6px 0; padding:6px 10px; border-left:3px solid var(--line);
    background:var(--sidebar); font-family:ui-monospace, monospace; font-size:12px;
    white-space:pre-wrap; overflow-wrap:anywhere; }
  .filters { display:flex; gap:6px; flex-wrap:wrap; margin:8px 0; }
  .filters button[aria-pressed=true] { background:var(--soft); border-color:var(--accent); }
  .report-h { font-size:20px; margin:24px 0 4px; }
  .report-lead { color:var(--muted); margin:0 0 12px; }
  .sub { font-size:12px; color:var(--muted); }
  dialog.wide { width:min(48rem, 94vw); max-height:88vh; }
  .form-stub textarea { width:100%; min-height:7rem; padding:8px; }
  .form-stub .field-label { margin-top:10px; }
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="workspace">
  <aside class="sidebar">
    <!--sidebar-->
  </aside>
  <div class="workspace-body">
    <main id="main"><div id="variant"></div><details class="proto-state"><summary>PROTOTYPE state: what the store, a submission and a report copy would hold</summary><pre id="proto-state"></pre></details></main>
  </div>
</div>
<div class="proto-bar" role="toolbar" aria-label="Prototype switcher">
  <button id="prev" type="button" aria-label="Previous variant">&larr;</button>
  <b id="variant-label"></b>
  <button id="next" type="button" aria-label="Next variant">&rarr;</button>
  <select id="view" aria-label="View"></select>
  <select id="scenario" aria-label="Scenario"></select>
</div>
<script nonce="__CSP_NONCE__">const PROTO = <!--payload-->;
<!--script--></script>
</body>
</html>
"""


if __name__ == "__main__":
    # PROTOTYPE only: serve the stub page alone on the network, for a headless
    # host. This app has no other route, reads no setting and writes nothing.
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

    port = 8019
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
    print(f"Controls prototype on port {port}: hosts {hosts}, clients {clients}")
    uvicorn.run(
        app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=False
    )
