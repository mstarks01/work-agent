"""Three readers a fact must have, and the sweeps that find one with none.

A defect class this repository keeps meeting is **a fact with no reader**: an
answer an endpoint gives that the page never reads, a field a model carries
that no code reads, and a string a test asserts that the served page holds
only inside a comment. Each one passes every test that ships with it,
because the missing reader is the thing that should read the fact. This module
checks for the class, so it does not accumulate (see the
``reviewed/2026-09-09`` round).

Each lint reads the tree rather than a list somebody remembered, in the shape
``test_dead_code_lints.py`` uses: a reader is anything under
:data:`SEARCHED` that reaches the name, tests included. Where a fact is for a
person rather than for code — provenance, a payload consumer reads outside
this repository — it is declared in :data:`DECLARED_FIELDS` with the reason,
and a declaration that stops being needed fails too.

**The limit worth stating.** The answer-key lint holds a key read *anywhere*
in the page's script as read, because a grep cannot tell which response a
``d.state`` came from. A key one endpoint answers and another endpoint's
handler reads passes here. A per-endpoint check would be stricter.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args

import pytest

from analysis_service.assertions import QUALIFIED_SPELLING, UNKNOWN
from analysis_service.claims import ATTRIBUTE_GROUNDS
from analysis_service.factbundle import LANDED
from analysis_service.sources import FORMATTING_CATEGORIES
from analysis_service.system_model import Element, all_attribute_names
from evals.harness.reference import CASE_FILES, MUST_FIND
from evals.harness.roster import STANDINGS
from tests.source_tree import REPO_ROOT, parse, source_files
from webapp import main, offline_sitting, review, sitting
from webapp.page import client_script

#: Every directory that may legitimately read a fact, including the tests.
SEARCHED = ("src", "evals", "webapp", "tests")

#: The sitting app's endpoints and the one script that reads their answers.
#: Keyed by module so a second surface arrives as a row rather than a branch.
ANSWERED_BY: dict[str, str] = {
    "webapp/sitting_base.py": "sitting.js",
    "webapp/sitting.py": "sitting.js",
}

#: Every page template the local apps serve, so a spelling a test asserts is
#: looked for in the bytes a reader gets.
PAGES: tuple[str, ...] = (
    sitting._PAGE,
    offline_sitting._PAGE,
    review._QUEUE_PAGE,
    review._REVIEW_PAGE,
    main._FORM_PAGE,
    (REPO_ROOT / "webapp" / "report_view.html").read_text(encoding="utf-8"),
)
SCRIPTS: tuple[str, ...] = tuple(
    path.name for path in sorted((REPO_ROOT / "webapp" / "static").glob("*.js"))
)

#: Fields no code reads, kept on purpose, with the reason. Two readings live
#: here: a fact for a person (provenance, a demand a reader of a failure needs
#: in one sentence), and a payload a consumer outside this repository reads.
#: An entry whose field gains a reader in code fails below, so the table
#: cannot rot into an excuse.
DECLARED_FIELDS: dict[str, str] = {
    "CaseMetadata.bootstrap": (
        "Provenance for a person: how the case's model was first made. The"
        " loader requires it, so a case cannot ship without saying, and no rule"
        " branches on its value."
    ),
    "AdversarialCase.demand": (
        "What the injected text asks for, in one sentence, so a reader of a"
        " failure knows what the model was talked into without opening the"
        " source. Its own docstring says nothing scores it."
    ),
}


# --- 1. An answer the page never reads ------------------------------------------


def _endpoint_answer_keys(module: Path) -> dict[str, set[str]]:
    """Every string key an endpoint's ``JSONResponse`` literal spells, by route."""
    source = module.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=    @app\.(?:get|post)\()", source)
    found: dict[str, set[str]] = {}
    for block in blocks:
        route = re.match(r'    @app\.(?:get|post)\("([^"]+)"\)', block)
        if route is None:
            continue
        body = block.split("\n    return app", 1)[0]
        keys = set(re.findall(r'^\s+"([a-z_]+)":', body, re.MULTILINE))
        if keys:
            found[route.group(1)] = keys
    return found


def _read_by_script(key: str, script: str) -> bool:
    return re.search(rf"\.{key}\b|\[\"{key}\"\]", script) is not None


