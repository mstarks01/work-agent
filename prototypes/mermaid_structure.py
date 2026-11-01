"""PROTOTYPE, throwaway: call Mermaid's own parsers from Python, and measure them.

Question: can Mermaid's parsers give a complete, cited structure for any
flowchart or architecture-beta diagram, from a subprocess the service calls?

Setup: (cd prototypes && npm install)
Run:   ANALYSIS_OFFLINE=1 uv run --no-sync python prototypes/mermaid_structure.py
"""

import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
# Mermaid keeps an entity code as this placeholder in a label it parsed.
PLACEHOLDER = re.compile(r"ﬂ°°(\d+)¶ß")
ICON = {
    "external_entities": "internet",
    "processes": "server",
    "data_stores": "database",
}


def read_structure(text: str) -> dict:
    """The diagram's structure, or {"error": ...}; never more than 20 s or 256 MB."""
    done = subprocess.run(
        ["node", "--max-old-space-size=256", str(HERE / "mermaid_structure.mjs")],
        input=text,
        capture_output=True,
        text=True,
        timeout=20,
        cwd=HERE,
        check=False,
    )
    found = json.loads(done.stdout.strip().splitlines()[-1])
    for part in ("groups", "nodes", "edges"):
        for item in found.get(part, []):
            item["label"] = PLACEHOLDER.sub(
                lambda m: chr(int(m[1])), item["label"] or ""
            )
    return found


def architecture_of(model: dict) -> str:
    """The blessed model as an architecture-beta diagram: zones as groups, a
    service for each element with an icon for its type, an edge for each flow."""
    out, ids = ["architecture-beta"], {}
    for at, zone in enumerate(model["trust_boundaries"]):
        out.append(f"    group z{at}(cloud)[{zone['name']}]")
        ids[zone["id"]] = f"z{at}"
    count = 0
    for group, icon in ICON.items():
        for element in model[group]:
            ids[element["id"]] = f"n{count}"
            place = (
                f" in {ids[element['trust_zone']]}"
                if element["trust_zone"] in ids
                else ""
            )
            out.append(f"    service n{count}({icon})[{element['name']}]{place}")
            count += 1
    for flow in model["data_flows"]:
        out.append(f"    {ids[flow['source']]}:R --> L:{ids[flow['destination']]}")
    return "\n".join(out) + "\n"


def compare(found: dict, model: dict, labelled_edges: bool) -> Counter:
    """How far the parsed structure is from the blessed model, by name."""
    names = {e["id"]: e["name"] for g in ICON for e in model[g]}
    zones = {z["id"]: z["name"] for z in model["trust_boundaries"]}
    by_id = {n["id"]: n for n in found["nodes"]}
    group_label = {
        g["id"]: g["label"].removeprefix("Trust zone: ") for g in found["groups"]
    }
    out = Counter()
    want_nodes = Counter(
        (names[e["id"]], zones.get(e["trust_zone"])) for g in ICON for e in model[g]
    )
    got_nodes = Counter(
        (n["label"], group_label.get(n["group"])) for n in found["nodes"]
    )
    out["nodes missing or misplaced"] = sum((want_nodes - got_nodes).values())
    out["nodes extra"] = sum((got_nodes - want_nodes).values())

    def flow_key(source: str, destination: str, label: str) -> tuple:
        return (source, destination, label if labelled_edges else "")

    want_edges = Counter(
        flow_key(
            names[f["source"]],
            names[f["destination"]],
            f["name"]
            if f["protocol"] in ("", "unknown")
            else f"{f['name']} ({f['protocol']})",
        )
        for f in model["data_flows"]
    )
    got_edges = Counter(
        flow_key(by_id[e["from"]]["label"], by_id[e["to"]]["label"], e["label"])
        for e in found["edges"]
    )
    out["edges missing"] = sum((want_edges - got_edges).values())
    out["edges extra"] = sum((got_edges - want_edges).values())
    out["parts without a citation"] = sum(
        1
        for part in ("groups", "nodes", "edges")
        for item in found[part]
        if not item["cite"]
    )
    return out


def main() -> None:
    cases = sorted(p.stem for p in Path("evals/diagrams").glob("*.mmd"))
    seconds = []
    for title, read, labelled in (
        (
            "Exported flowcharts",
            lambda c: Path(f"evals/diagrams/{c}.mmd").read_text(),
            True,
        ),
        (
            "architecture-beta from the blessed models",
            lambda c: architecture_of(
                json.loads(Path(f"evals/corpus/{c}/model.json").read_text())
            ),
            False,
        ),
    ):
        total = Counter()
        for case in cases:
            start = time.monotonic()
            found = read_structure(read(case))
            seconds.append(time.monotonic() - start)
            if "error" in found:
                total["parse errors"] += 1
                print(f"    {case}: {found['error']}")
                continue
            total += compare(
                found,
                json.loads(Path(f"evals/corpus/{case}/model.json").read_text()),
                labelled,
            )
        print(f"{title}: {dict(total) or 'exact on all 16'}")

    total, shapes = Counter(), Counter()
    for case in cases:
        text = Path(f"evals/experiments/QA-2026-10-31-04/drawn/{case}.mmd").read_text()
        found = read_structure(text)
        if "error" in found:
            total["parse errors"] += 1
            continue
        total["nodes"] += len(found["nodes"])
        total["edges"] += len(found["edges"])
        total["groups"] += len(found["groups"])
        total["parts without a citation"] += sum(
            1 for p in ("groups", "nodes", "edges") for i in found[p] if not i["cite"]
        )
        shapes.update(n["shape"] for n in found["nodes"])
    print(f"Drawn flowcharts: {dict(total)}; shapes as drawn: {dict(shapes)}")

    print("Inputs the reader must refuse or survive:")
    for name, text in {
        "syntax error": "flowchart LR\n  a[web --> b(\n",
        "a sequence diagram": "sequenceDiagram\n  Alice->>Bob: hi\n",
        "an init directive and a click": "%%{init: {'securityLevel': 'loose'}}%%\nflowchart LR\n  a[A] --> b[B]\n  click a call alert(1)\n",
        "markup in a label": 'flowchart LR\n  a["<img src=x onerror=alert(1)>"] --> b[B]\n',
        "a deep chain": "flowchart LR\n  "
        + " --> ".join(f"n{i}" for i in range(2000))
        + "\n",
    }.items():
        start = time.monotonic()
        found = read_structure(text)
        took = time.monotonic() - start
        summary = (
            found.get("error")
            or f"{len(found['nodes'])} nodes, {len(found['edges'])} edges, labels {[n['label'] for n in found['nodes'][:2]]}"
        )
        print(f"    {name}: {summary[:120]} ({took:.1f} s)")
    print(f"Median call: {sorted(seconds)[len(seconds) // 2]:.2f} s")


if __name__ == "__main__":
    main()
