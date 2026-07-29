"""PROTOTYPE — throwaway, delete me. Three shapes for the first-run web app.

Question (wayfinder ticket #29): what does the unbloated web app look like, and how much
of ``docs/example-report.html`` does it reuse?

Three variants of the whole app, switchable with ``?variant=`` and the floating bottom
bar. They disagree about page inventory, viewer reuse, waiting UX and render depth — the
four axes the ticket asks about:

    A  Extend the viewer in place. One URL, plain full-page form POST, the app IS the
       viewer with a textarea grafted on. Maximum reuse, maximum coupling.
    B  Viewer as a template. Three URLs: form → progress page (per-node ticks over SSE,
       fed by ``on_node``) → report page that is the untouched viewer with JSON injected.
    C  Own minimal renderer. One URL, split pane, fetch + SSE, and a deliberate subset of
       the report — a plain table. The viewer stays a docs artifact, linked not embedded.

Run:   uv run --with uvicorn python prototype/webapp_shape.py
Open:  http://127.0.0.1:8471/?variant=A

No credentials needed: ``_FakeEngine`` replays the example report from
``docs/example-report.html`` after a scripted node sequence with real delays. The shape is
the question; whether the models answer is not.
"""

from __future__ import annotations

import asyncio
import copy
import html
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

REPO = Path(__file__).resolve().parent.parent
VIEWER = REPO / "docs" / "example-report.html"
SAMPLE = Path(__file__).resolve().parent / "orders_sample.md"

VARIANTS = {
    "A": "Extend the viewer in place — one URL",
    "B": "Viewer as a template — form → progress → report",
    "C": "Own renderer, deliberate subset — one URL, split pane",
}

# The node sequence a real run emits through on_node, with plausible delays: this is what
# any progress UI has to work with. Names are graph.py's, verbatim.
NODE_SCRIPT: tuple[tuple[str, float], ...] = (
    ("extract", 3.2),
    ("validate", 0.1),
    ("prepare", 0.1),
    ("analyst_spoofing", 4.0),
    ("analyst_tampering", 3.6),
    ("analyst_repudiation", 3.1),
    ("analyst_information_disclosure", 4.4),
    ("analyst_denial_of_service", 3.3),
    ("analyst_elevation_of_privilege", 3.9),
    ("join", 0.1),
    ("merge", 0.1),
    ("critic", 9.5),
    ("router", 0.1),
    ("assemble", 0.1),
)
# Analysts run concurrently in the real graph, so their ticks land close together; the
# prototype divides their delays to keep the whole fake run near 20s.
ANALYST_SPEEDUP = 6.0

# Reflected model selection, per #28: read-only, one line per tier, no credential status.
TIERS = (("base", "vertex / gemini-2.5-flash"), ("strong", "vertex / gemini-2.5-pro"))


def _example_report() -> dict:
    """The viewer's own inline report, reused as the fake engine's output."""
    text = VIEWER.read_text(encoding="utf-8")
    block = re.search(
        r'<script type="application/json" id="report">(.*?)</script>', text, re.DOTALL
    )
    return json.loads(block.group(1))


def _blank_report() -> dict:
    """A well-formed empty report, because variant A's viewer JS cannot render nothing.

    This is a cost of extending the viewer in place, not an incidental prototype hack:
    the viewer indexes ``R.summary`` and iterates ``R.threats`` unconditionally, so the
    pre-analysis state of a one-page app has to be a real report shape or the page throws.
    """
    blank = copy.deepcopy(_example_report())
    blank["job"] = {
        "id": "—",
        "status": "awaiting input",
        "created_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:00:00Z",
        "revise_rounds": 0,
    }
    blank["input"] = {"system_name": "No analysis yet", "source_sha256": "0" * 64}
    blank["threats"] = []
    blank["rejected_threats"] = []
    blank["boundary_crossings"] = []
    blank["nodes"] = []
    blank["system_model"] = {key: [] for key in blank["system_model"]}
    blank["summary"] = {
        "threat_count": 0,
        "by_category": {},
        "by_severity": {},
        "needs_info_count": 0,
        "rejected_count": 0,
        "elements_analyzed": 0,
    }
    return blank