@pytest.mark.parametrize("module", sorted(ANSWERED_BY), ids=lambda m: m)
def test_every_answer_the_sitting_app_gives_is_read_by_its_page(module):
    """A key the page never reads is an answer nobody hears.

    Every key a route answers must appear in the page's script. A key the
    script does not read reaches no screen.
    """
    script = client_script(ANSWERED_BY[module])
    unread = {
        route: sorted(key for key in keys if not _read_by_script(key, script))
        for route, keys in _endpoint_answer_keys(REPO_ROOT / module).items()
    }
    unread = {route: keys for route, keys in unread.items() if keys}
    assert not unread, (
        f"{module} answers with keys {ANSWERED_BY[module]} never reads: {unread}."
        " Read them on the page, or stop answering with them."
    )


def test_the_answer_key_scan_reads_a_real_population():
    """A scanner that finds nothing passes everything."""
    routes = {
        route
        for module in ANSWERED_BY
        for route in _endpoint_answer_keys(REPO_ROOT / module)
    }
    assert {"/api/rail", "/api/part-one", "/api/stage", "/api/contribute"} <= routes


# --- 2. A spelling a test asserts that only a comment carries ------------------

_ASSERTION = re.compile(r"""assert\s+(['"])(.+?)\1\s+(?:not\s+)?in\s+(\S+)""")
_PAGE_TARGET = re.compile(r"page|_PAGE|script|nav|\.text\b|body")


def _without_comments(pages: tuple[str, ...], scripts: tuple[str, ...]) -> str:
    html = re.sub(r"<!--(?!\w+-->).*?-->", "", "\n".join(pages), flags=re.DOTALL)
    code = "\n".join(
        line
        for name in scripts
        for line in client_script(name).splitlines()
        if not line.strip().startswith("//")
    )
    return html + "\n" + code


def _asserted_spellings() -> list[tuple[str, int, str]]:
    found = []
    for path in source_files("tests"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _ASSERTION.search(line)
            if match is None or not _PAGE_TARGET.search(match.group(3)):
                continue
            needle = match.group(2)
            if "\\" in needle:
                needle = needle.encode().decode("unicode_escape")
            found.append((path.name, number, needle))
    return found


def test_no_page_assertion_is_satisfied_by_a_comment():
    """A string the served page holds only in a comment implements nothing.

    An assertion that finds its string only inside an HTML comment passes,
    while the reader sees a page without those words.
    """
    served = "\n".join(PAGES) + "\n".join(client_script(name) for name in SCRIPTS)
    clean = _without_comments(PAGES, SCRIPTS)
    comment_only = [
        f"{name}:{number}: {needle!r}"
        for name, number, needle in _asserted_spellings()
        if needle in served and needle not in clean
    ]
    assert not comment_only, (
        f"these tests assert a spelling the served page holds only in a comment:"
        f" {comment_only}. Make the page say it, or assert what the page says."
    )


def test_the_spelling_scan_reads_a_real_population():
    assert len(_asserted_spellings()) > 20


# --- 3. A field no code reads -------------------------------------------------------

_MODEL_BASES = re.compile(r"BaseModel|dataclass|NamedTuple|TypedDict")


def _declared_fields() -> dict[str, tuple[Path, int]]:
    """Every field of every model, dataclass or named tuple, by ``Class.field``."""
    fields: dict[str, tuple[Path, int]] = {}
    for path in source_files("src", "evals", "webapp"):
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.ClassDef):
                continue
            shape = " ".join(
                ast.dump(item) for item in [*node.bases, *node.decorator_list]
            )
            if not _MODEL_BASES.search(shape):
                continue
            for item in node.body:
                if not isinstance(item, ast.AnnAssign):
                    continue
                if not isinstance(item.target, ast.Name):
                    continue
                name = item.target.id
                if name.startswith("_") or name == "model_config":
                    continue
                fields[f"{node.name}.{name}"] = (path, item.lineno)
    return fields


def _readers_corpus() -> str:
    """Every file that may read a field — except this one.

    A declaration here spells ``Class.field``, which the reader pattern would
    match, so the lint would read its own table as a reader and the rot check
    would fail on every declared row.
    """
    files = source_files(*SEARCHED, suffixes=(".py", ".js", ".html"))
    own = Path(__file__).resolve()
    return "\n".join(
        path.read_text(encoding="utf-8") for path in files if path.resolve() != own
    )


def _unread_fields() -> dict[str, str]:
    corpus = _readers_corpus()
    unread = {}
    for qualified, (path, line) in _declared_fields().items():
        name = qualified.split(".")[1]
        if not re.search(rf"\.{name}\b|\[\"{name}\"\]", corpus):
            unread[qualified] = f"{path.relative_to(REPO_ROOT)}:{line}"
    return unread


