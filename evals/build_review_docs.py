"""Generate the step-6 reading document for every case with no clearing sitting.

``BLESSING.md`` step 6 is one sitting. A person reads a case's sources, its
blessed model and every declared framework's reference set together, and asks
whether the sets describe what could actually go wrong with this system. No
generator can do the sitting, and that is the point of it. A generator can put
everything the reader needs in one file, in the order the method requires, so a
sitting is pure reading time.

The template is ``corpus/01-payments-checkout/REVIEW-02.md``, the first such
document and the record of what a sitting needs: the sources verbatim, the model
as tables, the reader's own list before the recorded sets, one mark per record,
and the exact ``reviews`` entry to paste at the end. Case 01 keeps its
hand-written document. This writes ``REVIEW.md`` for every other case, and
refreshes it as reference sets change. The document is derived, so editing it by
hand is editing the wrong file.

A case that records a sitting keeps a current document. A sitting does not
retire a case: a second reader may sit the same one, and a change to any file a
sitting read puts that case back on the unreviewed list. A document this skipped
would go stale exactly while somebody needed it, and nothing would pin it.
``tests/test_corpus_lints.py`` holds every case's document against what this
writes, and a case it skipped would be a case that check could not cover.

Run ``python evals/build_review_docs.py``. It is offline, with no dependencies
beyond the repository.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from analysis_service.frameworks import PACKAGES

EVALS = Path(__file__).resolve().parent
CORPUS = EVALS / "corpus"

#: Case 01's sitting document predates this generator and is the template it
#: was derived from; regenerating it would overwrite the historical record.
HAND_WRITTEN = frozenset({"01-payments-checkout"})

#: What this generator writes beside each case. Named rather than spelled at
#: each use, because ``evals/harness/submit.py`` has to know which file under
#: a case directory is derived and which is somebody's evidence.
GENERATED_DOCUMENT = "REVIEW.md"

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
- `reject` — overstated, unsupported by the text, or not really a finding here.
- `duplicate` — the same finding as another entry on this list, by number.

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


#: One model group per row: the key it lives under in ``model.json``, the
#: caption a reader sees, the column headers, and the cells one element fills.
#: A table rather than five near-identical blocks, so a group added to the
#: model reaches both surfaces through one row. Order is the reading order.
MODEL_TABLES: list[tuple[str, str, list[str], Callable[[dict], list[str]]]] = [
    (
        "external_entities",
        "External entities",
        ["id", "kind", "zone"],
        lambda e: [e["id"], e["kind"], e["trust_zone"]],
    ),
    (
        "processes",
        "Processes",
        ["id", "exposure", "interface", "zone", "technology"],
        lambda p: [
            p["id"],
            p["exposure"],
            p["interface_kind"],
            p["trust_zone"],
            p["technology"],
        ],
    ),
    (
        "data_stores",
        "Data stores",
        ["id", "zone", "at rest", "classification"],
        lambda s: [
            s["id"],
            s["trust_zone"],
            s["encryption_at_rest"],
            s["data_classification"],
        ],
    ),
    (
        "data_flows",
        "Data flows",
        ["id", "source", "destination", "protocol", "authentication", "in transit"],
        lambda f: [
            f["id"],
            f["source"],
            f["destination"],
            f["protocol"],
            f["authentication"],
            f["encryption_in_transit"],
        ],
    ),
    (
        "trust_boundaries",
        "Trust boundaries",
        ["id", "kind"],
        lambda b: [b["id"], b["kind"]],
    ),
]


def model_blocks(model: dict) -> list[dict]:
    """One model, as the blocks a surface lays out.

    Two block kinds carry all of it. ``table`` is a caption over headers and
    rows; ``terms`` is a caption over ``id — sentence`` pairs, which is the
    shape both the recorded notes and the assumptions already have.
    """
    blocks: list[dict] = []
    for key, caption, headers, cells in MODEL_TABLES:
        elements = model.get(key)
        if elements:
            blocks.append(
                {
                    "kind": "table",
                    "caption": caption,
                    "headers": headers,
                    "rows": [cells(element) for element in elements],
                }
            )
    noted = [
        {"term": el["id"], "text": el["notes"]}
        for key, _, _, _ in MODEL_TABLES
        for el in model.get(key, [])
        if el.get("notes")
    ]
    if noted:
        blocks.append(
            {
                "kind": "terms",
                "caption": "Recorded notes",
                "hint": "hedges, probed gaps and source disagreements live here,"
                " so read them before the sets.",
                "items": noted,
            }
        )
    if model.get("assumptions"):
        blocks.append(
            {
                "kind": "terms",
                "caption": "Assumptions",
                "hint": None,
                "items": [
                    {
                        "term": entry["element_id"],
                        "text": f"{entry['assumption']} (basis: {entry['basis']})",
                    }
                    for entry in model["assumptions"]
                ],
            }
        )
    return blocks


def model_markdown(blocks: list[dict]) -> str:
    """The model blocks as the reading document prints them."""
    chunks = []
    for block in blocks:
        head = f"**{block['caption']}**"
        if block.get("hint"):
            head += f" — {block['hint']}"
        if block["kind"] == "table":
            body = table(block["headers"], block["rows"])
        else:
            body = "\n".join(
                f"- `{item['term']}` — {item['text']}" for item in block["items"]
            )
        chunks.append(f"{head}\n\n{body}")
    return "\n\n".join(chunks)


def model_tables(model: dict) -> str:
    """One model, as the reading document prints it."""
    return model_markdown(model_blocks(model))


def _fields(*rows: list[dict] | None) -> list[list[dict]]:
    """The named fields of one record, one printed line per row.

    A row is a list of fields, because the reading document puts a record's
    tier, severity and verb on one line and its citations on another. A field
    names itself, carries its values as values, and says whether they are
    identifiers — so the document can print backticks and a page can print
    code spans, from one description of the same record.
    """
    return [row for row in rows if row]


def _field(label: str, *values: str, code: bool = False) -> dict:
    return {"label": label, "values": list(values), "code": code}


def _note_row(record: dict) -> list[dict] | None:
    if not record.get("notes"):
        return None
    return [_field("recorded note", record["notes"])]


def stride_part(claims: list[dict]) -> dict:
    """STRIDE's part: could this attack happen in this system?"""
    groups: list[dict] = []
    for number, claim in enumerate(claims, start=1):
        if not groups or groups[-1]["name"] != claim["category"]:
            groups.append({"name": claim["category"], "records": []})
        severity = claim["severity"]
        groups[-1]["records"].append(
            {
                "label": str(number),
                "identifier": None,
                "title": claim["claim"],
                "fields": _fields(
                    [_field("cites", *claim["affected_element_ids"], code=True)],
                    [
                        _field("tier", claim["tier"]),
                        _field(
                            "severity",
                            f"{severity['likelihood']}/{severity['impact']}",
                        ),
                        _field("verb", claim["verb"], code=True),
                    ],
                    _note_row(claim),
                ),
            }
        )
    return {
        "framework": "stride",
        "question": "Could this attack happen in this system?",
        "heading": f"the {len(claims)} recorded STRIDE threats",
        "intro": ["Only after your own list exists.\n", MARK_GUIDANCE],
        "groups": groups,
    }


