"""Each comment or docstring that claims a rule has one reader names its guard.

A claim is a comment or a docstring in :data:`~tests.source_tree.PYTHON`, or in
a page script there, that says "the one reader", "one reader of", "the only
place" or "the single source". A claim is a promise that no second site
applies the rule, and only a lint keeps that promise as the code moves. So the
claim must name a site that a lint holds:

- the reader of a row of :data:`~tests.test_one_reader_lints.ONE_READERS`;
- a ``(path, function)`` site that an ``assert`` in a test of
  ``tests/test_one_reader_lints.py`` names as the one site of a rule: the one
  element of a list or a set that the found sites must equal.

A site that a row names as no second reader guards nothing. A name that its
file defines more than once guards nothing either, because a site cannot tell
the definitions apart.

A claim passes when its owner, in its own file, is such a site, or when it
names such a site in backticks on the claim's line, on the line before it or
on the line after it. A claim names its reader on either side of its words. A
dotted name in backticks names a function of the module it starts with, read
through the imports of the claim's file; a bare name names a function of the
claim's own file. So a guard is a site, never a bare name.

The owner of a comment is the definition or the assignment that starts on the
next statement, and otherwise the function or the class that holds it. The
owner of a docstring is the function or the class it documents, or the name it
follows as an attribute docstring. A claim spans line breaks, so a claim
wrapped across two lines is still found. "The one reader" followed by a verb in ``-ing`` describes a
reader and claims nothing, and so does "a single source" or "no single
source".

:data:`UNGUARDED_CLAIMS` holds each claim that a ruling keeps without a guard.
An entry that matches no unguarded claim fails, so it cannot outlive its claim.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

from tests.source_tree import (
    PYTHON,
    REPO_ROOT,
    SHORTEST_REASON,
    block_comments,
    comment_runs,
    imports,
    line_comments,
    module_name,
    parse,
    source_files,
    without,
)
from tests.test_one_reader_lints import ONE_READERS, Site

pytestmark = pytest.mark.lint

#: The parts of :data:`~tests.source_tree.PYTHON` the lint leaves out, with the
#: reason.
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

#: A symbol in backticks, as Sphinx or Markdown writes it: its dotted text,
#: without a leading ``~`` or trailing call parentheses.
_TICKED = re.compile(r"`~?([\w.]*\w)(?:\(\))?`")

#: The statements that define a name a test can require.
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

#: How many lines after a page-script comment the lint reads for the name the
#: comment is about: the line below the comment, and one more for a wrapped
#: declaration. Each claim in the page scripts sits right above its name.
_OWNER_REACH = 2

#: The JavaScript forms that name what a comment above them is about.
_JS_OWNER = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|const|let)\s+(\w+)")

#: The one shared reason for each claim the maintainer's ruling keeps.
_RULED = (
    "the maintainer ruled on 2026-11-02 that the older claims of that day's"
    " sweep that a unit test reads stay as they are, as no defect in them is"
    " proved"
)

#: Each claim that a ruling keeps without a guard, by its file and owner.
UNGUARDED_CLAIMS: dict[Site, str] = dict.fromkeys(
    [
        ("evals/critic_review/replay.py", "_reading"),
        ("evals/harness/alignment.py", "protocol_state"),
        ("evals/harness/applicability.py", "Observation"),
        ("evals/harness/applicability.py", "applied_requirements"),
        ("evals/harness/audit.py", "held_out"),
        ("evals/harness/baseline.py", "declared_pins"),
        ("evals/harness/flow_ids.py", "flow_ids"),
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
        ("src/analysis_service/answer_state.py", "passes_floor"),
        ("src/analysis_service/answer_state.py", "recommended"),
        ("src/analysis_service/assertion_use.py", "assertion_use"),
        ("src/analysis_service/assertions.py", "settled"),
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
        ("src/analysis_service/evidence.py", "lead_topics"),
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
        ("src/analysis_service/links.py", "resumed_sources"),
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
        ("src/analysis_service/settings_variables.py", "secret_vars"),
        ("src/analysis_service/system_model.py", "capability_facts"),
        ("src/analysis_service/vendors.py", "litellm_provider"),
        ("src/analysis_service/vendors.py", "vendor_for_route"),
        ("webapp/static/report_view.js", "GROUND_KIND"),
    ],
    _RULED,
)


@dataclass(frozen=True)
class OneReaderClaim:
    """One one-reader claim: where it sits, the symbol that owns it and each
    symbol it names in backticks."""

    path: str
    line: int
    owner: str
    named: frozenset[str]

    @property
    def key(self) -> Site:
        return (self.path, self.owner)


def _matches(text: str, first_line: int) -> Iterator[int]:
    """The line of each claim in ``text``, whose first line is ``first_line``."""
    for match in CLAIM.finditer(text):
        yield first_line + text.count("\n", 0, match.start())


def _statements(tree: ast.Module) -> list[ast.stmt]:
    """Each statement in ``tree``, in source order."""
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.stmt)),
        key=lambda node: (node.lineno, node.col_offset),
    )


def _name_of(node: ast.stmt) -> str | None:
    """The name a definition or a one-name assignment binds."""
    if isinstance(node, _DEFINITIONS):
        return node.name
    target = None
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    return target.id if isinstance(target, ast.Name) else None


def _holder(statements: list[ast.stmt], line: int, path: Path) -> str:
    """The innermost function or class whose lines hold ``line``."""
    holders = [
        node
        for node in statements
        if isinstance(node, _DEFINITIONS)
        and node.lineno <= line <= (node.end_lineno or node.lineno)
    ]
    return holders[-1].name if holders else path.stem


def _python_claims(path: Path) -> Iterator[tuple[int, str]]:
    """Each claim line in a Python file, with its owner."""
    tree = parse(path)
    statements = _statements(tree)
    for start, text in comment_runs(path):
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
    """Each claim line in a page script, with the name that the code after
    its comment defines first."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for start, text in line_comments(path, "//") + block_comments(path):
        end = start + text.count("\n")
        owner = next(
            (
                found.group(1)
                for code in lines[end : end + _OWNER_REACH]
                if (found := _JS_OWNER.search(code))
            ),
            path.stem,
        )
        for line in _matches(text, start):
            yield line, owner


