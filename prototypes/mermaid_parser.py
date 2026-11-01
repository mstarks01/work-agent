"""PROTOTYPE, throwaway: read the Mermaid that model_mermaid writes, with no model.

Question: can code read the export's diagrams exactly, and what does a parsed
model lack that extraction must still fill?

Run: ANALYSIS_OFFLINE=1 uv run --no-sync python prototypes/mermaid_parser.py
It parses each evals/diagrams/<case>.mmd, compares the result with the case's
blessed model.json, runs the validity gate on it, and then tries the drawn set.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from analysis_service.system_model import UNKNOWN, make_element_id, make_flow_id
from analysis_service.validation import parse_and_validate

LABEL = "System diagram"
# Placeholders for the two fields the schema gives no `unknown`.
GUESSED_ENTITY_KIND = "external-system"
GUESSED_BOUNDARY_KIND = "other"

SUBGRAPH = re.compile(r'^\s*subgraph (z\d+)\["Trust zone: (.*)"\]\s*$')
NODE = re.compile(r'^\s*(n\d+)(\[\("|\("|\[")(.*?)("\)\]|"\)|"\])\s*$')
EDGE = re.compile(r'^\s*(n\d+) -->\|"(.*)"\| (n\d+)\s*$')
SKIP = re.compile(r"^\s*(flowchart LR|end|linkStyle .*|%% Shapes: .*)?\s*$")
KIND = {'[("': "data_stores", '("': "processes", '["': "external_entities"}
PREFIX = {"data_stores": "store", "processes": "process", "external_entities": "entity"}
ENTITY = re.compile(r"#(\d+);")


def decode(text: str) -> str:
    return ENTITY.sub(lambda m: chr(int(m[1])), text)


def parse(text: str) -> tuple[dict, list[str], Counter]:
    """The diagram as raw model JSON, the lines it refused, and its guesses."""
    model = {
        k: []
        for k in (
            "external_entities",
            "processes",
            "data_stores",
            "data_flows",
            "trust_boundaries",
            "assumptions",
        )
    }
    ids, refused, guessed, zone = {}, [], Counter(), UNKNOWN
    for line in text.splitlines():
        excerpt = line.strip()
        if m := SUBGRAPH.match(line):
            name = decode(m[2])
            zone = make_element_id("boundary", name)
            guessed["boundary.kind"] += 1
            model["trust_boundaries"].append(
                {
                    "id": zone,
                    "name": name,
                    "kind": GUESSED_BOUNDARY_KIND,
                    "source_excerpt": excerpt,
                    "source_label": LABEL,
                }
            )
        elif line.strip() == "end":
            zone = UNKNOWN
        elif m := NODE.match(line):
            group, name = KIND[m[2]], decode(m[3])
            element = {
                "id": make_element_id(PREFIX[group], name),
                "name": name,
                "source_excerpt": excerpt,
                "source_label": LABEL,
                "trust_zone": zone,
            }
            if group == "external_entities":
                element["kind"] = GUESSED_ENTITY_KIND
                guessed["entity.kind"] += 1
            elif group == "processes":
                element |= {
                    "technology": UNKNOWN,
                    "exposure": UNKNOWN,
                    "interface_kind": UNKNOWN,
                }
            else:
                element |= {
                    "technology": UNKNOWN,
                    "data_classification": UNKNOWN,
                    "encryption_at_rest": UNKNOWN,
                }
            ids[m[1]] = element["id"]
            model[group].append(element)
        elif m := EDGE.match(line):
            label = decode(m[2])
            # The export writes "name (protocol)" only where a protocol is stated,
            # so a trailing parenthesis is ambiguous: it is read as the protocol.
            name, protocol = label, UNKNOWN
            if p := re.fullmatch(r"(.*) \((.*)\)", label):
                name, protocol = p[1], p[2]
            source, destination = ids[m[1]], ids[m[3]]
            model["data_flows"].append(
                {
                    "id": make_flow_id(source, destination, name),
                    "name": name,
                    "source": source,
                    "destination": destination,
                    "protocol": protocol,
                    "authentication": UNKNOWN,
                    "data_description": UNKNOWN,
                    "encryption_in_transit": UNKNOWN,
                    "source_excerpt": excerpt,
                    "source_label": LABEL,
                }
            )
        elif not SKIP.match(line):
            refused.append(excerpt)
    return model, refused, guessed


def compare(parsed: dict, blessed: dict) -> dict:
    """Which blessed elements the parse holds by ID, and which facts differ."""
    out = {"missing": [], "extra": [], "differs": Counter()}
    for group in (
        "external_entities",
        "processes",
        "data_stores",
        "data_flows",
        "trust_boundaries",
    ):
        mine = {e["id"]: e for e in parsed[group]}
        theirs = {e["id"]: e for e in blessed[group]}
        out["missing"] += sorted(set(theirs) - set(mine))
        out["extra"] += sorted(set(mine) - set(theirs))
        for key in set(mine) & set(theirs):
            for field, value in theirs[key].items():
                if field in (
                    "description",
                    "source_excerpt",
                    "source_label",
                    "notes",
                    "source_speaker",
                    "assets",
                    "id",
                ):
                    continue
                if mine[key].get(field) != value:
                    out["differs"][
                        f"{PREFIX.get(group, group.rstrip('s'))}.{field}"
                    ] += 1
    return out


def main() -> None:
    totals, guessed_all, gate = Counter(), Counter(), Counter()
    for path in sorted(Path("evals/diagrams").glob("*.mmd")):
        text = path.read_text()
        parsed, refused, guessed = parse(text)
        blessed = json.loads(Path(f"evals/corpus/{path.stem}/model.json").read_text())
        diff = compare(parsed, blessed)
        _, issues = parse_and_validate(parsed, sources={LABEL: text})
        n = sum(
            len(blessed[g])
            for g in (
                "external_entities",
                "processes",
                "data_stores",
                "data_flows",
                "trust_boundaries",
            )
        )
        totals["elements"] += n
        totals["missing"] += len(diff["missing"])
        totals["extra"] += len(diff["extra"])
        totals["refused lines"] += len(refused)
        totals.update({f"differs {k}": v for k, v in diff["differs"].items()})
        guessed_all.update(guessed)
        gate.update(issue.code for issue in issues)
        print(
            f"{path.stem:34} elements {n:3}  missing {len(diff['missing'])}  extra {len(diff['extra'])}  refused lines {len(refused)}  gate issues {len(issues)}"
        )
        for key in diff["missing"] + diff["extra"]:
            print(f"    id mismatch: {key}")
    print("\nTotals:", dict(totals))
    print("Guessed (no `unknown` in the schema):", dict(guessed_all))
    print("Gate issue codes:", dict(gate))

    print("\nDrawn set (loose drawings):")
    for path in sorted(Path("evals/experiments/QA-2026-10-31-04/drawn").glob("*.mmd")):
        _, refused, _ = parse(path.read_text())
        print(
            f"    {path.stem:34} refused lines {len(refused)} of {len(path.read_text().splitlines())}"
        )


if __name__ == "__main__":
    sys.exit(main())