def test_every_model_field_has_a_reader():
    """A field the code sets and never reads records a promise nobody keeps.

    Every field the code sets must have a reader somewhere under
    :data:`SEARCHED`, or a declaration in :data:`DECLARED_FIELDS`.
    """
    unread = {
        qualified: where
        for qualified, where in _unread_fields().items()
        if qualified not in DECLARED_FIELDS
    }
    assert not unread, (
        f"these fields are read by nothing under {SEARCHED}: {unread}. Give the"
        " fact a reader, delete the field, or add it to DECLARED_FIELDS with the"
        " reason it is for a person or a consumer outside this repository."
    )


def test_the_field_declarations_do_not_rot():
    stale = sorted(set(DECLARED_FIELDS) - set(_unread_fields()))
    assert not stale, (
        f"these declared fields now have a reader, so the declaration excuses"
        f" nothing: {stale}. Remove the row."
    )


def test_every_field_declaration_gives_a_reason():
    thin = sorted(name for name, reason in DECLARED_FIELDS.items() if len(reason) < 40)
    assert not thin, f"these declarations need a real reason: {thin}"


def test_the_field_scan_reads_a_real_population():
    assert len(_declared_fields()) > 200


# --- One spelling of the tier that drives the gate ---------------------------


def _tier_comparisons(path: Path) -> list[int]:
    """Lines comparing something to the must-find tier as a bare string.

    An ``ast`` walk rather than a grep, because the question is about a
    **comparison** and not about the characters: a label a report prints and a
    key an artifact writes both hold the same words and neither decides
    anything.
    """
    return [
        node.lineno
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Compare)
        and any(isinstance(op, ast.Eq | ast.NotEq) for op in node.ops)
        for side in (node.left, *node.comparators)
        if isinstance(side, ast.Constant) and side.value == MUST_FIND
    ]


def test_the_must_find_tier_is_compared_through_one_name():
    """A rule with a reader per module is how the readers come to disagree.

    ``reference.MUST_FIND`` is the one spelling. A site that compares the
    literal stays right only for as long as nobody renames a tier.

    A **label** is not a comparison and is not in scope: three sites in
    ``pairing.py`` print the words as a column, driven by the ``must_find``
    property, and one of them prints ``should-find`` beside it — a phrase that
    is not a tier at all.
    """
    offenders = {
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in source_files("evals", "src", "webapp")
        for line in _tier_comparisons(path)
        if path != REPO_ROOT / "evals" / "harness" / "reference.py"
    }

    assert not offenders, (
        f"these compare the must-find tier as a bare string: {sorted(offenders)}."
        " Import MUST_FIND, or ask the object's own `must_find` property."
    )


def test_the_tier_comparison_scan_finds_one_when_there_is_one(tmp_path):
    """The positive control. The tree is clean, so a lint over it passes
    whether or not the scan works at all — a synthetic offender is the only
    thing that says the net has holes in the right size.

    It also pins what is **not** an offence: a label carrying the same words,
    and a comparison against the imported name.
    """
    module = tmp_path / "offender.py"
    module.write_text(
        "from evals.harness.reference import MUST_FIND\n"
        "def rule(row):\n"
        "    return row.tier == 'must-find'\n"
        "def allowed(row):\n"
        "    return row.tier == MUST_FIND\n"
        "LABEL = 'must-find'\n",
        encoding="utf-8",
    )

    assert _tier_comparisons(module) == [3]


# --- A message that tells its reader a word it already knew ------------------
#
# The fourth shape of the same class (the round over #917-#990): a refusal
# message that interpolates the very expression its own branch pinned to a
# literal. A message that prints `predicate.value` inside
# `if predicate.value == "reference":` always prints the same word. The fact
# the message carries is a constant, and the repair pass that reads it learns
# nothing from it.

#: Where a message is production output. Tests are excluded: a test may
#: deliberately build such a string to drive a scan.
MESSAGE_ROOTS = ("src", "evals", "webapp")


def _pinned_by(test: ast.expr) -> list[tuple[str, str, str]]:
    """Each ``x == "lit"`` or ``x in (...)`` an ``if`` test fixes, as dumps."""
    parts = (
        [test]
        if isinstance(test, ast.Compare)
        else [v for v in test.values if isinstance(v, ast.Compare)]
        if isinstance(test, ast.BoolOp)
        else []
    )
    fixed = []
    for compare in parts:
        if (
            len(compare.ops) == 1
            and isinstance(compare.ops[0], (ast.Eq, ast.In))
            and isinstance(
                compare.comparators[0], (ast.Constant, ast.Tuple, ast.List, ast.Set)
            )
            and isinstance(compare.left, (ast.Attribute, ast.Name))
        ):
            fixed.append(
                (
                    ast.dump(compare.left),
                    ast.unparse(compare.left),
                    ast.unparse(compare.comparators[0]),
                )
            )
    return fixed


