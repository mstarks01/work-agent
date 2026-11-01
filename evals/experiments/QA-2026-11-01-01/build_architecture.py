"""The ``architecture-beta`` diagrams of QA-2026-11-01-01: one for each case a
diagnosis may read, from its blessed ``model.json``. A trust zone is a group
titled ``Trust zone <name>``, an element a service with Mermaid's own icon for
its kind, and a flow an edge with an arrow. Mermaid's architecture grammar
gives an edge no label, so a flow's name is not in the diagram. A title keeps
the name as the model spells it, so Mermaid refuses a diagram with a name it
cannot parse, as it does for a person. Run it as
``python build_architecture.py <out dir>``."""

import sys
from pathlib import Path

from analysis_service.system_model import SystemModel
from evals.harness.reference import development_cases, load_corpus

ICON = {
    "external_entities": "internet",
    "processes": "server",
    "data_stores": "database",
}


def architecture(model: SystemModel) -> str:
    """``model`` as ``architecture-beta`` text."""
    lines, ids = ["architecture-beta"], {}
    for at, zone in enumerate(model.trust_boundaries):
        ids[zone.id] = f"z{at}"
        lines.append(f"    group z{at}(cloud)[Trust zone {zone.name}]")
    count = 0
    for kind, icon in ICON.items():
        for element in getattr(model, kind):
            ids[element.id] = f"n{count}"
            place = (
                f" in {ids[element.trust_zone]}" if element.trust_zone in ids else ""
            )
            lines.append(f"    service n{count}({icon})[{element.name}]{place}")
            count += 1
    lines.extend(
        f"    {ids[flow.source]}:R --> L:{ids[flow.destination]}"
        for flow in model.data_flows
    )
    return "\n".join(lines) + "\n"


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for case in development_cases(load_corpus(Path("evals/corpus"))):
        (out / f"{case.meta.id}.mmd").write_text(
            architecture(case.model), encoding="utf-8"
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
