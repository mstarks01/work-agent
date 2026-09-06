"""Every place outside the registry that names a vendor, and why.

## What went wrong, and why nothing caught it

Nobody had ever read the ``vertex`` row as a subject.
[#607](https://github.com/mstarks01/work-agent/issues/607) listed six defects in
it, every one found in passing by a session charting a Bedrock map and looking
at something else, and the audit that answered it found more. Not one of them
raised. Each reported a smaller, plausible answer instead.

``SERVED_TRUST = "provider_reported"`` was true with one vendor and stayed
true-looking when a second arrived that echoes the request rather than naming
the build. ``_credential_vars`` branched on the credential mode and demanded a
Google file path, so a platform-identity deployment failed closed before the
adapter was built. ``_FORM_RULES["openai"]`` held the catch-all alone, so the
same identifier was legal on one vendor and refused on two others.

**A one-vendor assumption is vacuously correct when it is written and silently
wrong afterwards.** That is the same sentence
``tests/test_framework_neutrality.py`` opens with, about frameworks, and the
lesson reached ``CLAUDE.md`` as prose while the *check* was built for frameworks
only. Vendors got the sentence and not the mechanism. This module is the
mechanism.

## The shape that survived, and the shape that did not

Sorting the seven vendor defects by what they touched gives the same rule the
framework audit gave, with no exceptions:

* **A table keyed by vendor was always already correct.** ``VENDORS`` and
  ``REFERENCE_MODELS`` needed no change through the whole audit. A missing key
  raises at the first call, so the edit is forced.
* **A constant, a branch or a missing entry was always wrong.** The
  ``served_trust`` constant, the ``_credential_vars`` branch on mode, the
  redactor reading the wrong list, the workflow lane naming ``vertex``, and the
  ``_FORM_RULES`` entry with its key present and its value short — every gap was
  one of those four.

## What this module does, in three layers

Each layer catches a failure the others cannot.

**1. Completeness — did this vendor answer at all?** :func:`vendor_keyed_tables`
finds every module-level mapping keyed by a closed vendor vocabulary by reading
the modules, rather than by listing the tables somebody remembered. A table that
does not answer for every vendor fails, *including a table added tomorrow*. This
is ``CLAUDE.md``'s "check the table against its registry" made automatic, so
that a new table cannot fail as quietly as the branch it replaced.

**2. Declaration — is naming a vendor here right?** The literal and identifier
scans below, which are the framework module's shape. A name outside the registry
must say why it is there, as a property of the vendor rather than as its name.

**3. Property — is the answer right?** Completeness cannot see a wrong value:
``_FORM_RULES["openai"]`` had its key. Those tests live beside the rules they
check — ``test_vendors.py`` for the registry's own contract,
``test_identity.py`` for what the installed translator actually does, and
``test_conformance.py`` for the live lanes — because a property is best asserted
where a reader meets it.

The explicit per-table checks in ``test_vendors.py`` stay. Layer 1 is a **net
under** them, not a second copy of them: both read ``VENDOR_NAMES``, so they
cannot drift apart against separate expectations, and the explicit ones give a
better failure message for the tables we already know about.

## The limit worth stating

A vendor row makes claims about a **third party**, which a framework never does.
``served_trust`` is a claim about what litellm's transformation reads; whether
``gpt-4o`` is an alias is a claim about OpenAI's catalogue. Neither is decidable
from this repository's own tables. The first is checked by driving the installed
litellm in ``test_identity.py``; the second needed a live call and is recorded
beside the code in ``vendors.py`` because CI cannot reach it. Nothing here can
close that gap, and pretending otherwise would be the failure this module exists
to prevent.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import re
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import get_args

import pytest

# Imported first so the model-cost map is pinned before anything reaches
# litellm; `vendor_keyed_tables` imports every shipped module.
from analysis_service import model_gate  # noqa: F401
from analysis_service.vendors import (
    CREDENTIAL_MODES,
    VENDOR_NAMES,
    VENDORS,
    CredentialMode,
    Vendor,
    VendorName,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCHED = ("src", "evals", "webapp")

#: The closed vocabularies a table may be keyed by. Both are per-vendor
#: alphabets: a vendor's name, and the credential mechanisms a vendor row can
#: declare. A table keyed by either has to answer for all of it.
VOCABULARIES: tuple[tuple[str, frozenset], ...] = (
    ("VENDOR_NAMES", frozenset(VENDOR_NAMES)),
    ("CredentialMode", frozenset(CredentialMode)),
)

#: Every vendor name, as one alternation, built from the registry. Written down
#: once and read by both scans below: a hand-kept list is the shape this module
#: exists to catch, and a vendor missing from it would be scanned for by
#: nothing at all.
_ANY_VENDOR = "|".join(re.escape(name) for name in VENDOR_NAMES)

#: A string literal naming a vendor. The name may sit anywhere inside the
#: string and in any case, which is the blind spot the framework scan had to
#: close twice: ``"an OpenAI reasoning model"`` in an error message names a
#: vendor as surely as ``"openai"`` does.
LITERAL = re.compile(rf'"[^"]*\b(?:{_ANY_VENDOR})\b[^"]*"', re.IGNORECASE)

#: A vendor's name inside an identifier, which :data:`LITERAL` cannot see.
#: ``openai_reasoning_model`` is a vendor's name on a rule that runs for every
#: vendor, and it is not a string literal.
IN_WORD = re.compile(_ANY_VENDOR, re.IGNORECASE)

#: Where a vendor literal is allowed, and why. Two readings live here and the
#: reason has to say which:
#:
#: **"a table keyed by vendor"** — self-completing, and layer 1 proves it stays
#: that way. A vendor added to ``VENDORS`` and missing here fails.
#:
#: **"this names one vendor's own fact"** — a variable only that vendor reads,
#: or an error naming the family a rule matched. Legitimate, and the thing to
#: check when a row lands is whether the *dispatch* is a table or a branch.
#:
#: An entry that is neither is a gap. Say so in the reason rather than filing it
#: beside the legitimate ones.
DECLARED: dict[str, str] = {
    "src/analysis_service/vendors.py": (
        "The registry. `VendorName` is the closed type every other module"
        " reads, so this is the one place the names are spelled at all."
        " Its six tables — VENDORS, CREDENTIAL_MODES, _CREDENTIAL_VARS,"
        " _MODE_KWARGS, _FORM_RULES and VENDOR_SDKS — are keyed by vendor and"
        " self-completing. VERTEX_PROJECT_VAR, VERTEX_LOCATION_VAR and"
        " BEDROCK_REGION_VAR name one vendor's own addressing config, which no"
        " other vendor reads."
    ),
    "src/analysis_service/conformance.py": (
        "REFERENCE_MODELS is a table keyed by vendor: the pair the offline"
        " capability matrix profiles for each row. A vendor missing from it"
        " has no profiled pair, which the matrix fails on rather than"
        " skipping quietly."
    ),
    "src/analysis_service/binding.py": (
        "An error message naming the model family a rule matched, not a vendor"
        " selection. `openai_reasoning_model` is keyed on the identifier and"
        " runs under every vendor — see OPEN_BY_DECISION — so the message has"
        " to name the family it recognised or an operator cannot tell why"
        " their temperature was refused."
    ),
}

#: A vendor's name on a thing that serves every vendor, kept deliberately, with
#: the reason. The framework module's ``Engine`` and ``analysis_pipeline`` are
#: the same shape: a name that reads as a selection and is not one.
OPEN_BY_DECISION: dict[str, str] = {
    "openai_reasoning_model": (
        "Names the model **family**, not the vendor that serves it. OpenAI"
        " publishes the o-series and GPT-5-and-later, and their sampling"
        " surface is a property of those weights wherever they are reached"
        " from — the function's own docstring records that it refuses to key"
        " on the vendor, because a reasoning identifier arriving through a"
        " gateway under another vendor would otherwise pass. Renaming it to"
        " hide the family would make the rule harder to read, not more"
        " neutral."
    ),
    "VERTEX_PROJECT_VAR": (
        "One vendor's own addressing config. Google is the only provider that"
        " scopes a request to a project, so there is no neutral spelling to"
        " prefer and nothing for another vendor to answer here."
    ),
    "VERTEX_LOCATION_VAR": (
        "The other half of the same addressing pair, and the value #601 was"
        " about: it is required, it is not a credential, and the redactor that"
        " read it as one substituted a region out of provider error text."
    ),
    "BEDROCK_REGION_VAR": (
        "One vendor's own addressing config, the same reading as the Vertex"
        " pair above. AWS scopes a request to a region and the other providers"
        " do not, so there is no neutral spelling to prefer and nothing for"
        " another vendor to answer here. It is required under both of this"
        " vendor's credential modes and is a credential under neither."
    ),
}


# --- Layer 1: completeness, over tables nobody had to remember to list -------


def _shipped_modules() -> list[str]:
    """Every importable module under the searched roots.

    Read from the filesystem rather than from a list, for the same reason the
    tables are: a module added tomorrow is covered without an edit.
    """
    names = []
    for root, package in (("src", "analysis_service"), ("evals", "evals")):
        base = REPO_ROOT / root / package.replace(".", "/")
        if not base.is_dir():
            continue
        names.append(package)
        for info in pkgutil.walk_packages([str(base)], prefix=f"{package}."):
            names.append(info.name)
    return sorted(set(names))


def _vendors_in(key: object, vocabulary: frozenset) -> set:
    """Which members of ``vocabulary`` a mapping key names.

    A key is either a member itself, or a tuple carrying one —
    ``_CREDENTIAL_VARS`` is keyed by ``(vendor, mode)``, and a table keyed by a
    pair has to answer for every vendor in it just as a flat one does.
    """
    if isinstance(key, tuple):
        return {part for part in key if part in vocabulary}
    return {key} if key in vocabulary else set()


def _assigned_at_module_level(path: Path) -> set[str]:
    """Every name this module *defines* at module level.

    Read from the source rather than from ``vars()``, which cannot tell a table
    a module defines from one it imports. ``model_tiers`` re-exports
    ``CREDENTIAL_MODES``, and reporting one table under two names would read as
    two broken tables in a failure message.
    """
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def unimportable_modules() -> dict[str, str]:
    """Every shipped module the scan could not import, and why.

    Reported rather than skipped. A module that fails to import is a module
    :func:`vendor_keyed_tables` reads no table out of, so swallowing the error
    would shrink this file's coverage without shrinking its green tick.
    """
    broken = {}
    for module_name in _shipped_modules():
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - reported, not handled
            broken[module_name] = f"{type(exc).__name__}: {exc}"
    return broken


def vendor_keyed_tables() -> dict[str, tuple[str, set]]:
    """Every module-level mapping keyed by a closed vendor vocabulary.

    Returns ``qualified name -> (vocabulary name, the members it answers for)``.
    Found by importing the modules and reading their attributes, so a table
    added tomorrow is checked without anybody remembering this file exists.

    A mapping qualifies when **any** key names a member. That is deliberate: a
    table answering for one vendor is exactly the half-done shape this module
    exists to catch, and requiring it to answer for all of them first would let
    it through.
    """
    found: dict[str, tuple[str, set]] = {}
    for module_name in _shipped_modules():
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001,S112 - `unimportable_modules` reports it
            continue
        source = getattr(module, "__file__", None)
        if source is None:
            continue
        defined = _assigned_at_module_level(Path(source))
        for attribute, value in vars(module).items():
            if attribute not in defined or not isinstance(value, Mapping) or not value:
                continue
            for vocabulary_name, vocabulary in VOCABULARIES:
                answered: set = set()
                for key in value:
                    answered |= _vendors_in(key, vocabulary)
                if answered:
                    found[f"{module_name}.{attribute}"] = (vocabulary_name, answered)
                    break
    return found


@pytest.fixture(scope="module")
def tables():
    return vendor_keyed_tables()


def test_every_shipped_module_imports():
    """Coverage this scan claims, it has to actually have."""
    broken = unimportable_modules()
    assert not broken, (
        f"these modules could not be imported, so no table in them was"
        f" checked: {broken}"
    )


def test_the_scan_finds_the_tables_we_already_know_about(tables):
    """A scanner that discovers nothing passes everything.

    The failure one level up from the one this module fixes: a check that reads
    a registry is worth what its reader finds, and a reflective reader that
    silently matched no table would report a clean tree forever. These five are
    the tables the vertex audit worked through, so the scan has to see them.
    """
    expected = {
        "analysis_service.vendors.VENDORS",
        "analysis_service.vendors.CREDENTIAL_MODES",
        "analysis_service.vendors._CREDENTIAL_VARS",
        "analysis_service.vendors._MODE_KWARGS",
        "analysis_service.vendors._FORM_RULES",
        "analysis_service.vendors.VENDOR_SDKS",
        "analysis_service.conformance.REFERENCE_MODELS",
    }
    assert expected <= set(tables), (
        f"the scan no longer finds: {sorted(expected - set(tables))}."
        " Either the table moved, or `vendor_keyed_tables` stopped seeing its"
        " shape — and a scan that sees nothing reports a clean tree forever."
    )


def test_every_vendor_keyed_table_answers_for_every_vendor(tables):
    """The rule `CLAUDE.md` states, applied to tables nobody listed here.

    A table keyed by vendor is self-completing only while something compares it
    to the registry. This is that comparison, for every table at once, so a
    vendor row added tomorrow cannot leave one half-filled.
    """
    incomplete = {}
    for name, (vocabulary_name, answered) in tables.items():
        whole = dict(VOCABULARIES)[vocabulary_name]
        missing = whole - answered
        if missing:
            incomplete[name] = sorted(str(member) for member in missing)

    assert not incomplete, (
        f"these tables are keyed by a vendor vocabulary and do not answer for"
        f" all of it: {incomplete}. Add the missing entries. A table with a"
        " vendor missing is the shape that let `_FORM_RULES` refuse an"
        " identifier on two vendors and accept it on the third."
    )


# --- The registry's own closed shapes ----------------------------------------


def test_the_closed_type_and_the_name_tuple_agree():
    """Two spellings of the registry's vocabulary, checked against each other.

    `VendorName` is what every annotation reads and `VENDOR_NAMES` is what
    every loop reads. A vendor added to one and not the other gives the type
    checker and the test suite different ideas of how many vendors exist.
    """
    assert get_args(VendorName) == VENDOR_NAMES
    assert set(VENDORS) == set(VENDOR_NAMES)


def test_no_vendor_field_has_a_default():
    """A default is how a new vendor row stays silent about a fact.

    This is the mechanism that fixes the `served_trust` class of defect rather
    than the instance. It was a module constant, so every vendor inherited one
    answer and nobody was asked. Making it a required field means a fourth row
    cannot construct without stating its own — and a field that later grows a
    default would quietly restore the constant.
    """
    defaulted = [
        field.name
        for field in fields(Vendor)
        if field.default is not field.default_factory  # both MISSING when required
    ]
    assert not defaulted, (
        f"these Vendor fields have a default: {defaulted}. A default answers"
        " for a vendor nobody asked, which is what the `served_trust` constant"
        " did for two rows. Make it required, or move it to a table this"
        " module's completeness check can see."
    )


def test_every_vendor_declares_at_least_one_credential_mode():
    # ``.get``, so a row missing from the table is reported here rather than
    # raising. This module has to survive a half-built registry: that is
    # precisely when its messages are the ones somebody needs.
    empty = sorted(name for name in VENDOR_NAMES if not CREDENTIAL_MODES.get(name))
    assert not empty, (
        f"these vendors declare no credential mode: {empty}. A row that"
        " authenticates in no way cannot be built, and one absent from"
        " CREDENTIAL_MODES raises at `Vendor.credential_modes` on its first"
        " use rather than here."
    )


# --- Layer 2: declaration ----------------------------------------------------


def _named_identifiers(tree: ast.AST) -> set[str]:
    """Every identifier in one module carrying a vendor's name."""
    words: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.add(node.id)
        elif isinstance(node, ast.Attribute):
            words.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            words.add(node.name)
            arguments = getattr(node, "args", None)
            if arguments is not None:
                words.update(argument.arg for argument in arguments.args)
    return {word for word in words if IN_WORD.search(word)}


