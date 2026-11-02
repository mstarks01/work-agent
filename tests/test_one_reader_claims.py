"""Each comment or docstring that claims a rule has one reader names its guard.

A claim is a comment or a docstring in :data:`~tests.source_tree.PYTHON`, or in
a page script there, that says "the one reader",
"one reader of", "the only place" or "the single source". A claim is a
promise that no second site applies the rule, and only a lint keeps that
promise as the code moves. So the claim must name a symbol that a lint holds:

- the reader of a row of :data:`~tests.test_one_reader_lints.ONE_READERS`, or
- a symbol that ``tests/test_one_reader_lints.py`` imports, or spells as a
  whole string, as the function of a site or the name of a call it matches.

The symbols of a claim are its owner and each symbol in backticks on the
claim's line, on the line before it or on the line after it, because a claim
names its reader on either side of its words. The owner of a comment is the
definition or the assignment that starts on the next statement, and otherwise
the function or the class that holds it. The owner of a docstring is the
function or the class it documents, or the name it follows as an attribute
docstring. A claim spans line breaks, so a claim wrapped across two lines is
still found. "The one reader" followed by a verb in ``-ing`` describes a
reader and claims nothing, and so does "a single source" or "no single
source".

:data:`UNGUARDED_CLAIMS` holds each claim that a ruling keeps without a guard.
An entry that matches no unguarded claim fails, so it cannot outlive its claim.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

from tests.source_tree import (
    PYTHON,
    REPO_ROOT,
    SHORTEST_REASON,
    parse,
    source_files,
    without,
)
from tests.test_one_reader_lints import ONE_READERS

pytestmark = pytest.mark.lint

#: The parts of the read trees that the lint leaves out, with the reason.
LEFT_OUT: dict[str, str] = {
    "evals/experiments": (
        "an audit script there runs one experiment as it ran on its day, so it"
        " records the rule of that day and no live code calls it"
    ),
}

#: The files the lint reads: the Python the project runs, and the page scripts
#: beside it.
FILES = without(source_files(*PYTHON, suffixes=(".py", ".js")), LEFT_OUT)


#: The words that make a one-reader claim, with any whitespace between them.
CLAIM = re.compile(
    r"\bthe\s+one\s+reader\b(?!\s+\w+ing\b)"
    r"|\bone\s+reader\s+of\b"
    r"|\bthe\s+only\s+places?\b"
    r"|\bthe\s+single\s+source\b",
    re.IGNORECASE,
)

#: A symbol in backticks, as Sphinx or Markdown writes it: the last dotted
#: part, without a leading ``~`` or trailing call parentheses.
_TICKED = re.compile(r"`~?(?:[\w.]+\.)?(\w+)(?:\(\))?`")

#: The JavaScript forms that name what a comment above them is about.
_JS_OWNER = re.compile(r"(?:function\s+|const\s+|let\s+)(\w+)")

#: A run of ``//`` comment lines, or one ``/* */`` comment, in a page script.
_SCRIPT_COMMENT = re.compile(r"//[^\n]*(?:\n[ \t]*//[^\n]*)*|/\*.*?\*/", re.DOTALL)

#: The one shared reason for each claim the maintainer's ruling keeps.
_RULED = (
    "ruled on 2026-11-02: an older claim whose symbol a test outside the lints"
    " reads stays without a lint row, because no defect in it is proved"
)

#: Each claim that a ruling keeps without a guard, by its file and owner.
UNGUARDED_CLAIMS: dict[tuple[str, str], str] = dict.fromkeys(
    [
        ("evals/critic_review/replay.py", "_reading"),
        ("evals/harness/applicability.py", "Observation"),
        ("evals/harness/applicability.py", "applied_requirements"),
        ("evals/harness/audit.py", "held_out"),
        ("evals/harness/baseline.py", "declared_pins"),
        ("evals/harness/flow_ids.py", "rewrite_text"),
        ("evals/harness/instruments.py", "ModeRun"),
        ("evals/harness/ledger.py", "verdicts_for"),
        ("evals/harness/modes.py", "NAMED_TYPES"),
        ("evals/harness/modes.py", "PROJECTION_COMPARED"),
        ("evals/harness/modes.py", "reachable_controls"),
        ("evals/harness/node_call.py", "node_call"),
        ("evals/harness/provenance.py", "checkout_state"),
        ("evals/harness/reference.py", "stride_readings"),
        ("evals/harness/replay.py", "bind_assertions"),
        ("evals/harness/run.py", "record_failure"),
        ("evals/harness/verbs.py", "EQUIVALENT"),
        ("evals/reference_facts.py", "signed_proposal"),
        ("evals/review_submission.py", "_case_problems"),
        ("src/analysis_service/analysis.py", "comparable_asset_tags"),
        ("src/analysis_service/analysis.py", "crossings_by_flow"),
        ("src/analysis_service/analysis.py", "leading_word"),
        ("src/analysis_service/answer_forms.py", "answer_limit"),
        ("src/analysis_service/answer_sets.py", "within_held_limits"),
        ("src/analysis_service/answer_state.py", "recommended"),
        ("src/analysis_service/assertion_use.py", "assertion_use"),
        ("src/analysis_service/assertions.py", "_bounded"),
        ("src/analysis_service/assertions.py", "admissible"),
        ("src/analysis_service/assertions.py", "conflicts"),
        ("src/analysis_service/assertions.py", "projected_attribute"),
        ("src/analysis_service/assertions.py", "quarantine"),
        ("src/analysis_service/bands.py", "band_of"),
        ("src/analysis_service/charges.py", "recordable_charge"),
        ("src/analysis_service/charges.py", "records_reported_charge"),
        ("src/analysis_service/claim_identity.py", "distinct_mechanisms"),
        ("src/analysis_service/claims.py", "dismissed_refs"),
        ("src/analysis_service/claims.py", "re_ask_kinds"),
        ("src/analysis_service/compact.py", "parse_extraction"),
        ("src/analysis_service/critic_round.py", "rulings_of"),
        ("src/analysis_service/deployment.py", "job_deadline_seconds"),
        ("src/analysis_service/evidence.py", "ground_gloss"),
        ("src/analysis_service/evidence.py", "ground_issues"),
        ("src/analysis_service/evidence.py", "ground_places"),
        ("src/analysis_service/evidence.py", "prepared_view"),
        ("src/analysis_service/fan_in.py", "within_bound"),
        ("src/analysis_service/frameworks/__init__.py", "lane_of"),
        ("src/analysis_service/graph.py", "JOIN_ROLE"),
        ("src/analysis_service/graph.py", "LANE_ARTIFACTS"),
        ("src/analysis_service/graph.py", "fired_by_lane"),
        ("src/analysis_service/graph.py", "framework_lane_inputs"),
        ("src/analysis_service/graph.py", "render_model"),
        ("src/analysis_service/grounding.py", "match_normalized"),
        ("src/analysis_service/jobs.py", "envelope_parts"),
        ("src/analysis_service/jobs.py", "event_frames"),
        ("src/analysis_service/jobs.py", "execute_job"),
        ("src/analysis_service/jobs.py", "parent_of"),
        ("src/analysis_service/knowledge.py", "select_per_lane"),
        ("src/analysis_service/model_gate.py", "library_sends_no_native_schema"),
        ("src/analysis_service/model_gate.py", "schema_support"),
        ("src/analysis_service/model_gate.py", "takes_thinking"),
        ("src/analysis_service/model_gate.py", "tool_models"),
        ("src/analysis_service/model_tiers.py", "bound_tiers"),
        ("src/analysis_service/model_tiers.py", "charge_mode"),
        ("src/analysis_service/model_tiers.py", "credential_mode"),
        ("src/analysis_service/model_tiers.py", "upstreams_for"),
        ("src/analysis_service/open_facts.py", "gaps"),
        ("src/analysis_service/pipeline.py", "entry_of"),
        ("src/analysis_service/report_conditions.py", "corrected_findings"),
        ("src/analysis_service/sources.py", "plain_name"),
        ("src/analysis_service/system_model.py", "capability_facts"),
        ("src/analysis_service/vendors.py", "litellm_provider"),
        ("src/analysis_service/vendors.py", "vendor_for_route"),
        ("webapp/static/report_view.js", "GROUND_KIND"),
    ],
    _RULED,
)


@dataclass(frozen=True)
class Claim:
    """One claim: where it sits, the symbol that owns it and each symbol it names."""

    path: str
    line: int
    owner: str
    named: frozenset[str]

    @property
    def key(self) -> tuple[str, str]:
        return (self.path, self.owner)

    @property
    def symbols(self) -> frozenset[str]:
        return self.named | {self.owner}


def _matches(text: str, first_line: int) -> Iterator[int]:
    """The line of each claim in ``text``, whose first line is ``first_line``."""
    for match in CLAIM.finditer(text):
        yield first_line + text.count("\n", 0, match.start())


def _comment_blocks(source: str) -> Iterator[tuple[int, str]]:
    """Each run of comment lines in Python ``source``, with its first line."""
    block: list[str] = []
    start = last = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        if block and line != last + 1:
            yield start, "\n".join(block)
            block = []
        if not block:
            start = line
        block.append(token.string.lstrip("#:"))
        last = line
    if block:
        yield start, "\n".join(block)


def _definitions(tree: ast.Module) -> list[ast.stmt]:
    """Each statement in ``tree``, in source order."""
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.stmt)),
        key=lambda node: (node.lineno, node.col_offset),
    )


def _name_of(node: ast.stmt) -> str | None:
    """The name a definition or a one-name assignment binds."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    target = (
        node.targets[0]
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        else getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign)
        else None
    )
    return target.id if isinstance(target, ast.Name) else None