def _constant_interpolations(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Every f-string field whose enclosing branch already fixed its value."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        fixed = _pinned_by(node.test)
        if not fixed:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.JoinedStr):
                continue
            for value in sub.values:
                if not isinstance(value, ast.FormattedValue):
                    continue
                for dump, expr, literal in fixed:
                    if ast.dump(value.value) == dump:
                        found.append((sub.lineno, expr, literal))
    return found


def test_no_message_interpolates_a_value_its_own_branch_pinned():
    """A message whose field is a constant tells its reader nothing."""
    found = []
    for path in source_files(*MESSAGE_ROOTS):
        for line, expr, literal in _constant_interpolations(parse(path)):
            found.append(
                f"{path.relative_to(REPO_ROOT)}:{line} interpolates {expr},"
                f" which that branch pinned to {literal}"
            )

    assert not found, (
        "these messages carry a field their own branch already fixed, so each"
        f" one always prints the same word: {found}. Name the thing the branch"
        " is about instead -- the referent's type, not the value kind."
    )


def test_the_constant_interpolation_scan_finds_one_when_there_is_one(tmp_path):
    """Positive control, in the shape of the #991 refusal message."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def drop(predicate, value):\n"
        '    if predicate.value == "reference":\n'
        "        return (\n"
        '            f"{value!r} names no {predicate.value} this predicate takes"\n'
        "        )\n"
        '    return ""\n',
        encoding="utf-8",
    )
    found = _constant_interpolations(ast.parse(probe.read_text(encoding="utf-8")))

    assert [(expr, literal) for _, expr, literal in found] == [
        ("predicate.value", "'reference'")
    ]


#: The vocabularies a module owns, against the module that owns each one. A
#: second site spelling the same members out is the fourth shape of this
#: module's class: not a fact with no reader, but a rule with two, where each
#: reader's own test agrees with it and neither moves when the rule does.
#:
#: See the checkpoint round over ``reviewed/2026-09-16b...main``. A module
#: that writes ``("consumed", "preserved")`` again beside
#: ``factbundle.LANDED`` would miss a sixth landing disposition.
#: Read from the owner rather than written out here, so this table is not
#: itself the second spelling it exists to forbid.
OWNED_VOCABULARIES: dict[str, frozenset[str]] = {
    "src/analysis_service/factbundle.py": LANDED,
    "src/analysis_service/claims.py": ATTRIBUTE_GROUNDS,
    "src/analysis_service/sources.py": FORMATTING_CATEGORIES,
    "evals/harness/roster.py": STANDINGS,
    "evals/harness/reference.py": frozenset(CASE_FILES),
}


#: The one site allowed to write a vocabulary out again, with the reason. A
#: declaration that stops being needed fails with everything else, because the
#: lint reports a site it cannot find.
DECLARED_RESPELLINGS: dict[str, str] = {
    "evals/harness/standings.py": (
        "SERIES writes the standings out in a published order: the artifact's"
        " series block carries it and evals/baselines/README.md is generated"
        " from it, so deriving it from an unordered set would rewrite a"
        " committed table. tests/test_evals_standings.py holds the table's"
        " union against the roster's set instead"
    ),
}


def _respelled(tree: ast.Module, members: frozenset[str]) -> list[int]:
    """Each line holding a literal collection whose strings are ``members``."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple | ast.List | ast.Set):
            continue
        written = {
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        if len(written) == len(node.elts) and written == members:
            found.append(node.lineno)
    return found


@pytest.mark.parametrize("owner", sorted(OWNED_VOCABULARIES))
def test_no_second_site_respells_an_owned_vocabulary(owner):
    """One rule, one reader, for the half a scan can decide.

    A caller that wants the set imports the name. A caller that writes the
    members out again is a reader the owner cannot move.
    """
    members = OWNED_VOCABULARIES[owner]
    # Production roots only. A test naming both members is data — a vote
    # fixture needs a standing to put in it — while a second *reader* in the
    # shipped tree is the thing that comes to disagree.
    found = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in source_files("src", "evals", "webapp")
        if path.relative_to(REPO_ROOT).as_posix()
        not in (owner.split(":")[0], *DECLARED_RESPELLINGS)
        for line in _respelled(parse(path), members)
    ]

    assert not found, (
        f"these sites spell out {sorted(members)}, which {owner} owns:"
        f" {found}. Import the name rather than repeating its members."
    )