def _searched_files():
    for root in SEARCHED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            yield path.relative_to(REPO_ROOT).as_posix(), path


def vendor_literals() -> dict[str, list[tuple[int, str]]]:
    """Every non-test vendor literal, by file.

    Comment lines are skipped: a comment naming a vendor is prose about one,
    and the thing this scan is for is a name the code acts on.
    """
    found: dict[str, list[tuple[int, str]]] = {}
    for relative, path in _searched_files():
        hits = [
            (number, line.strip())
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            if LITERAL.search(line) and not line.strip().startswith("#")
        ]
        if hits:
            found[relative] = hits
    return found


def vendor_named_identifiers() -> dict[str, list[str]]:
    """Every non-test module with an identifier carrying a vendor's name."""
    found: dict[str, list[str]] = {}
    for relative, path in _searched_files():
        words = _named_identifiers(ast.parse(path.read_text(encoding="utf-8")))
        if words:
            found[relative] = sorted(words)
    return found


@pytest.fixture(scope="module")
def literals():
    return vendor_literals()


def test_a_new_vendor_literal_is_declared(literals):
    undeclared = sorted(set(literals) - set(DECLARED))
    assert not undeclared, (
        f"these files name a vendor and nothing says why: {undeclared}."
        " Prefer a table keyed by vendor — it raises when a row is missing,"
        " where a constant or a branch quietly does less. If the name is"
        " right, add the file to DECLARED with which of the two readings"
        " applies."
    )