def _json_for_script(payload: dict) -> str:
    """Serialise for a ``<script>`` block without letting the data close the block.

    A report carries the submitter's own prose in ``source_excerpt``, so ``</script>`` in
    the description would otherwise end the block and run whatever followed as HTML — the
    one XSS in the inject-into-the-viewer approach. Escaping ``<`` closes it for good.
    """
    return json.dumps(payload).replace("<", "\\u003c")


def _inject_report(viewer_html: str, report: dict) -> str:
    """Swap the viewer's inline report for this one. The viewer file is not edited."""
    return re.sub(
        r'(<script type="application/json" id="report">)(.*?)(</script>)',
        lambda m: m.group(1) + _json_for_script(report) + m.group(3),
        viewer_html,
        flags=re.DOTALL,
    )


@dataclass
class _Run:
    """One in-flight fake analysis."""

    description: str
    nodes: list[str] = field(default_factory=list)
    report: dict | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class _FakeEngine:
    """StrideEngine's shape, with the models replaced by sleeps."""

    def __init__(self) -> None:
        self._report = _example_report()

    async def analyze(self, description: str, *, on_node=None) -> dict:
        for node, seconds in NODE_SCRIPT:
            await asyncio.sleep(
                seconds / ANALYST_SPEEDUP if node.startswith("analyst") else seconds
            )
            if on_node:
                await on_node(node)
        report = copy.deepcopy(self._report)
        report["input"]["system_name"] = "Orders Service"
        return report


ENGINE = _FakeEngine()
RUNS: dict[str, _Run] = {}
# LLM10: an unauthenticated loopback page is a free-spend surface. One run at a time.
RUN_SLOT = asyncio.Semaphore(1)

app = FastAPI()


def _require_same_origin(request: Request) -> None:
    """Loopback and no auth (#28) still leaves CSRF: any site you visit can POST here.

    ``Sec-Fetch-Site`` is browser-set and unspoofable from script, and it is sent on plain
    form posts too — so one check covers both the form shapes (A, B) and the fetch shape
    (C). Missing header (curl) is allowed here so the prototype stays pokeable.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        raise HTTPException(status_code=403, detail="cross-origin submission refused")


def _variant(request: Request) -> str:
    asked = request.query_params.get("variant", "A").upper()
    return asked if asked in VARIANTS else "A"


# --- shared chrome ----------------------------------------------------------------


def _switcher(current: str) -> str:
    keys = list(VARIANTS)
    idx = keys.index(current)
    prev_key, next_key = keys[idx - 1], keys[(idx + 1) % len(keys)]
    return f"""
<div id="proto-bar">
  <a href="/?variant={prev_key}" aria-label="previous variant">&larr;</a>
  <span><b>{current}</b> — {html.escape(VARIANTS[current])}</span>
  <a href="/?variant={next_key}" aria-label="next variant">&rarr;</a>
</div>
<style>
  #proto-bar {{ position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
    display: flex; gap: 14px; align-items: center; z-index: 9999;
    background: #111; color: #fff; padding: 8px 16px; border-radius: 999px;
    font: 13px/1.4 ui-monospace, monospace; box-shadow: 0 6px 24px rgba(0,0,0,.35); }}
  #proto-bar a {{ color: #fff; text-decoration: none; font-size: 16px; }}
</style>
<script>
  document.addEventListener("keydown", (e) => {{
    const t = e.target;
    if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
    if (e.key === "ArrowLeft") location.href = "/?variant={prev_key}";
    if (e.key === "ArrowRight") location.href = "/?variant={next_key}";
  }});
