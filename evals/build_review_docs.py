"""Generate the step-6 reading document for every case with no clearing sitting.

``BLESSING.md`` step 6 is one sitting: a person reads a case's sources, its
blessed model and every declared framework's reference set together, and asks
whether the sets describe what could actually go wrong with this system. No
generator can do the sitting — that is the point of it — but a generator can
put everything the reader needs in one file, in the order the method requires,
so a sitting is pure reading time.

The template is ``corpus/01-payments-checkout/REVIEW-02.md``, the first such
document and the record of what a sitting needs: the sources verbatim, the
model as tables, the reader's own list *before* the recorded sets, one mark
per record, and the exact ``reviews`` entry to paste at the end. Case 01 keeps
its hand-written document; this writes ``REVIEW.md`` for every other case whose
``case.json`` records no sitting, and refreshes it as reference sets
change — the document is derived, so editing it by hand is editing the wrong
file.

Run: ``python evals/build_review_docs.py``. Offline, no dependencies beyond
the repository.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from stride_service.frameworks import PACKAGES

EVALS = Path(__file__).resolve().parent
CORPUS = EVALS / "corpus"

#: Case 01's sitting document predates this generator and is the template it
#: was derived from; regenerating it would overwrite the historical record.
HAND_WRITTEN = frozenset({"01-payments-checkout"})

PREAMBLE = """\
**What you are checking.** Not whether two write-ups are the same threat — the
identity rule decides that mechanically. This asks the question underneath:
**do these reference sets describe what could actually go wrong with this
system?** If a set misses a whole class of attack, the tool scores full marks
for missing it too, and nothing in the repo would ever say so.

## The one rule

**Read Part 1 and write your own list before you open Part 2.** If you read
the recorded threats first you will find them reasonable, and the sitting
measures nothing. Your list does not have to be good or complete — it only has
to be yours, written first.

Roughly an hour.
"""

OWN_LIST = """\
### Your list

Write what could go wrong. Anything: an attack, a missing control, a question
the text does not answer. Bullet points, in any order, no need to sort by
category.

```
-
-
-
```
"""

MARK_GUIDANCE = """\
For each, mark one of:

- `agree` — a real finding against this system, worth reporting.
- `doubt` — overstated, unsupported by the text, or not really a finding here.
- `dup` — the same finding as another entry on this list, by number.

Then, at the end of the last part, note anything on **your** list that is not
on either of them. That is the finding this sitting exists for.
"""

MISSING = """\
---

## What was on your list and not on either of theirs

The point of the sitting. One line each, and say which set you expected it in.

```
-
-
```
"""


def load_meta(path: Path) -> dict:
    """One case.json, typed as the mapping it always is."""
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(meta, dict), path
    return meta


def load_records(path: Path) -> list[dict]:
    """One claims file or model.json's shape: a JSON object or array."""
    records = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(records, list), path
    return records


def quoted(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.splitlines())


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(out)


def model_tables(model: dict) -> str:
    parts = []
    if model.get("external_entities"):
        parts.append("**External entities**\n")
        parts.append(
            table(
                ["id", "kind", "zone"],
                [
                    [e["id"], e["kind"], e["trust_zone"]]
                    for e in model["external_entities"]
                ],
            )
        )
    if model.get("processes"):
        parts.append("\n**Processes**\n")
        parts.append(
            table(
                ["id", "exposure", "interface", "zone", "technology"],
                [
                    [
                        p["id"],
                        p["exposure"],
                        p["interface_kind"],
                        p["trust_zone"],
                        p["technology"],
                    ]
                    for p in model["processes"]
                ],
            )
        )
    if model.get("data_stores"):
        parts.append("\n**Data stores**\n")
        parts.append(
            table(
                ["id", "zone", "at rest", "classification"],
                [
                    [
                        s["id"],
                        s["trust_zone"],
                        s["encryption_at_rest"],
                        s["data_classification"],
                    ]
                    for s in model["data_stores"]
                ],
            )
        )
    if model.get("data_flows"):
        parts.append("\n**Data flows**\n")
        parts.append(
            table(
                [
                    "id",
                    "source",
                    "destination",
                    "protocol",
                    "authentication",
                    "in transit",
                ],
                [
                    [
                        f["id"],
                        f["source"],
                        f["destination"],
                        f["protocol"],
                        f["authentication"],
                        f["encryption_in_transit"],
                    ]
                    for f in model["data_flows"]
                ],
            )
        )
    if model.get("trust_boundaries"):
        parts.append("\n**Trust boundaries**\n")
        parts.append(
            table(
                ["id", "kind"],
                [[b["id"], b["kind"]] for b in model["trust_boundaries"]],
            )
        )

    noted = [
        (el["id"], el["notes"])
        for group in (
            "external_entities",
            "processes",
            "data_stores",
            "data_flows",
            "trust_boundaries",
        )
        for el in model.get(group, [])
        if el.get("notes")
    ]
    if noted:
        parts.append(
            "\n**Recorded notes** — hedges, probed gaps and source disagreements"
            " live here, so read them before the sets.\n"
        )
        for element_id, note in noted:
            parts.append(f"- `{element_id}` — {note}")
    if model.get("assumptions"):
        parts.append("\n**Assumptions**\n")
        for entry in model["assumptions"]:
            parts.append(
                f"- `{entry['element_id']}` — {entry['assumption']} (basis: {entry['basis']})"
            )
    return "\n".join(parts)