def _holder(statements: list[ast.stmt], line: int, path: Path) -> str:
    """The innermost function or class whose lines hold ``line``."""
    holders = [
        node
        for node in statements
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.lineno <= line <= (node.end_lineno or node.lineno)
    ]
    return holders[-1].name if holders else path.stem


def _python_claims(path: Path) -> Iterator[tuple[int, str]]:
    """Each claim line in a Python file, with its owner."""
    tree = parse(path)
    statements = _definitions(tree)
    for start, text in _comment_blocks(path.read_text(encoding="utf-8")):
        for line in _matches(text, start):
            following = next((node for node in statements if node.lineno > line), None)
            name = None if following is None else _name_of(following)
            yield line, name or _holder(statements, line, path)
    for body_owner in ast.walk(tree):
        body = getattr(body_owner, "body", None)
        if not isinstance(body, list):
            continue
        for index, node in enumerate(body):
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                continue
            before = _name_of(body[index - 1]) if index else None
            for line in _matches(node.value.value, node.lineno):
                yield line, before or _holder(statements, line, path)


def _script_claims(path: Path) -> Iterator[tuple[int, str]]:
    """Each claim line in a page script, with the name the next lines define."""
    source = path.read_text(encoding="utf-8")
    rows = source.splitlines()
    for comment in _SCRIPT_COMMENT.finditer(source):
        start = source.count("\n", 0, comment.start()) + 1
        text = re.sub(r"(?m)^[ \t]*(?://|/?\*+/?)", "", comment.group())
        for line in _matches(text, start):
            owner = next(
                (
                    found.group(1)
                    for row in rows[line - 1 : line + 15]
                    if (found := _JS_OWNER.search(row))
                ),
                path.stem,
            )
            yield line, owner