</script>
"""


def _tier_lines() -> str:
    rows = "".join(
        f"<div><code>{html.escape(tier)}</code> &rarr; {html.escape(model)}</div>"
        for tier, model in TIERS
    )
    return f'<div class="tiers">{rows}</div>'


def _page(title: str, body: str, variant: str, *, style: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 32px;
    max-width: 860px; margin-inline: auto; color: #16181d; background: #fbfbfc; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  textarea {{ width: 100%; min-height: 220px; font: 13px/1.5 ui-monospace, monospace;
    padding: 10px; border: 1px solid #ccd; border-radius: 8px; box-sizing: border-box; }}
  button {{ font: 14px system-ui; padding: 8px 16px; border-radius: 8px;
    border: 1px solid #234; background: #234; color: #fff; cursor: pointer; }}
  button.ghost {{ background: #fff; color: #234; }}
  .tiers {{ font: 12px ui-monospace, monospace; color: #667; margin: 8px 0 20px; }}
  .row {{ display: flex; gap: 10px; align-items: center; margin-top: 12px; }}
  .muted {{ color: #667; font-size: 13px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
  th, td {{ text-align: left; padding: 7px 9px; border-bottom: 1px solid #e3e3e8;
    vertical-align: top; font-size: 13px; }}
  th {{ background: #f2f2f5; }}
  code {{ font-size: 12px; background: #f0f0f4; padding: 1px 4px; border-radius: 4px; }}
  {style}
</style></head><body>{body}{_switcher(variant)}</body></html>""")


# --- routes ----------------------------------------------------------------------


@app.get("/")
async def index(request: Request) -> HTMLResponse:
    variant = _variant(request)
    prefill = (
        SAMPLE.read_text(encoding="utf-8") if "sample" in request.query_params else ""
    )
    if variant == "A":
        return _variant_a_page(prefill, report=_blank_report())
    if variant == "B":
        return _variant_b_form(prefill)
    return _variant_c_page()


@app.get("/sample")
async def sample() -> HTMLResponse:
    """Load example, for the variants that fill the textarea without a page load."""
    return HTMLResponse(SAMPLE.read_text(encoding="utf-8"), media_type="text/plain")


# Variant A — one URL, full-page POST, the viewer with a textarea grafted on -------


def _variant_a_page(prefill: str, *, report: dict) -> HTMLResponse:
    viewer = _inject_report(VIEWER.read_text(encoding="utf-8"), report)
    panel = f"""
    <form method="post" action="/?variant=A" class="proto-input">
      <h2 style="margin-top:0">Describe your system</h2>
      {_tier_lines()}
      <textarea name="description" rows="10"
        placeholder="Paste prose describing your system…">{html.escape(prefill)}</textarea>
      <div class="row">
        <button type="submit">Analyze</button>
        <a class="loadex" href="/?variant=A&amp;sample=1">Load example</a>
        <span class="muted">a real run takes ~40s; this page blocks until it finishes</span>
      </div>
    </form>
    <style>
      .proto-input {{ border: 1px solid var(--line, #ccd); border-radius: 10px;
        padding: 18px; margin-bottom: 28px; }}
      .proto-input textarea {{ width: 100%; box-sizing: border-box;
        font: 13px/1.5 ui-monospace, monospace; }}
      .proto-input .row {{ display: flex; gap: 12px; align-items: center;
        margin-top: 10px; }}
      .proto-input .muted {{ font-size: 12px; opacity: .7; }}
      .tiers {{ font: 12px ui-monospace, monospace; opacity: .7; margin-bottom: 10px; }}
    </style>
    """
    page = viewer.replace('<div class="wrap">', f'<div class="wrap">{panel}', 1)
    page = page.replace("</body>", f"{_switcher('A')}</body>", 1)
    return HTMLResponse(page)


@app.post("/")
async def analyze_form(request: Request, description: str = Form("")) -> HTMLResponse:
    _require_same_origin(request)
    if _variant(request) == "B":
        return await _variant_b_submit(description)
    if not description.strip():
        return _variant_a_page("", report=_blank_report())
    async with RUN_SLOT:
        report = await ENGINE.analyze(description)
    return _variant_a_page(description, report=report)


# Variant B — three URLs: form → progress (SSE) → viewer as template ---------------