def test_a_vendor_named_thing_is_declared_or_open():
    """A value may not carry a vendor's name unexplained.

    The half :data:`LITERAL` is blind to, and the half that matters most here:
    a rule named for a vendor invites the next reader to key it on one, which
    is how a family rule came to sit in a vendor-keyed table with an entry
    missing.
    """
    unexplained = {
        name: sorted(word for word in words if word not in OPEN_BY_DECISION)
        for name, words in vendor_named_identifiers().items()
        if name not in DECLARED
    }
    unexplained = {name: words for name, words in unexplained.items() if words}

    assert not unexplained, (
        f"these name a vendor on a thing that serves every vendor:"
        f" {unexplained}. Rename it, or add the file to DECLARED if the code"
        " really is that vendor's, or add the name to OPEN_BY_DECISION with"
        " the reason it stays."
    )


def test_the_declaration_does_not_rot(literals):
    stale = sorted(set(DECLARED) - set(literals))
    assert not stale, (
        f"these files no longer name a vendor and are still declared:"
        f" {stale}. Remove them — a declaration nobody can act on excuses the"
        " next name that spells it."
    )


def test_every_open_decision_is_still_open():
    live: set[str] = set()
    for words in vendor_named_identifiers().values():
        live.update(words)
    stale = sorted(name for name in OPEN_BY_DECISION if name not in live)
    assert not stale, (
        f"these names are gone and are still recorded as open: {stale}. Remove the row."
    )


@pytest.mark.parametrize(
    "declaration", [DECLARED, OPEN_BY_DECISION], ids=["file", "name"]
)
def test_every_declaration_gives_a_reason(declaration):
    """An entry with no reason excuses nothing and teaches the next reader nothing."""
    thin = sorted(name for name, reason in declaration.items() if len(reason) < 40)
    assert not thin, f"these declarations need a real reason: {thin}"