def asvs_part(records: list[dict]) -> dict:
    """ASVS's part: does this requirement apply, and is it shown satisfied?"""
    groups: list[dict] = []
    for number, record in enumerate(records, start=1):
        if not groups or groups[-1]["name"] != record["chapter"]:
            groups.append({"name": record["chapter"], "records": []})
        groups[-1]["records"].append(
            {
                "label": f"A{number}",
                "identifier": record["requirement"],
                "title": record["claim"],
                "fields": _fields(
                    [_field("cites", *record["affected_element_ids"], code=True)],
                    [_field("tier", record["tier"])],
                    _note_row(record),
                ),
            }
        )
    return {
        "framework": "asvs",
        "question": "Does this requirement apply to this system, and does the"
        " input show it satisfied?",
        "heading": f"the {len(records)} recorded ASVS records",
        "intro": [
            (
                "The narrower question, per record: **does this requirement apply"
                " to this system, and does the input show it satisfied?** An ASVS"
                " claim rules applicability and never a pass.\n"
            )
        ],
        "groups": groups,
    }


#: One renderer per framework, keyed rather than branched: a package missing
#: from this table raises `KeyError` at its first case, and the check below
#: keeps the table complete against the registry — a table nobody compares to
#: `PACKAGES` fails as quietly as the branch it replaces. Each renderer asks
#: the question that package's records rule on: STRIDE's is a threat, ASVS's
#: is applicability. Each answers in blocks rather than in text, so the
#: reading document and `webapp/sitting.py` lay out one description of the
#: record and cannot drift into describing two.
RENDERERS: dict[str, Callable[[list[dict]], dict]] = {
    "stride": stride_part,
    "asvs": asvs_part,
}

_missing = set(PACKAGES) - set(RENDERERS)
if _missing:
    raise SystemExit(
        f"no reading-document renderer for {sorted(_missing)}; add a row to"
        " RENDERERS so that package's reference set reaches a sitting"
    )


def _printed(field: dict) -> str:
    values = field["values"]
    if field["code"]:
        values = [f"`{value}`" for value in values]
    return f"{field['label']}: {', '.join(values)}"


def part_markdown(rendered: dict, part: int) -> str:
    """One framework's part as the reading document prints it."""
    lines = [f"## Part {part} — {rendered['heading']}\n", *rendered["intro"]]
    for group in rendered["groups"]:
        lines.append(f"\n### {group['name']}\n")
        for record in group["records"]:
            named = f"`{record['identifier']}` — " if record["identifier"] else ""
            lines.append(f"**{record['label']}.** {named}{record['title']}\n")
            lines += [
                "- " + " · ".join(_printed(field) for field in row)
                for row in record["fields"]
            ]
            lines.append("\n> mark:\n")
    return "\n".join(lines)


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