def _claims_in(path: Path, name: str) -> list[OneReaderClaim]:
    """Each claim in the file at ``path``, which the claims call ``name``."""
    lines = path.read_text(encoding="utf-8").splitlines()
    read = _python_claims if path.suffix == ".py" else _script_claims
    return [
        OneReaderClaim(
            name,
            line,
            owner,
            frozenset(_TICKED.findall(" ".join(lines[max(0, line - 2) : line + 1]))),
        )
        for line, owner in read(path)
    ]


@cache
def claims() -> tuple[OneReaderClaim, ...]:
    """Each one-reader claim in :data:`FILES`."""
    found = {
        claim
        for path in FILES
        for claim in _claims_in(path, path.relative_to(REPO_ROOT).as_posix())
    }
    return tuple(sorted(found, key=lambda claim: (claim.path, claim.line)))


def _tests(tree: ast.Module) -> Iterator[ast.FunctionDef]:
    """Each test function in ``tree``."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            yield node


def _asserted_sites(tree: ast.Module) -> Iterator[Site]:
    """Each site that an ``assert`` in a test names as the one site of a rule:
    the one element of a list or a set that the found sites must equal."""
    for test in _tests(tree):
        for statement in ast.walk(test):
            if not isinstance(statement, ast.Assert):
                continue
            for node in ast.walk(statement.test):
                if not (
                    isinstance(node, ast.Compare)
                    and len(node.ops) == 1
                    and isinstance(node.ops[0], ast.Eq)
                ):
                    continue
                candidates = [
                    side.elts[0]
                    for side in (node.left, *node.comparators)
                    if isinstance(side, ast.List | ast.Set) and len(side.elts) == 1
                ]
                for candidate in candidates:
                    if (site := _site(candidate)) is not None:
                        yield site


def _site(node: ast.AST) -> Site | None:
    """The site that the tuple ``node`` spells, or None."""
    if not (isinstance(node, ast.Tuple) and len(node.elts) == 2):
        return None
    path, name = map(_text, node.elts)
    return None if path is None or name is None else (path, name)


def _text(node: ast.AST | None) -> str | None:
    """The string ``node`` spells, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


@cache
def guarded_sites() -> frozenset[Site]:
    """Each ``(path, function)`` site that a lint holds.

    - the reader of each ``ONE_READERS`` row;
    - each site that an ``assert`` in a test of
      ``tests/test_one_reader_lints.py`` names as the one site of a rule.

    A site that a row names as no second reader is no guard. A name that its
    file defines more than once, or not at all, names no site, because a site
    is a file and a function and cannot tell the definitions apart.
    """
    tree = parse(REPO_ROOT / "tests" / "test_one_reader_lints.py")
    defined: dict[str, list[str]] = {}
    for path in FILES:
        if path.suffix == ".py":
            for node in ast.walk(parse(path)):
                if isinstance(node, _DEFINITIONS):
                    place = path.relative_to(REPO_ROOT).as_posix()
                    defined.setdefault(node.name, []).append(place)
    exempt = {site for row in ONE_READERS.values() for site in row.others}
    sites = {row.reader for row in ONE_READERS.values()}
    sites |= set(_asserted_sites(tree)) - exempt
    return frozenset(
        (path, name) for path, name in sites if defined.get(name, []).count(path) == 1
    )


@cache
def _modules() -> dict[str, str]:
    """The file of each module the lint reads, by its dotted name."""
    return {
        module_name(path): path.relative_to(REPO_ROOT).as_posix()
        for path in FILES
        if path.suffix == ".py"
    }


@cache
def _imported(path: str) -> dict[str, str]:
    """Each name that an import in the Python file ``path`` binds, with the
    dotted name it reads. A file the lint does not read binds nothing."""
    if path not in _modules().values():
        return {}
    return imports(parse(REPO_ROOT / path))


