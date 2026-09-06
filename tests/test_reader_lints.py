"""Three readers a fact must have, and the sweeps that find one with none.

A defect class this repository keeps meeting is **a fact with no reader**: an
answer an endpoint gives that the page never reads, a field a model carries
that no code reads, and a string a test asserts that the served page holds
only inside a comment. Each one passes every test that shipped with it,
because the thing that should read the fact is the thing that was never
written. Found by hand in the review of ``reviewed/2026-09-09...main`` and by
script in the sweep that followed it, this module is those sweeps made a
check, so the class does not accumulate again.

Each lint reads the tree rather than a list somebody remembered, in the shape
``test_dead_code_lints.py`` uses: a reader is anything under
:data:`SEARCHED` that reaches the name, tests included. Where a fact is for a
person rather than for code — provenance, a payload consumer reads outside
this repository — it is declared in :data:`DECLARED_FIELDS` with the reason,
and a declaration that stops being needed fails too.

**The limit worth stating.** The answer-key lint holds a key read *anywhere*
in the page's script as read, because a grep cannot tell which response a
``d.state`` came from. A key one endpoint answers and another endpoint's
handler reads passes here. That is a smaller net than a per-endpoint one, and
still the net that caught ``written``, ``command``, ``paste``, ``carried`` and
``warnings``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from webapp import main, offline_sitting, review, sitting
from webapp.page import client_script

REPO_ROOT = Path(__file__).resolve().parents[1]
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


def _source_files(*roots: str, suffixes: tuple[str, ...] = (".py",)) -> list[Path]:
    return [
        path
        for root in roots
        for suffix in suffixes
        for path in sorted((REPO_ROOT / root).rglob(f"*{suffix}"))
        if "research" not in path.parts and ".venv" not in path.parts
    ]


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

    ``written``, ``command`` and ``paste`` rode on ``/api/stage`` for a month
    after the page stopped reading them, naming a command that did not exist.
    A draft that would not delete rode on ``/api/contribute`` as ``warnings``
    and reached no screen.
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
    for path in _source_files("tests"):
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

    ``_PAGE`` once ended with two HTML comments after ``</html>`` that existed
    so three assertions would keep passing after the page stopped saying those
    words. The assertion passed; the reader got a different page.
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
    for path in _source_files("src", "evals", "webapp"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
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
    files = _source_files(*SEARCHED, suffixes=(".py", ".js", ".html"))
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

    ``Draft.clone``'s own comment promised a warning nothing raised.
    ``Draft.recorded`` and ``Draft.unreviewed_entry`` were the removed record's
    fields, still accepted from every draft file. ``Store.held`` named a surface
    in a document nothing wrote.
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