def _variant_b_form(prefill: str) -> HTMLResponse:
    body = f"""
    <h1>STRIDE — analyse a system</h1>
    <p class="muted">Paste a description. The analysis runs on the models this deployment
      is configured for; the report opens on its own page.</p>
    {_tier_lines()}
    <form method="post" action="/?variant=B">
      <textarea name="description"
        placeholder="Paste prose describing your system…">{html.escape(prefill)}</textarea>
      <div class="row">
        <button type="submit">Analyze</button>
        <a href="/?variant=B&amp;sample=1"><button class="ghost" type="button">Load example</button></a>
      </div>
    </form>
    """
    return _page("STRIDE — analyse a system", body, "B")


async def _variant_b_submit(description: str) -> HTMLResponse:
    if not description.strip():
        return _variant_b_form("")
    run_id = _start_run(description)
    return RedirectResponse(f"/progress/{run_id}?variant=B", status_code=303)


def _start_run(description: str) -> str:
    run_id = uuid.uuid4().hex
    run = _Run(description=description)
    RUNS[run_id] = run

    async def drive() -> None:
        async def tick(node: str) -> None:
            run.nodes.append(node)

        async with RUN_SLOT:
            run.report = await ENGINE.analyze(description, on_node=tick)
        run.done.set()

    asyncio.get_running_loop().create_task(drive())
    return run_id


@app.get("/progress/{run_id}")
async def progress(run_id: str, request: Request) -> HTMLResponse:
    if run_id not in RUNS:
        raise HTTPException(404)
    body = f"""
    <h1>Analysing…</h1>
    <p class="muted">Fourteen nodes, six of them analysts running in parallel. Leave this
      page open; it redirects to the report when the critic has ruled.</p>
    <ol id="nodes" class="nodes"></ol>
    <style>
      .nodes {{ font: 13px ui-monospace, monospace; }}
      .nodes li {{ margin: 2px 0; }}
    </style>
    <script>
      const seen = new Set();
      const es = new EventSource("/events/{run_id}");
      es.addEventListener("node", (e) => {{
        if (seen.has(e.data)) return;
        seen.add(e.data);
        const li = document.createElement("li");
        li.textContent = e.data;
        document.getElementById("nodes").append(li);
      }});
      es.addEventListener("done", () => {{
        es.close();
        location.href = "/report/{run_id}?variant=B";
      }});
    </script>
    """
    return _page("Analysing…", body, "B")


@app.get("/events/{run_id}")
async def events(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404)

    async def stream():
        sent = 0
        while not run.done.is_set() or sent < len(run.nodes):
            while sent < len(run.nodes):
                yield f"event: node\ndata: {run.nodes[sent]}\n\n"
                sent += 1
            await asyncio.sleep(0.2)
        yield "event: done\ndata: ok\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/report/{run_id}")
async def report_page(run_id: str) -> HTMLResponse:
    run = RUNS.get(run_id)
    if run is None or run.report is None:
        raise HTTPException(404)
    page = _inject_report(VIEWER.read_text(encoding="utf-8"), run.report)
    page = page.replace("</body>", f"{_switcher('B')}</body>", 1)
    return HTMLResponse(page)


# Variant C — one URL, split pane, own minimal renderer over a subset --------------