def test_every_declared_respelling_is_still_one():
    """A declaration that stops being needed fails, as the field table's does."""
    stale = [
        path
        for path in DECLARED_RESPELLINGS
        if not any(
            _respelled(parse(REPO_ROOT / path), members)
            for members in OWNED_VOCABULARIES.values()
        )
    ]

    assert not stale, (
        f"these files no longer write an owned vocabulary out: {stale}."
        " Drop the declaration."
    )


def test_the_respelling_scan_finds_one_when_there_is_one(tmp_path):
    """Positive control: a module that spells the landed set out again."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        'def landed(row):\n    return row.disposition in ("consumed", "preserved")\n',
        encoding="utf-8",
    )

    found = _respelled(ast.parse(probe.read_text(encoding="utf-8")), LANDED)

    assert found == [2]


# --- One reader of "is this attribute unknown" -------------------------------
#
# Every reader asks whether an attribute is unknown through ``control_state``
# (#1289, Q4). A reader that compares with exactly ``unknown`` misses a
# qualified value such as ``unknown; the sources are silent``, which the
# analysis treats as open. Each reader's own test would agree with it.


def _qualifiable_attributes() -> frozenset[str]:
    """The attributes that can hold a qualified ``unknown``.

    A free-text field in some element type, less the fields the projection
    spells bare (:data:`~analysis_service.assertions.QUALIFIED_SPELLING`),
    where the gate refuses anything after the sentinel. A closed field holds
    the bare sentinel or nothing, so an exact comparison reads it correctly.
    """
    return frozenset(
        name
        for element_type in get_args(Element)
        for name, field in element_type.model_fields.items()
        if name in all_attribute_names()
        and field.annotation is str
        and QUALIFIED_SPELLING.get(name) != "bare"
    )


def _unknown_comparisons(path: Path, attributes: frozenset[str]) -> list[int]:
    """Lines comparing one of ``attributes`` to the ``unknown`` sentinel exactly."""

    def sentinel(node: ast.expr) -> bool:
        return (isinstance(node, ast.Name) and node.id == "UNKNOWN") or (
            isinstance(node, ast.Constant) and node.value == UNKNOWN
        )

    def attribute(node: ast.expr) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr in attributes
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return node.slice.value in attributes
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
        )

    return [
        node.lineno
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Compare)
        and any(isinstance(op, ast.Eq | ast.NotEq) for op in node.ops)
        and any(sentinel(side) for side in (node.left, *node.comparators))
        and any(attribute(side) for side in (node.left, *node.comparators))
    ]


def test_no_site_reads_an_open_attribute_by_exact_match():
    """``control_state`` is the one reader, and ``open_attribute`` asks it.

    An exact match misses a qualified ``unknown``, which the projection writes
    into every free-text field it qualifies.
    """
    attributes = _qualifiable_attributes()
    offenders = {
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in source_files("src", "evals", "webapp")
        for line in _unknown_comparisons(path, attributes)
    }

    assert not offenders, (
        f"these compare an attribute to 'unknown' exactly: {sorted(offenders)}."
        " Ask analysis.control_state, or open_facts.open_attribute."
    )


def test_the_qualifiable_set_is_the_free_text_fields():
    """Pins the derivation: a bare or closed field is out, a prose one is in."""
    attributes = _qualifiable_attributes()

    assert {"authentication", "encryption_in_transit"} <= attributes
    assert not {"trust_zone", "exposure", "kind"} & attributes


def test_the_unknown_comparison_scan_finds_one_when_there_is_one(tmp_path):
    """Positive control: an exact comparison with ``unknown``, and what is not one."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "UNKNOWN = 'unknown'\n"
        "def asked(element, field):\n"
        "    return getattr(element, field, None) == UNKNOWN\n"
        "def flow(f):\n"
        "    return f.encryption_in_transit != 'unknown'\n"
        "def zone(e):\n"
        "    return e.trust_zone == UNKNOWN\n"
        "def row(entry):\n"
        "    return entry.value == UNKNOWN\n",
        encoding="utf-8",
    )

    assert _unknown_comparisons(probe, _qualifiable_attributes()) == [3, 5]
