"""Holding a **Case Sitting**: what it reads, and what it writes when it ends.

The act is #327's, and ``evals/BLESSING.md`` step 6 is the method. This module
is the part a front end does not get to reinvent: which files a sitting must
read, the digest of each as it stood, the append-only entry that records it,
and the debt line it clears. ``webapp/sitting.py`` is one surface over this;
the CLI path writes the same files by hand and the checks cannot tell them
apart, which is the point — one implementation of the rules.

**The own list comes first, and that is a property rather than an
instruction.** A reader who opens the recorded sets first finds them
reasonable and the sitting measures nothing. So a caller here asks for part
one and part two separately, and :func:`parts_after` is what a surface must
withhold until the reader has written their own list down. That mirrors the
review app's configuration-blindness, which is enforced by the queue item
having no field for it rather than by asking the reviewer not to peek.

Nothing here talks to a network or a provider. A sitting is reading, and the
whole path is free.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import build_review_docs as docs
from evals.harness.reference import CLAIMS_DIR, ReadRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "evals" / "corpus"

#: Where the debt list lives. A sitting that does not clear its line leaves
#: the count a lie, so ``submit sitting`` refuses one that has not.
DEBT_FILE = "tests/test_case_review.py"


class SittingError(ValueError):
    """The sitting cannot be recorded; the message says what stops it."""


def required_files(case_dir: Path) -> list[str]:
    """What a complete sitting reads, derived from the case's own declaration.

    The shared artefacts plus one reference set per declared framework, so a
    case that gains a package requires its set read by construction and no
    table here needs editing.
    """
    meta = docs.load_meta(case_dir / "case.json")
    return ["source.md", "model.json"] + [
        f"{CLAIMS_DIR}/{declared['name']}.json" for declared in meta["frameworks"]
    ]


def read_records(case_dir: Path, files: list[str]) -> list[ReadRecord]:
    """Each file pinned to the bytes it holds now.

    Taken at the moment the sitting is recorded, so the entry signs what will
    merge. A later edit to any of them moves the digest and re-opens the debt.
    """
    records = []
    for name in files:
        path = case_dir / name
        if not path.is_file():
            raise SittingError(
                f"{name}: not in the case directory, so it cannot be read"
            )
        records.append(
            ReadRecord(file=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        )
    return records


@dataclass(frozen=True)
class Prepared:
    """One case, ready to be sat with. Part two is deliberately separate."""

    case_id: str
    title: str
    part_one: str
    #: Framework -> the recorded set as the reading document renders it. A
    #: surface must not send this until the reader's own list is in.
    part_two: dict[str, str]
    files: list[str]


def prepare(case_dir: Path) -> Prepared:
    """Everything a sitting needs, split at the own-list boundary."""
    meta = docs.load_meta(case_dir / "case.json")
    return Prepared(
        case_id=meta["id"],
        title=meta["title"],
        part_one=docs.part_one(case_dir),
        part_two=docs.parts_after(case_dir),
        files=required_files(case_dir),
    )


def document(
    prepared: Prepared,
    own_list: list[str],
    marks: dict[str, str],
    missing: list[str],
    notes: str,
) -> str:
    """The filled reading document — the evidence that the method ran.

    Only the filled copy shows the own list was written before the recorded
    sets were opened, which is the one thing a generated ``REVIEW.md`` cannot
    show. ``submit sitting`` checks it exists; a reader checks it means
    something.
    """
    lines = [
        f"# Case Sitting — `{prepared.case_id}`\n",
        f"\n**{prepared.title}**\n",
        (
            "\nHeld through `webapp/sitting.py`. The own list below was"
            " written before the recorded sets were shown.\n"
        ),
        "\n---\n",
        prepared.part_one,
        "\n## Your list, written first\n",
    ]
    lines += [f"- {item}" for item in own_list] or ["- (nothing)"]
    for framework, body in prepared.part_two.items():
        lines.append(f"\n---\n\n## The recorded `{framework}` set\n")
        lines.append(body)
        answered = {
            key: value for key, value in marks.items() if key.startswith(framework)
        }
        if answered:
            lines.append("\n### Marks\n")
            lines += [f"- `{key}` — {value}" for key, value in sorted(answered.items())]
    lines.append("\n---\n\n## On your list and not on theirs\n")
    lines += [f"- {item}" for item in missing] or ["- (nothing)"]
    if notes:
        lines.append(f"\n## Notes\n\n{notes}\n")
    return "\n".join(lines) + "\n"


def record(
    case_dir: Path,
    reviewer: str,
    read: list[ReadRecord],
    document_name: str,
    notes: str,
) -> dict[str, Any]:
    """Append the sitting to ``case.json``'s ``reviews``, and return the entry.

    Append-only: a correction is a new entry, never an edit to one recorded
    (#327). The file is rewritten whole because it is small and JSON has no
    append, but nothing already in ``reviews`` is touched.
    """
    path = case_dir / "case.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    entry = {
        "reviewer": reviewer,
        "date": datetime.now(UTC).date().isoformat(),
        "read": [{"file": item.file, "sha256": item.sha256} for item in read],
        "document": document_name,
        "notes": notes,
    }
    meta.setdefault("reviews", []).append(entry)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entry


def _debt_entries(source: str) -> list[tuple[str, int, int]]:
    """Each ``UNREVIEWED`` entry as ``(case id, first line, last line)``.

    Read through :mod:`ast` rather than by matching text, because the debt
    prose is arbitrary English: counting brackets to find where an entry ends
    works only while no reason writes one, and the reasons cite issues. The
    parser knows where every entry starts, so an entry runs to the line before
    the next one — which is what carries the trailing comma, the closing
    parenthesis and any comment with it, whatever shape they were written in.

    Line numbers are 0-based and the end is exclusive, ready to slice.
    """
    tree = ast.parse(source)
    table = next(
        (
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.AnnAssign | ast.Assign)
            and isinstance(node.value, ast.Dict)
            and any(
                isinstance(target, ast.Name) and target.id == "UNREVIEWED"
                for target in (
                    [node.target] if isinstance(node, ast.AnnAssign) else node.targets
                )
            )
        ),
        None,
    )
    if not isinstance(table, ast.Dict):
        raise SittingError(f"{DEBT_FILE}: no UNREVIEWED table to read")

    starts = [
        (key.value, key.lineno - 1)
        for key in table.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    if len(starts) != len(table.keys):
        raise SittingError(f"{DEBT_FILE}: UNREVIEWED holds a key that is not a string")
    # The closing brace bounds the last entry; every other one ends where the
    # next begins. `end_lineno` on the value would stop before the `),`.
    bounds = [line for _, line in starts[1:]] + [(table.end_lineno or 0) - 1]
    return [
        (case, start, end) for (case, start), end in zip(starts, bounds, strict=True)
    ]


def clear_debt(root: Path, case_id: str) -> bool:
    """Remove this case's entry from ``UNREVIEWED``. Returns whether it wrote.

    The list names the cases nobody has read, so it is only accurate while a
    case that gets read comes off it.
    """
    path = root / DEBT_FILE
    source = path.read_text(encoding="utf-8")
    span = next(
        ((start, end) for case, start, end in _debt_entries(source) if case == case_id),
        None,
    )
    if span is None:
        return False
    start, end = span
    lines = source.splitlines(keepends=True)
    path.write_text("".join(lines[:start] + lines[end:]), encoding="utf-8")
    return True


def cases_in_debt(root: Path) -> list[str]:
    """Every case the debt list still names, in file order."""
    source = (root / DEBT_FILE).read_text(encoding="utf-8")
    return [case for case, _, _ in _debt_entries(source)]