@cache
def claims() -> tuple[Claim, ...]:
    """Each one-reader claim in :data:`FILES`."""
    found = []
    for path in FILES:
        rows = path.read_text(encoding="utf-8").splitlines()
        read = _python_claims if path.suffix == ".py" else _script_claims
        for line, owner in read(path):
            near = " ".join(rows[max(0, line - 2) : line + 1])
            found.append(
                Claim(
                    path.relative_to(REPO_ROOT).as_posix(),
                    line,
                    owner,
                    frozenset(_TICKED.findall(near)),
                )
            )
    return tuple(sorted(set(found), key=lambda claim: (claim.path, claim.line)))


@cache
def guarded_symbols() -> frozenset[str]:
    """Each symbol a lint holds: the reader of each ``ONE_READERS`` row, and
    each symbol that ``tests/test_one_reader_lints.py`` imports or spells as a
    whole string, as a site or a call it matches."""
    tree = parse(REPO_ROOT / "tests" / "test_one_reader_lints.py")
    spelled = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.isidentifier()
    }
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    readers = {row.reader[1] for row in ONE_READERS.values()}
    return frozenset(spelled | imported | readers)


def unguarded() -> list[Claim]:
    """Each claim that names no symbol a lint holds."""
    held = guarded_symbols()
    return [claim for claim in claims() if not claim.symbols & held]