**Counts first**, kept apart per framework: how many `agree`, `reject`,
`duplicate` per part, and how many of your own items are missing from either set.

- **Few `reject` marks, nothing important missing** — the sets hold, and the numbers
  measured against them have a standard behind them.
- **A whole class of attack missing** — the serious outcome. Recall is measured
  against these sets, so the tool has been scoring full marks for a gap nobody
  could see. Extend the set, and re-derive what was quoted against it.
- **Several `reject` marks** — the sets overstate, inflating the denominator. Cheaper
  direction, still wrong.

**Then record the sitting.** Save this filled document as
`REVIEW-<the submitting GitHub login>.md` beside the original — the filled copy
is the evidence, and the generated `REVIEW.md` stays derived and unfilled.
Append this entry to `reviews` in `evals/corpus/{case_id}/case.json`, which is
what `tests/test_case_review.py` reads:

```json
  "reviews": [
    {{
      "submitted_by": "<the GitHub login opening the PR>",
      "submitted_for": "<who read the case: a login, or the word anonymous>",
      "date": "<YYYY-MM-DD>",
      "read": [
        {read_json}
      ],
      "document": "REVIEW-<the submitting GitHub login>.md",
      "notes": "<counts, and anything you changed>"
    }}
  ],
```

**Two names, because they answer two questions.** `submitted_by` is the account
that opens the pull request and answers for the sitting. `submitted_for` is who
read the case: the same login where you read it yourself, another login, or
`anonymous` where the reader takes part on no name of their own. Only
`submitted_by` needs a roster line, and only `submitted_by` names the document.

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
and that `submitted_by` has a line in `evals/review/voters.toml` — a first-time
contributor adds their own, standing `contributor`. `submitted_for` needs no
roster line, because it grants nothing. Then
`python -m evals.harness.run submit sitting` opens the PR.
"""


def part_one_blocks(case_dir: Path) -> list[dict]:
    """The system as the reader meets it: every source, then the model.

    Split out so a surface can show this and withhold the recorded sets until
    the reader's own list is written — the one rule the method has. The
    generated document and `webapp/sitting.py` lay out these same blocks, so
    a page and a document cannot describe two different systems.
    """
    meta = load_meta(case_dir / "case.json")
    blocks: list[dict] = [
        {
            "kind": "source",
            "label": source["label"],
            "source_kind": source["kind"],
            "text": (case_dir / source["file"]).read_text(encoding="utf-8"),
        }
        for source in meta["sources"]
    ]
    return blocks + model_blocks(load_meta(case_dir / "model.json"))


def part_one_markdown(blocks: list[dict]) -> str:
    """Part one's blocks as the reading document prints them."""
    lines = ["## Part 1 — the system\n"]
    for block in blocks:
        if block["kind"] != "source":
            continue
        lines.append(f"### {block['label']} ({block['source_kind']})\n")
        lines.append("Exactly what the service would receive.\n")
        lines.append(quoted(block["text"]))
        lines.append("")
    lines.append("### What the model says is in it\n")
    lines.append(
        "Not part of the question, but the records cite these names, so you"
        " need them.\n"
    )
    lines.append(
        model_markdown([block for block in blocks if block["kind"] != "source"])
    )
    return "\n".join(lines)


def part_one(case_dir: Path) -> str:
    return part_one_markdown(part_one_blocks(case_dir))


def parts_after_blocks(case_dir: Path) -> dict[str, dict]:
    """The recorded set per declared framework, rendered through RENDERERS.

    Keyed by framework rather than concatenated, so a caller can show one at a
    time and so a package that joins `PACKAGES` arrives here through its own
    renderer entry with no edit.
    """
    meta = load_meta(case_dir / "case.json")
    return {
        declared["name"]: RENDERERS[declared["name"]](
            load_records(case_dir / "claims" / f"{declared['name']}.json")
        )
        for declared in meta["frameworks"]
    }


def parts_after(case_dir: Path) -> dict[str, str]:
    """The recorded set per declared framework, as the document prints it."""
    return {
        name: part_markdown(rendered, part)
        for part, (name, rendered) in enumerate(
            parts_after_blocks(case_dir).items(), start=2
        )
    }


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


def documents(corpus: Path = CORPUS) -> list[Path]:
    """Every case this generator writes a document for.

    Split out of :func:`main` so the check that pins those documents reads the
    same selection rather than a second copy of it. A derived file leaves the
    submission delta, so that check is the only thing pinning one — and a case
    the generator writes but the check skips would be pinned by nothing.
    """
    return [
        case_dir
        for case_dir in sorted(corpus.iterdir())
        if case_dir.is_dir() and case_dir.name not in HAND_WRITTEN
    ]


def main() -> int:
    for case_dir in documents():
        (case_dir / GENERATED_DOCUMENT).write_text(
            build_doc(case_dir), encoding="utf-8"
        )
        print(f"wrote {case_dir.name}/{GENERATED_DOCUMENT}")
    print(f"{len(documents())} reading document(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