def _variant_c_page() -> HTMLResponse:
    body = f"""
    <h1>STRIDE</h1>
    {_tier_lines()}
    <div class="split">
      <div>
        <textarea id="desc" placeholder="Paste prose describing your system…"></textarea>
        <div class="row">
          <button id="go">Analyze</button>
          <button class="ghost" id="ex">Load example</button>
        </div>
      </div>
      <div id="out"><p class="muted">The report appears here.</p></div>
    </div>
    <style>
      body {{ max-width: 1180px; }}
      .split {{ display: grid; grid-template-columns: 380px 1fr; gap: 28px;
        align-items: start; }}
      #out {{ min-height: 300px; }}
      .log {{ font: 12px ui-monospace, monospace; color: #667; }}
      .sev-high {{ color: #a4262c; font-weight: 600; }}
      .sev-medium {{ color: #8a6116; font-weight: 600; }}
      .sev-low {{ color: #4a6b45; font-weight: 600; }}
      .sev-critical {{ color: #6e1420; font-weight: 700; }}
    </style>
    <script>
      const out = document.getElementById("out");
      document.getElementById("ex").addEventListener("click", async () => {{
        document.getElementById("desc").value = await (await fetch("/sample")).text();
      }});
      document.getElementById("go").addEventListener("click", async () => {{
        const description = document.getElementById("desc").value;
        if (!description.trim()) return;
        out.innerHTML = '<div class="log" id="log">starting…</div>';
        const r = await fetch("/start?variant=C", {{
          method: "POST",
          headers: {{ "content-type": "application/json" }},
          body: JSON.stringify({{ description }}),
        }});
        const {{ run_id }} = await r.json();
        const log = document.getElementById("log");
        const es = new EventSource("/events/" + run_id);
        es.addEventListener("node", (e) => {{ log.textContent = "running: " + e.data; }});
        es.addEventListener("done", async () => {{
          es.close();
          out.innerHTML = await (await fetch("/render/" + run_id)).text();
        }});
      }});
    </script>
    """
    return _page("STRIDE", body, "C")


@app.post("/start")
async def start(request: Request) -> JSONResponse:
    _require_same_origin(request)
    payload = await request.json()
    description = str(payload.get("description", ""))
    if not description.strip():
        raise HTTPException(400, "description must be non-empty")
    return JSONResponse({"run_id": _start_run(description)})


@app.get("/render/{run_id}")
async def render_subset(run_id: str) -> HTMLResponse:
    """The deliberate subset: threats as a table, plus a link out to the full viewer.

    Everything here is model output derived from untrusted prose, so every interpolation
    goes through ``html.escape`` — no ``innerHTML`` of raw report strings.
    """
    run = RUNS.get(run_id)
    if run is None or run.report is None:
        raise HTTPException(404)
    report = run.report
    rows = []
    for threat in report["threats"]:
        level = threat["severity"]["level"]
        elements = " ".join(
            f"<code>{html.escape(ref)}</code>" for ref in threat["affected_element_ids"]
        )
        mitigations = "<br>".join(
            html.escape(m["summary"]) for m in threat["mitigations"]
        )
        rows.append(
            f"<tr><td class='sev-{html.escape(level)}'>{html.escape(level)}</td>"
            f"<td>{html.escape(threat['category'])}</td>"
            f"<td><b>{html.escape(threat['title'])}</b><br>"
            f"{html.escape(threat['description'])}</td>"
            f"<td>{elements}</td><td>{mitigations}</td>"
            f"<td>{html.escape(threat['verdict']['status'])}</td></tr>"
        )
    summary = report["summary"]
    return HTMLResponse(f"""
    <p class="muted">{html.escape(report["disclaimer"])}</p>
    <p><b>{summary["threat_count"]}</b> threats ·
      {summary["needs_info_count"]} needs-info ·
      {summary["rejected_count"]} rejected ·
      {summary["elements_analyzed"]} elements analysed</p>
    <table>
      <thead><tr><th>Severity</th><th>Category</th><th>Threat</th><th>Elements</th>
        <th>Mitigations</th><th>Verdict</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
    <p class="row">
      <a href="/download/{html.escape(run_id)}">Download report JSON</a>
      <span class="muted">— the full shape (system model, boundary crossings,
        provenance) is in the JSON and in docs/example-report.html, not here.</span>
    </p>
    """)


@app.get("/download/{run_id}")
async def download(run_id: str) -> JSONResponse:
    run = RUNS.get(run_id)
    if run is None or run.report is None:
        raise HTTPException(404)
    return JSONResponse(run.report)


if __name__ == "__main__":
    print(f"PROTOTYPE — variants: {', '.join(VARIANTS)}")
    print("open http://127.0.0.1:8471/?variant=A")
    # Loopback only, per #28 decision 5.
    uvicorn.run(app, host="127.0.0.1", port=8471, log_level="warning")