def stride_part(claims: list[dict], part: int) -> str:
    lines = [f"## Part {part} — the {len(claims)} recorded STRIDE threats\n"]
    lines.append("Only after your own list exists.\n")
    lines.append(MARK_GUIDANCE)
    current = None
    for number, claim in enumerate(claims, start=1):
        if claim["category"] != current:
            current = claim["category"]
            lines.append(f"\n### {current}\n")
        severity = claim["severity"]
        lines.append(f"**{number}.** {claim['claim']}\n")
        cites = ", ".join(f"`{i}`" for i in claim["affected_element_ids"])
        lines.append(f"- cites: {cites}")
        lines.append(
            f"- tier: {claim['tier']} · severity: {severity['likelihood']}/{severity['impact']}"
            f" · verb: `{claim['verb']}`"
        )
        if claim.get("notes"):
            lines.append(f"- recorded note: {claim['notes']}")
        lines.append("\n> mark:\n")
    return "\n".join(lines)


def asvs_part(records: list[dict], part: int) -> str:
    lines = [f"## Part {part} — the {len(records)} recorded ASVS records\n"]
    lines.append(
        "The narrower question, per record: **does this requirement apply to"
        " this system, and does the input show it satisfied?** An ASVS claim"
        " rules applicability and never a pass.\n"
    )
    current = None
    for number, record in enumerate(records, start=1):
        if record["chapter"] != current:
            current = record["chapter"]
            lines.append(f"\n### {current}\n")
        lines.append(f"**A{number}.** `{record['requirement']}` — {record['claim']}\n")
        cites = ", ".join(f"`{i}`" for i in record["affected_element_ids"])
        lines.append(f"- cites: {cites}")
        lines.append(f"- tier: {record['tier']}")
        if record.get("notes"):
            lines.append(f"- recorded note: {record['notes']}")
        lines.append("\n> mark:\n")
    return "\n".join(lines)


#: One renderer per framework, keyed rather than branched: a package missing
#: from this table raises `KeyError` at its first case, and the check below
#: keeps the table complete against the registry — a table nobody compares to
#: `PACKAGES` fails as quietly as the branch it replaces. Each renderer asks
#: the question that package's records rule on: STRIDE's is a threat, ASVS's
#: is applicability.
RENDERERS: dict[str, Callable[[list[dict], int], str]] = {
    "stride": stride_part,
    "asvs": asvs_part,
}

_missing = set(PACKAGES) - set(RENDERERS)
if _missing:
    raise SystemExit(
        f"no reading-document renderer for {sorted(_missing)}; add a row to"
        " RENDERERS so that package's reference set reaches a sitting"
    )


