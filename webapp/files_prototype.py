"""PROTOTYPE for ticket #2062 on map #2057: three layouts of the Files section.

Throwaway. This module lives only on the branch ``prototype/files-page-2062``
and never merges. It answers one question: how does the Files section look,
and how does a person add an Owner File to an analysis?

Three variants, switchable via ``?variant=A|B|C``, each with two views via
``?view=files|form``: the Files section and the analysis form with its picker.

The data is a stub held in the browser. Nothing reaches a store, and a
submission runs no job. The client script runs the real browser checks of the
direct upload, and it copies the server checks that ADRs 0118-0120 settle, so
that each refusal reads as it will in the built page.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from analysis_service.sources import ANSWERS_LABEL
from webapp.page import (
    WORKSPACE_STYLE,
    Grants,
    client_script,
    render,
    response,
    script_json,
)

_GRANTS = Grants(script=True, style=True)

#: The format table of ADR 0120, as the server would render it into the page.
_FORMATS = [
    {
        "format": "text/plain",
        "label": "Plain text",
        "extensions": [".txt", ".vtt", ".srt"],
        "kinds": ["description", "transcript"],
        "default_kind": "transcript",
    },
    {
        "format": "text/vnd.mermaid",
        "label": "Mermaid",
        "extensions": [".mmd", ".mermaid"],
        "kinds": ["diagram"],
        "default_kind": "diagram",
    },
]

#: The limits the page reads: ``max_source_bytes`` and ``max_sources`` from
#: resilience.toml, ``max_owner_files`` from ADR 0118, the label limit of a
#: Source.
_LIMITS = {
    "max_source_bytes": 102400,
    "max_sources": 10,
    "max_owner_files": 100,
    "name_chars": 200,
}

#: The labels the page gives its own Sources. An Owner File with one of these
#: names collides with a direct upload in the same job.
_PAGE_LABELS = [
    "Pasted description",
    "Uploaded transcript",
    "Uploaded diagram",
    ANSWERS_LABEL,
]

_SEED = [
    {
        "id": "file-3f1c9a0d6e2b4c58a7d19e0f2b6c4a81",
        "name": "Kickoff call 2026-10-14.vtt",
        "kind": "transcript",
        "format": "text/plain",
        "text": (
            "WEBVTT\n\n00:00:01.000 --> 00:00:06.000\n"
            "<v Dana>The portal talks to the billing API over mTLS.</v>\n"
        )
        * 40,
        "added": "2026-10-14T15:02:00Z",
    },
    {
        "id": "file-9b0e7d2c41a84f6e93c5d8a1b2e7f034",
        "name": "Network diagram",
        "kind": "diagram",
        "format": "text/vnd.mermaid",
        "text": (
            "flowchart LR\n"
            "  shopper([Shopper]) -->|HTTPS| web[Web portal]\n"
            "  web -->|mTLS| billing[Billing API]\n"
            "  billing --> db[(Orders DB)]\n"
        ),
        "added": "2026-10-20T09:41:00Z",
    },
    {
        "id": "file-c27a5e9f0d1b43e6b8f4a2c9d7e1036b",
        "name": "Architecture notes",
        "kind": "description",
        "format": "text/plain",
        "text": "The portal runs on two VMs behind a load balancer.\n" * 600,
        "added": "2026-10-28T17:20:00Z",
    },
    {
        "id": "file-71d0b4e8a92c4f1d8e6a3c5b0f9d2e47",
        "name": "All-hands call 2026-10-27.srt",
        "kind": "transcript",
        "format": "text/plain",
        "text": "1\n00:00:01,000 --> 00:00:04,000\nThe batch job copies orders nightly.\n\n"
        * 1100,
        "added": "2026-10-27T13:30:00Z",
    },
    {
        "id": "file-5e8d1a3b7c2f4096a1e3d5b7c9f20a14",
        "name": '<img src=x onerror="alert(1)"> security review.txt',
        "kind": "transcript",
        "format": "text/plain",
        "text": "Q: Who can read the backups?\nA: <script>alert(1)</script>\n",
        "added": "2026-10-30T11:05:00Z",
    },
]


def mount(app: FastAPI) -> None:
    """Add the PROTOTYPE route to an app."""

    @app.get("/files", response_class=HTMLResponse)
    async def files_prototype(request: Request) -> HTMLResponse:
        payload = {
            "formats": _FORMATS,
            "limits": _LIMITS,
            "page_labels": _PAGE_LABELS,
            "seed": _SEED,
        }
        return response(
            render(
                _PAGE,
                _GRANTS,
                script="\n".join(
                    client_script(name) for name in ("sidebar.js", "files_prototype.js")
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
        '<a id="files-nav" href="/files"><svg class="nav-icon" aria-hidden="true"'
        ' viewBox="0 0 24 24"><path d="M14,2H6A2,2 0 0,0 4,4V20A2,2 0 0,0 6,22H18A2,2'
        ' 0 0,0 20,20V8L14,2M18,20H6V4H13V9H18V20Z"/></svg>'
        '<span class="nav-label">Files</span></a><a href="/settings">',
    )
)

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Files (prototype)</title>
<style nonce="__CSP_NONCE__"><!--workspace_style-->
  .proto-bar { position:fixed; left:50%; bottom:16px; transform:translateX(-50%);
    display:flex; gap:8px; align-items:center; padding:6px 10px; z-index:20;
    background:#111; color:#fff; border-radius:999px; flex-wrap:wrap;
    box-shadow:0 6px 24px rgba(0,0,0,.35); font-size:13px; max-width:96vw; }
  .proto-bar button, .proto-bar select { min-height:30px; padding:4px 10px;
    background:#333; color:#fff; border-color:#555; }
  .proto-bar b { min-width:13rem; text-align:center; }
  .proto-state { margin:48px 0 120px; font-size:12px; }
  .proto-state pre { white-space:pre-wrap; overflow-wrap:anywhere;
    background:var(--sidebar); padding:12px; border-radius:7px; }
  .refusal { border-left:3px solid var(--error); background:var(--error-bg);
    padding:10px 14px; margin:12px 0; border-radius:4px; }
  .notice { border-left:3px solid var(--accent); background:var(--soft);
    padding:10px 14px; margin:12px 0; border-radius:4px; }
  .tag { font-size:11px; padding:1px 7px; border-radius:999px;
    border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
  .files-table { width:100%; border-collapse:collapse; font-size:14px; }
  .files-table th, .files-table td { text-align:left; padding:8px;
    border-bottom:1px solid var(--line); vertical-align:middle; }
  .files-table td.name { overflow-wrap:anywhere; max-width:22rem; }
  .files-table td.pick { width:1%; }
  .files-table td.num { text-align:right; white-space:nowrap; }
  .upload-form { display:grid; gap:10px; border:1px solid var(--line);
    border-radius:9px; padding:14px 16px; margin:16px 0 24px; }
  .upload-form .line { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .upload-form input[type=text] { width:100%; padding:8px; }
  .kind-choice { display:flex; gap:16px; border:0; padding:0; margin:0; }
  .kind-choice legend { padding:0; margin-bottom:4px; }
  .count-line { display:flex; justify-content:space-between; align-items:baseline;
    gap:12px; flex-wrap:wrap; }
  .lib { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,24rem);
    gap:20px; align-items:start; }
  @media(max-width:1000px) { .lib { grid-template-columns:minmax(0,1fr); } }
  .lib-list { list-style:none; padding:0; margin:0; display:grid; gap:6px; }
  .lib-list button.row { width:100%; text-align:left; display:grid;
    grid-template-columns:minmax(0,1fr) auto; gap:4px 12px; padding:10px 12px;
    border:1px solid var(--line); border-radius:8px; background:var(--canvas); }
  .lib-list button.row[aria-pressed=true] { border-color:var(--accent);
    background:var(--soft); }
  .lib-list .row .n { overflow-wrap:anywhere; font-weight:600; }
  .lib-list .row .m { grid-column:1 / -1; font-size:12px; color:var(--muted); }
  .detail { position:sticky; top:16px; border:1px solid var(--line);
    border-radius:9px; padding:14px 16px; }
  .detail h2 { overflow-wrap:anywhere; margin-top:0; font-size:17px; }
  .detail pre { max-height:16rem; overflow:auto; white-space:pre-wrap;
    overflow-wrap:anywhere; background:var(--sidebar); padding:10px;
    border-radius:6px; font-size:12px; }
  .detail dl { display:grid; grid-template-columns:auto 1fr; gap:4px 12px;
    font-size:13px; }
  .detail dd { margin:0; overflow-wrap:anywhere; }
  .picked { list-style:none; padding:0; margin:8px 0; display:grid; gap:4px; }
  .picked li { display:flex; gap:8px; align-items:center; flex-wrap:wrap;
    font-size:13px; }
  .picked li span.n { overflow-wrap:anywhere; }
  .picker-menu { position:absolute; right:0; bottom:44px; z-index:6; width:22rem;
    max-width:80vw; background:var(--canvas); border:1px solid var(--line);
    border-radius:8px; box-shadow:0 6px 18px rgba(0,0,0,.14); padding:6px; }
  .picker-menu input[type=search] { width:100%; padding:6px; margin-bottom:6px; }
  .picker-menu button { width:100%; text-align:left; display:block;
    padding:6px 8px; border:0; background:none; overflow-wrap:anywhere; }
  .picker-menu button:hover, .picker-menu button:focus { background:var(--soft); }
  .picker-menu button[disabled] { opacity:.5; }
  .composer-tools { position:relative; display:flex; gap:4px; }
  .checklist { border:1px solid var(--line); border-radius:8px; padding:6px 10px;
    margin:0 0 12px; }
  .checklist label { display:grid; grid-template-columns:auto minmax(0,1fr) auto;
    gap:8px; align-items:center; padding:6px 0; border-bottom:1px solid var(--line); }
  .checklist label:last-of-type { border-bottom:0; }
  .checklist .n { overflow-wrap:anywhere; }
  meter { width:100%; }
  dialog.files-dialog { width:min(46rem, 94vw); max-height:88vh; }
  .drop { border:2px dashed var(--line); border-radius:9px; padding:16px;
    text-align:center; }
  .drop.over { border-color:var(--accent); background:var(--soft); }
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="workspace">
  <aside class="sidebar">
    <!--sidebar-->
  </aside>
  <div class="workspace-body">
    <main id="main"><div id="variant"></div><details class="proto-state"><summary>PROTOTYPE state: what the store and a submission would hold</summary><pre id="proto-state"></pre></details></main>
  </div>
</div>
<div class="proto-bar" role="toolbar" aria-label="Prototype switcher">
  <button id="prev" type="button" aria-label="Previous variant">&larr;</button>
  <b id="variant-label"></b>
  <button id="next" type="button" aria-label="Next variant">&rarr;</button>
  <button id="view" type="button"></button>
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

    port = 8018
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
    print(f"Files prototype on port {port}: hosts {hosts}, clients {clients}")
    uvicorn.run(
        app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=False
    )