def test_each_one_reader_claim_names_its_guard():
    """The lint: each claim names a symbol a lint holds, or a ruling keeps it."""
    found = [
        f"{claim.path}:{claim.line} {claim.owner}"
        for claim in unguarded()
        if claim.key not in UNGUARDED_CLAIMS
    ]

    assert not found, (
        f"these claims name no symbol that a lint holds: {found}. Add a row to"
        " ONE_READERS in tests/test_one_reader_lints.py for the rule, or say in"
        " the comment what is true."
    )


@pytest.mark.parametrize("key", sorted(UNGUARDED_CLAIMS), ids="/".join)
def test_each_kept_claim_is_still_an_unguarded_claim(key):
    """An entry whose claim moved, went away or got a guard is stale."""
    assert key in {claim.key for claim in unguarded()}, (
        f"{key} is in UNGUARDED_CLAIMS, but no unguarded claim sits there; drop"
        " the entry"
    )


@pytest.mark.parametrize("key", sorted(UNGUARDED_CLAIMS), ids="/".join)
def test_each_kept_claim_states_a_reason(key):
    assert len(UNGUARDED_CLAIMS[key]) >= SHORTEST_REASON, f"state why {key} stays"


def test_the_claim_pattern_finds_a_claim_and_skips_a_description():
    """Positive and negative controls for the words of a claim."""
    claimed = [
        "the one reader of a flow's label",
        "This is the one\n    reader of the cell.",
        "One reader of the file.",
        "the only place that correspondence lives",
        "Derived so the prefix stays the single source.",
    ]
    described = [
        "and no single source is at fault.",
        'a check that dropped a "not" would be the one reader lying to the rest.',
        "a single source of text",
    ]

    assert [bool(CLAIM.search(text)) for text in claimed] == [True] * len(claimed)
    assert [bool(CLAIM.search(text)) for text in described] == [False] * len(described)


def test_the_lint_reads_the_owner_and_the_named_symbol(tmp_path):
    """Positive control: a claim on an unheld symbol fails, and a claim that
    names a reader in backticks, or sits on that reader, passes."""
    reader = next(iter(ONE_READERS.values())).reader[1]
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def unheld_probe_rule():\n"
        '    """The one reader of a probe rule."""\n'
        f"def {reader}():\n"
        '    """The one reader of its rule."""\n'
        "#: :func:`" + reader + "` is the one reader of that rule.\n"
        "PROBE_TABLE = {}\n",
        encoding="utf-8",
    )
    found = sorted((line, owner) for line, owner in _python_claims(probe))

    assert found == [(2, "unheld_probe_rule"), (4, reader), (5, "PROBE_TABLE")]
    assert "unheld_probe_rule" not in guarded_symbols()
    assert reader in guarded_symbols()


def test_the_lint_reads_a_real_population():
    """Guards the guard: a scan that finds too few claims passes vacuously."""
    assert len(claims()) > 150