def _site_of(reference: str, path: str) -> Site:
    """The site that the backticked ``reference`` in the file ``path`` names.

    A dotted reference names the function in the longest module it starts
    with, read through the imports of ``path`` where its first part is an
    imported name. A reference that starts with no module names a function in
    ``path`` itself.
    """
    head, _, rest = reference.partition(".")
    dotted = _imported(path).get(head, head) + (f".{rest}" if rest else "")
    parts = dotted.split(".")
    modules = _modules()
    for end in range(len(parts) - 1, 0, -1):
        module = ".".join(parts[:end])
        if module in modules:
            return modules[module], parts[-1]
    return path, parts[-1]


def guarded(claim: OneReaderClaim) -> bool:
    """Whether ``claim``'s owner, in its own file, is a guarded site, or the
    claim names the function of a guarded site in backticks."""
    sites = guarded_sites()
    return claim.key in sites or any(
        _site_of(reference, claim.path) in sites for reference in claim.named
    )


def unguarded() -> list[OneReaderClaim]:
    """Each claim that names no guard."""
    return [claim for claim in claims() if not guarded(claim)]


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


def test_a_kept_entry_keeps_one_claim():
    """An entry keys one claim. A second unguarded claim under a kept owner
    is a new claim, which the ruling does not cover."""
    kept = Counter(claim.key for claim in unguarded() if claim.key in UNGUARDED_CLAIMS)

    assert [key for key, count in kept.items() if count > 1] == []


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
    """Positive and negative controls for the guard.

    A claim on an unheld function fails. A claim on a ``ONE_READERS`` reader
    in the reader's own file passes, and so does a claim there that names
    that reader in backticks. In another file, both fail: a guard is a site,
    never a bare name.
    """
    place, reader = next(iter(ONE_READERS.values())).reader
    probe = tmp_path / "probe.py"
    probe.write_text(
        "#: :func:`" + reader + "` is the one reader of that rule.\n"
        "PROBE_TABLE = {}\n"
        "\n"
        "def unheld_probe_rule():\n"
        '    """The one reader of a probe rule."""\n'
        "\n"
        f"def {reader}():\n"
        '    """The one reader of its rule."""\n',
        encoding="utf-8",
    )
    found = sorted(_claims_in(probe, place), key=lambda claim: claim.line)
    elsewhere = _claims_in(probe, "src/analysis_service/elsewhere.py")

    assert [(claim.line, claim.owner) for claim in found] == [
        (1, "PROBE_TABLE"),
        (5, "unheld_probe_rule"),
        (8, reader),
    ]
    assert [guarded(claim) for claim in found] == [True, False, True]
    assert not any(guarded(claim) for claim in elsewhere)


def test_a_dotted_name_resolves_through_its_module_and_the_imports(tmp_path):
    """Positive and negative controls for a dotted or imported reference.

    A dotted reference resolves to a function of the module it names. A bare
    name that the claim's file imports resolves through that import. The
    reader's name under another module does not resolve to the reader.
    """
    place, reader = ONE_READERS["the frameworks a job names"].reader
    importer = "src/analysis_service/deployment.py"
    probe = tmp_path / "probe.py"
    probe.write_text(
        f"#: :func:`{module_name(REPO_ROOT / place)}.{reader}` is the one reader.\n"
        "FIRST = 1\n"
        "\n"
        f"#: :func:`analysis_service.jobs.{reader}` is the one reader of them.\n"
        "SECOND = 2\n"
        "\n"
        f"#: :func:`{reader}` is the one reader of the rule.\n"
        "THIRD = 3\n",
        encoding="utf-8",
    )
    found = sorted(_claims_in(probe, importer), key=lambda claim: claim.line)

    assert reader in _imported(importer)
    assert [guarded(claim) for claim in found] == [True, False, True]


def test_a_site_a_row_exempts_guards_nothing(tmp_path):
    """Negative control: a claim on a site that a row names as no second
    reader fails, though the lint file spells that site."""
    row = ONE_READERS["a rejection's steps in an eval instrument"]
    (place, name), *_ = row.others
    probe = tmp_path / "probe.py"
    probe.write_text(
        f'def {name}():\n    """The one reader of its own rule."""\n',
        encoding="utf-8",
    )
    (claim,) = _claims_in(probe, place)

    assert claim.key == (place, name)
    assert not guarded(claim)


def test_a_site_among_several_in_an_assert_guards_nothing():
    """Negative control: a site that an assert lists beside other sites is no
    one reader, so it guards nothing."""
    assert ("src/analysis_service/job_answers.py", "amend") not in guarded_sites()
    assert ("src/analysis_service/fixes.py", "absent_rows") not in guarded_sites()
    assert ("src/analysis_service/claim_identity.py", "report_rows") in guarded_sites()


def test_the_lint_reads_a_real_population():
    """Guards the guard: a scan that finds too few claims passes vacuously."""
    assert len(claims()) > 150
