"""Which must-find leads an extracted model loses, against the signed model.

For each sweep and each diagnosable case, the shipped STRIDE rules run on the
signed model and on the extracted one. A must-find a rule leads to on the
signed model is "kept" when a rule also leads to it on the extracted model,
read through the replay's own alignment. Otherwise it is lost, either because
an element it names has no produced counterpart ("unaligned") or because every
element aligns and no rule fires there ("unfired": a fact on the extracted
model differs).
"""

import json
import sys
from collections import Counter
from pathlib import Path

from evals.harness.alignment import align
from evals.harness.reference import development_cases, load_corpus
from evals.harness.run import extractions_from_reports, load_artifact
from evals.harness.triggers import _hit

FRAMEWORK = "stride"


def lead_fates(case, extracted):
    produced_of = align(case, extracted).produced_of
    for index, reference in enumerate(case.references.get(FRAMEWORK) or ()):
        if not reference.must_find:
            continue
        if not _hit(FRAMEWORK, reference, case.model).rule_ids:
            yield index, reference, "unled-on-signed"
            continue
        mapped = [
            produced_of.get(element) for element in reference.affected_element_ids
        ]
        moved = reference.model_copy(
            update={"affected_element_ids": tuple(m for m in mapped if m)}
        )
        if _hit(FRAMEWORK, moved, extracted).rule_ids:
            yield index, reference, "kept"
        elif None in mapped:
            yield index, reference, "unaligned"
        else:
            yield index, reference, "unfired"


def main(corpus_dir, artifacts):
    cases = development_cases(load_corpus(Path(corpus_dir)))
    totals = Counter()
    rows = []
    for path in artifacts:
        loaded = load_artifact(Path(path))
        present = [case for case in cases if case.id in loaded.cases]
        extracted = extractions_from_reports(loaded, present)
        for case in present:
            result = extracted.get(case.id)
            if result is None or result.extracted is None:
                totals["no-model"] += 1
                continue
            for index, reference, fate in lead_fates(case, result.extracted):
                totals[fate] += 1
                if fate in ("unaligned", "unfired"):
                    rows.append(
                        {
                            "sweep": Path(path).name,
                            "case": case.id,
                            "reference": index,
                            "lane": reference.lane,
                            "fate": fate,
                        }
                    )
    print(json.dumps({"totals": totals, "lost": rows}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
