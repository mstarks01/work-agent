"""Hold each matched STRIDE must-find of the Baselines to a mechanism proxy (E8).

A must-find matches a draft by verb and place. This script asks whether any
field the code reads tells the reference's mechanism from the draft's.

Readers: ``runs_from_reports`` reads each Baseline under ``evals/baselines``.
``score_case`` with ``SubsetVerbIdentity`` assigns the matches, as ``run.py
score`` builds it. ``scorer._match_in_lane`` lists every same-lane draft that
the rule calls equivalent to a reference, not only the assigned one. Each
Baseline is read twice: the report's threats, after the critic, and
``drafts.json``, before it.

The proxies:

* A draft's proxy is the attribute that each attribute ground cites,
  ``crossing`` for a derived-fact ground, and each assertion predicate.
* Reference proxy A is the attributes that ``WORDS`` finds in the claim and
  the notes. A reference has no ground field, so this reads prose.
* Reference proxy B is the controls that the blessed model leaves unknown or
  states absent on the reference's elements: the controls that can fail at
  the place, not the one the reference rests on.
* Lead C is a reference text with "compromis" where no matching draft's
  title or description has it. A word list raises a lead and rules out none.

A place is one Baseline and one matched must-find. Proxy A or B "differs" at a
place where the reference's proxy is not empty and no matching draft shares
it. The script writes each place to ``runs/mechanism-rows.json`` and the
counts to ``runs/mechanism-counts.json``.

Usage, from the repository root: ``mechanism_proxy.py``.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from analysis_service.claims import ATTRIBUTE_GROUNDS
from evals.harness.artifact import load_artifact
from evals.harness.bundle import runs_from_reports, stride_threats
from evals.harness.identity import SubsetVerbIdentity
from evals.harness.ledger import Ledger
from evals.harness.reference import development_cases, flows_by_case, load_corpus
from evals.harness.scorer import _match_in_lane, score_case

RUNS = Path(__file__).parent / "runs"
#: The control attributes of a model element that proxy B reads.
CONTROLS = (
    "authentication",
    "encryption_in_transit",
    "encryption_at_rest",
    "protocol",
    "data_classification",
)
#: The words that proxy A reads as a reference's control.
WORDS = {
    "authentication": r"authenticat|\bmtls\b|mutual tls",
    "encryption_in_transit": (
        r"in.transit|transport|\btls\b|https|on the wire|plaintext|cleartext"
    ),
    "encryption_at_rest": r"at.rest|storage media|backup|snapshot|\bdisk",
    "protocol": r"\bprotocol",
    "data_classification": r"classif",
}
COMPROMISE = re.compile(r"compromis", re.IGNORECASE)


def draft_proxy(draft) -> set[str]:
    marks = set()
    for ground in draft.grounds:
        if ground.kind in ATTRIBUTE_GROUNDS:
            marks.add(ground.attribute)
        elif ground.kind == "derived-fact":
            marks.add("crossing")
        elif ground.kind in ("assertion", "unknown-assertion"):
            marks.add(f"assertion:{ground.assertion}")
    return marks


def text_proxy(reference) -> set[str]:
    text = f"{reference.claim} {reference.notes}".lower()
    return {name for name, pattern in WORDS.items() if re.search(pattern, text)}


def model_proxy(case, reference) -> set[str]:
    marks = set()
    for element_id in reference.affected_element_ids:
        element = case.model.get(element_id)
        for name in CONTROLS:
            value = str(getattr(element, name, "") or "")
            if value == "unknown" or value.startswith("none"):
                marks.add(name)
    return marks


def places(artifact_path: Path, corpus) -> list[dict]:
    """Each matched must-find of one Baseline, at both stages, with its proxies."""
    artifact = load_artifact(artifact_path)
    cases = [case for case in corpus if case.id in artifact.cases]
    runs = runs_from_reports(artifact, cases)
    rows = []
    for case in cases:
        run = runs.get(case.id)
        if run is None:
            continue
        references = case.stride_claims()
        stages = {
            "report": stride_threats(run.report),
            "drafts": list(run.drafts.get("stride", ())),
        }
        for stage, produced in stages.items():
            matcher = SubsetVerbIdentity(
                flows_by_case([case], {case.id: run.report.system_model})
            )
            score = score_case(case, produced, matcher, Ledger())
            own, ruled = _match_in_lane(case, produced, matcher, [])
            for pair in score.matched:
                index = pair.reference_index
                reference = references[index]
                if not reference.must_find:
                    continue
                text = f"{reference.claim} {reference.notes}"
                rows.append(
                    {
                        "sweep": artifact_path.parent.name.split("-")[0],
                        "stage": stage,
                        "case": case.id[:2],
                        "ref": index,
                        "lane": reference.category,
                        "ref_text": sorted(text_proxy(reference)),
                        "ref_model": sorted(model_proxy(case, reference)),
                        "ref_compromise": bool(COMPROMISE.search(text)),
                        "drafts": [
                            {
                                "title": draft.title,
                                "proxy": sorted(draft_proxy(draft)),
                                "compromise": bool(
                                    COMPROMISE.search(
                                        f"{draft.title} {draft.description}"
                                    )
                                ),
                            }
                            for draft in (
                                produced[p] for p in own[index] + ruled[index]
                            )
                        ],
                    }
                )
    return rows


def verdict(row: dict, key: str) -> str:
    wanted = set(row[key])
    if not wanted:
        return "no-ref-control"
    if any(wanted & set(draft["proxy"]) for draft in row["drafts"]):
        return "shares"
    return "differs"


def counts(rows: list[dict]) -> dict:
    """The counts of each stage: places, references, each proxy and lead C."""
    result = {}
    for stage in ("report", "drafts"):
        staged = [row for row in rows if row["stage"] == stage]
        flagged: dict[str, dict[str, int]] = {
            "A": defaultdict(int),
            "C": defaultdict(int),
        }
        for row in staged:
            place = f"{row['case']}/{row['ref']} {row['lane']}"
            if verdict(row, "ref_text") == "differs":
                flagged["A"][place] += 1
            if row["ref_compromise"] and not any(
                d["compromise"] for d in row["drafts"]
            ):
                flagged["C"][place] += 1
        result[stage] = {
            "places": len(staged),
            "references": len({(row["case"], row["ref"]) for row in staged}),
            "A": dict(Counter(verdict(row, "ref_text") for row in staged)),
            "B": dict(Counter(verdict(row, "ref_model") for row in staged)),
            "C": sum(flagged["C"].values()),
            "C_of": sum(row["ref_compromise"] for row in staged),
            "A_flagged_sweeps": dict(sorted(flagged["A"].items())),
            "C_flagged_sweeps": dict(sorted(flagged["C"].items())),
        }
    return result


def main() -> None:
    corpus = development_cases(load_corpus(Path("evals/corpus")))
    rows = [
        row
        for artifact_path in sorted(Path("evals/baselines").glob("*/mstarks01-*.json"))
        for row in places(artifact_path, corpus)
    ]
    (RUNS / "mechanism-rows.json").write_text(json.dumps(rows, indent=1) + "\n")
    result = json.dumps(counts(rows), indent=1)
    (RUNS / "mechanism-counts.json").write_text(result + "\n")
    print(result)


if __name__ == "__main__":
    main()