def closing(case_id: str, meta: dict) -> str:
    case_dir = CORPUS / case_id
    read = ["source.md"]
    read += [source["file"] for source in meta["sources"] if source["file"] not in read]
    read.append("model.json")
    read += [f"claims/{fw['name']}.json" for fw in meta["frameworks"]]
    read_json = ",\n        ".join(
        json.dumps(
            {
                "file": name,
                "sha256": hashlib.sha256((case_dir / name).read_bytes()).hexdigest(),
            }
        )
        for name in read
    )
    return f"""\
---

## What to do with the result

**Counts first**, kept apart per framework: how many `agree`, `doubt`, `dup`
per part, and how many of your own items are missing from either set.

- **Few doubts, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several doubts** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sitting.** Save this filled document as
`REVIEW-<your GitHub login>.md` beside the original — the filled copy is the
evidence, and the generated `REVIEW.md` stays derived and unfilled. Append
this entry to `reviews` in `evals/corpus/{case_id}/case.json`, which is what
`tests/test_case_review.py` reads:

```json
  "reviews": [
    {{
      "reviewer": "<your GitHub login>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {read_json}
      ],
      "document": "REVIEW-<your GitHub login>.md",
      "notes": "<counts, and anything you changed>"
    }}
  ],
```

The digests above are the files as they were when this document was
generated. If the sitting changed a file — a claim edit is a normal outcome —
recompute that file's digest (`sha256sum <file>`) before you commit: the
entry signs the bytes that merge.

If this case is named in `UNREVIEWED` in `tests/test_case_review.py`, delete
its line. That list names the cases nobody has read, so it is only accurate
while a reviewed case comes off it. A case not named there is new, and merges
with this entry from the start.

`tests/test_case_review.py` checks that `read` covers every framework the
case declares, that every digest matches, that the `document` file exists,
and that the reviewer has a line in `evals/review/voters.toml` — a first-time
contributor adds their own, standing `contributor`. Then
`python -m evals.harness.run submit sitting` opens the PR.
"""


def part_one(case_dir: Path) -> str:
    """The system as the reader meets it: every source, then the model.

    Split out so a surface can show this and withhold the recorded sets until
    the reader's own list is written — the one rule the method has. The
    generated document and `webapp/sitting.py` compose the same text.
    """
    meta = load_meta(case_dir / "case.json")
    model = load_meta(case_dir / "model.json")
    lines = ["## Part 1 — the system\n"]
    for source in meta["sources"]:
        text = (case_dir / source["file"]).read_text(encoding="utf-8")
        lines.append(f"### {source['label']} ({source['kind']})\n")
        lines.append("Exactly what the service would receive.\n")
        lines.append(quoted(text))
        lines.append("")
    lines.append("### What the model says is in it\n")
    lines.append(
        "Not part of the question, but the records cite these names, so you"
        " need them.\n"
    )
    lines.append(model_tables(model))
    return "\n".join(lines)


def parts_after(case_dir: Path) -> dict[str, str]:
    """The recorded set per declared framework, rendered through RENDERERS.

    Keyed by framework rather than concatenated, so a caller can show one at a
    time and so a package that joins `PACKAGES` arrives here through its own
    renderer entry with no edit.
    """
    meta = load_meta(case_dir / "case.json")
    rendered = {}
    for part, declared in enumerate(meta["frameworks"], start=2):
        name = declared["name"]
        claims = load_records(case_dir / "claims" / f"{name}.json")
        rendered[name] = RENDERERS[name](claims, part)
    return rendered


def build_doc(case_dir: Path) -> str:
    meta = load_meta(case_dir / "case.json")

    lines = [
        f"# Review sitting — is `{meta['id']}`'s reference list right?\n",
        f"`evals/BLESSING.md` step 6, over `evals/corpus/{meta['id']}`.",
        f"\n**{meta['title']}** — domain `{meta['domain']}`.\n",
        PREAMBLE,
        "---\n",
        part_one(case_dir),
        "",
        OWN_LIST,
        "---\n",
    ]
    lines += list(parts_after(case_dir).values())
    lines.append(MISSING)
    lines.append(closing(meta["id"], meta))
    return "\n".join(lines)


def main() -> int:
    written = 0
    for case_dir in sorted(CORPUS.iterdir()):
        if not case_dir.is_dir() or case_dir.name in HAND_WRITTEN:
            continue
        meta = load_meta(case_dir / "case.json")
        if meta.get("reviews"):
            continue
        (case_dir / "REVIEW.md").write_text(build_doc(case_dir), encoding="utf-8")
        written += 1
        print(f"wrote {case_dir.name}/REVIEW.md")
    print(f"{written} reading document(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
