"""Every licence obligation this repo carries, checked rather than promised.

## What this guards

Two obligations, and both of them fail silently.

**Attribution.** OWASP publishes ASVS 5.0.0 under CC BY-SA 4.0, and this repo
reproduces its 345 requirement sentences. ``NOTICE`` says which files those are.
Nothing in the tree makes that visible: a requirement sentence reads like any
other prompt text, so a contributor who copies one into a new lane, a fixture or
a doc creates an unattributed adaptation, and the wheel still builds and the
suite still passes.

**ShareAlike.** The same 18 files carry CC BY-SA 4.0 rather than the repo's
Apache-2.0. A file that quietly acquires that text acquires the ShareAlike
condition with it, and ``NOTICE`` no longer describes the distribution.

So the register below is the record, and these lints are what keep it true. The
scan is the important one: it does not ask anybody to remember the rule. It reads
the governed files, fingerprints every fifteen-word run in them, and looks for
those words anywhere else in the tree.

## Why a register rather than a reading of NOTICE

``NOTICE`` is prose for a human who wants to know what they received. Parsing it
would make the check as loose as the prose. :data:`THIRD_PARTY` is the fact in a
form code reads, and :func:`test_notice_names_every_governed_path` makes the
prose answer to it — so a lane added tomorrow fails here until ``NOTICE`` names
its file, rather than drifting the way a guide's sentence drifts.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import pytest

from analysis_service.frameworks import CONTENT_LICENSE

REPO_ROOT = Path(__file__).resolve().parents[1]


def _unpunctuated(text: str) -> str:
    """``text`` with the separators that SPDX and prose disagree about removed."""
    return text.replace("-", "").replace(" ", "").lower()


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


#: What this repo licenses its own work under, read from the one place that
#: decides it rather than repeated here.
REPO_LICENSE = _pyproject()["project"]["license"]

#: Directories with nothing this repo authored or ships.
SKIPPED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
    }
)

#: How many consecutive words identify where a passage came from.
#:
#: The comparison is over words rather than characters, and that is the whole
#: design. A requirement sentence arrives in ``catalog.json`` wrapped in JSON
#: quotes and in a lane file behind ``- **V1.1.1** (L2) — ``, so a check that
#: matched punctuation would miss the same sentence pasted anywhere a third way
#: -- which is precisely the case worth catching, because a contributor copies
#: the words and retypes the formatting.
#:
#: Fifteen words. Two files in this repo share fifteen consecutive words only by
#: copying; the tree scans clean at this length, and the shortest ASVS
#: requirement is longer than it.
FINGERPRINT_WORDS = 15

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ThirdPartyWork:
    """One upstream work this repo carries text from.

    ``upstream_license``
        The SPDX identifier the upstream project publishes under. This is what
        obliges us, and what ``NOTICE`` must name.
    ``files_carry``
        The SPDX identifier the files in *this* repo carry. It differs from
        ``upstream_license`` exactly when the upstream licence permits an
        adaptation under other terms: an attribution-only licence does, and a
        ShareAlike licence does not. Naming both is what makes that difference a
        stated decision rather than an oversight.
    ``governed``
        Repo-relative paths, directories or glob patterns holding the text.
    ``upstream_text``
        Reads back the upstream author's own words, or ``None`` where this repo
        holds none of them verbatim. The scan fingerprints *this* rather than the
        governed files, and the difference matters: a governed file mixes
        upstream sentences with prose written here, and only the upstream half
        carries an obligation. ``roster.py`` builds a lane file's own heading
        sentence, which is duplication of this project's words and nobody's
        business but ours.

        A reader rather than a schema, because the shape of a catalog is the
        upstream project's choice and the next one will not be ``requirements``
        and ``text``.
    """

    upstream_license: str
    files_carry: str
    governed: tuple[str, ...]
    upstream_text: Callable[[], tuple[str, ...]] | None

    def paths(self) -> frozenset[Path]:
        """Every file this work governs, with directories and globs expanded."""
        found: set[Path] = set()
        for pattern in self.governed:
            for match in sorted(REPO_ROOT.glob(pattern)):
                if match.is_dir():
                    found.update(p for p in match.rglob("*") if p.is_file())
                elif match.is_file():
                    found.add(match)
        return frozenset(found)


ASVS_CATALOG = "src/analysis_service/frameworks/asvs/catalog.json"


def _asvs_requirements() -> tuple[str, ...]:
    """The 345 ASVS 5.0.0 requirement sentences, as OWASP wrote them."""
    catalog = json.loads((REPO_ROOT / ASVS_CATALOG).read_text())
    return tuple(requirement["text"] for requirement in catalog["requirements"])


#: Every third-party work whose text this repo carries, keyed as ``NOTICE``
#: names it.
#:
#: An entry here is a distribution obligation, never a citation. STRIDE's lanes
#: point at OWASP identifiers such as ``A01`` and ``ASI08`` and are absent for
#: that reason: a short identifier is not the expression it points at, so no
#: licence follows it. The test is whether a reader receives the upstream
#: author's words, not whether the upstream project is mentioned.
THIRD_PARTY: dict[str, ThirdPartyWork] = {
    "OWASP Application Security Verification Standard (ASVS) 5.0.0": ThirdPartyWork(
        upstream_license="CC-BY-SA-4.0",
        # ShareAlike, so the adaptation cannot be relicensed and these files do
        # not take the repo's Apache-2.0.
        files_carry="CC-BY-SA-4.0",
        governed=(
            ASVS_CATALOG,
            "frameworks/asvs/lanes/*/skill.md",
        ),
        upstream_text=_asvs_requirements,
    ),
    "OWASP Threat Model Cookbook": ThirdPartyWork(
        upstream_license="CC-BY-4.0",
        # Attribution only, which permits an adaptation under other terms, so
        # these cases take the repo licence and keep the attribution.
        files_carry="Apache-2.0",
        governed=(
            "evals/corpus/05-cookbook-queue-webapp",
            "evals/corpus/06-cookbook-online-game",
            "evals/corpus/09-cookbook-sokify-retail",
            "evals/corpus/10-cookbook-generic-cms",
        ),
        # Every case is a rewrite in this project's words. The obligation is to
        # credit the model that was rewritten, which NOTICE and each case's
        # `provenance` both do; no upstream sentence survives to be found.
        upstream_text=None,
    ),
}

#: SPDX fragments that put a condition on the code this repo distributes.
#:
#: MPL-2.0 is deliberately absent. Its copyleft is per-file: an unmodified MPL
#: file may travel inside a larger work under other terms, which is what a
#: dependency is. The identifiers here reach further than the file, so one of
#: them in the tree is a decision rather than a dependency update.
COPYLEFT = ("AGPL", "GPL", "SSPL", "EUPL", "OSL-", "CPAL", "CDDL")

#: A locked dependency under a copyleft licence, and why it is carried anyway.
#:
#: **Empty, and that is the state rather than an omission.** Nothing this repo
#: locks is copyleft today. A legitimate entry exists -- a development tool is
#: run rather than distributed, so its licence puts no condition on the wheel --
#: but it must be written down, because the check cannot see which group a
#: distribution arrived in.
DECLARED_COPYLEFT: dict[str, str] = {}


def fingerprints(text: str) -> set[tuple[str, ...]]:
    """Every run of :data:`FINGERPRINT_WORDS` words in ``text``.

    Punctuation, case, markup and line breaks are all dropped first, so the same
    passage fingerprints identically whether it arrives as a JSON string value,
    a markdown bullet or a docstring.
    """
    words = _WORD.findall(text.lower())
    return {
        tuple(words[start : start + FINGERPRINT_WORDS])
        for start in range(len(words) - FINGERPRINT_WORDS + 1)
    }


def leaks(
    root: Path, governed: frozenset[Path], protected: set[tuple[str, ...]]
) -> dict[Path, str]:
    """Ungoverned files under ``root`` holding a protected passage.

    Returns one offending passage per file, because the fix is the same whether
    a file took one sentence or forty.
    """
    found: dict[Path, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or SKIPPED_DIRS.intersection(path.parts):
            continue
        if path in governed:
            continue
        shared = fingerprints(path.read_text(errors="ignore")) & protected
        if shared:
            found[path] = " ".join(min(shared))
    return found


@pytest.fixture(scope="module")
def governed() -> frozenset[Path]:
    return frozenset().union(*(work.paths() for work in THIRD_PARTY.values()))


@pytest.fixture(scope="module")
def upstream_words() -> set[tuple[str, ...]]:
    """Fingerprints of every upstream sentence this repo holds verbatim.

    This is what leaks, and it is narrower than the governed files on purpose. A
    governed file mixes the upstream author's sentences with prose written here,
    and only the first half travels with an obligation.
    """
    return {
        fingerprint
        for work in THIRD_PARTY.values()
        if work.upstream_text is not None
        for sentence in work.upstream_text()
        for fingerprint in fingerprints(sentence)
    }


@pytest.mark.parametrize("name", sorted(THIRD_PARTY), ids=lambda name: name.split()[1])
def test_every_governed_path_exists(name):
    """A rename makes NOTICE name a file the distribution does not carry.

    Nothing else notices. The attribution stays in the wheel, pointing at a path
    that resolves to nothing, which is the same as no attribution to a reader
    who tries to check it.
    """
    work = THIRD_PARTY[name]
    empty = [pattern for pattern in work.governed if not list(REPO_ROOT.glob(pattern))]

    assert not empty, (
        f"{name} declares {empty}, which matches no file. Either the text moved"
        " -- update THIRD_PARTY and NOTICE together -- or it is gone, and the"
        " whole entry goes with it."
    )


def test_no_upstream_sentence_appears_in_an_ungoverned_file(governed, upstream_words):
    """The check that does not rely on anybody remembering the rule.

    A requirement sentence copied into a new lane, a test fixture, a doc or a
    prompt is an adaptation of a ShareAlike work sitting in an Apache-2.0 file.
    It reads like ordinary prose, so review does not catch it and neither does
    any other test in this suite.
    """
    offenders = leaks(REPO_ROOT, governed, upstream_words)

    formatted = "\n".join(
        f"  {path.relative_to(REPO_ROOT)}: {passage}"
        for path, passage in offenders.items()
    )
    assert not offenders, (
        "these files hold text a third-party licence governs, and NOTICE does"
        f" not say so:\n{formatted}\n"
        "Either write the passage in your own words, or add the file to"
        " THIRD_PARTY and NOTICE and accept the upstream licence on it."
    )


def test_the_scan_catches_a_sentence_pasted_in_another_format(tmp_path):
    """Positive control: a clean tree and a broken scan look identical.

    Every other assertion here passes when :func:`leaks` returns nothing, so a
    scan that always returned nothing would leave the suite green and the
    obligation unmet. An earlier version of this module did exactly that -- it
    compared punctuation, and the planted sentence went unnoticed because the
    file that took it wrote a markdown bullet where the catalog wrote a JSON
    string. So the plant here is deliberately reformatted.
    """
    borrowed = (
        "Verify that the application encodes every value it writes into a"
        " response for the context that value is written into."
    )
    (tmp_path / "copied.md").write_text(
        f"## A heading\n\n- **V1.2.3** (L1) — {borrowed.upper()}\n"
    )
    (tmp_path / "clean.md").write_text("A file that borrows nothing at all.")

    found = leaks(tmp_path, frozenset(), fingerprints(borrowed))

    assert list(found) == [tmp_path / "copied.md"]


def test_a_governed_file_is_not_reported_against_itself(tmp_path):
    """The files that hold the upstream text are where it belongs."""
    borrowed = (
        "Verify that the application encodes every value it writes into a"
        " response for the context that value is written into."
    )
    source = tmp_path / "catalog.json"
    source.write_text(f'{{"text": "{borrowed}"}}')

    assert not leaks(tmp_path, frozenset({source}), fingerprints(borrowed))


@pytest.mark.parametrize("name", sorted(THIRD_PARTY), ids=lambda name: name.split()[1])
def test_notice_names_every_governed_path(name):
    """NOTICE answers to the register, so its prose cannot drift.

    A new ASVS lane matches the declared glob the moment it lands, so it becomes
    governed without anybody deciding to attribute it. This is what forces the
    decision: the file is governed, NOTICE does not name it, and the lint says
    so.

    A pattern that resolves to a directory is checked as the directory, not as
    its contents. ``NOTICE`` names an eval case by its case directory, and a
    reader who wants to know what they received is served better by that than by
    the eleven files under it.
    """
    notice = (REPO_ROOT / "NOTICE").read_text()
    unnamed = sorted(
        relative
        for pattern in THIRD_PARTY[name].governed
        for match in REPO_ROOT.glob(pattern)
        if (relative := str(match.relative_to(REPO_ROOT))) not in notice
    )

    assert not unnamed, f"governed by {name} and absent from NOTICE: {unnamed}"


@pytest.mark.parametrize("name", sorted(THIRD_PARTY), ids=lambda name: name.split()[1])
def test_notice_names_every_work_and_its_upstream_license(name):
    """The two facts a licence obliges us to pass on: who wrote it, and terms."""
    notice = (REPO_ROOT / "NOTICE").read_text()
    work = THIRD_PARTY[name]

    assert name in notice, f"NOTICE does not name {name}"
    # NOTICE spells a licence the way a human reads it -- "CC BY-SA 4.0" for
    # CC-BY-SA-4.0, "CC BY 4.0" for CC-BY-4.0 -- so compare with the separators
    # taken out rather than demanding the SPDX punctuation in prose.
    assert _unpunctuated(work.upstream_license) in _unpunctuated(notice), (
        f"NOTICE names {name} without its licence, {work.upstream_license}."
    )


def test_every_package_carrying_foreign_text_agrees_with_the_register():
    """``CONTENT_LICENSE`` and this register describe one fact from two sides.

    The table answers "what licence does this package's text carry"; the register
    answers "which files, and on whose authority". They disagree only by mistake,
    and a disagreement is how a package ends up declared one way and attributed
    another.
    """
    from_register = {
        str(path.relative_to(REPO_ROOT)): work.files_carry
        for work in THIRD_PARTY.values()
        for path in work.paths()
    }

    for package, declared in CONTENT_LICENSE.items():
        governed_here = {
            license_id
            for path, license_id in from_register.items()
            if f"/{package}/" in path or f"frameworks/{package}" in path
        }
        if declared == "Apache-2.0":
            assert not governed_here - {"Apache-2.0"}, (
                f"{package} declares Apache-2.0 while the register puts"
                f" {governed_here} on files inside it."
            )
        else:
            assert governed_here == {declared}, (
                f"{package} declares {declared} and the register puts"
                f" {governed_here or 'nothing'} on files inside it."
            )


def test_every_corpus_case_from_an_external_source_names_its_license():
    """A converted case carries an obligation its author is the last to know.

    ``provenance`` is where a case says where it came from. A case that names an
    upstream project and no licence has recorded the interesting half and
    dropped the half that binds the distribution.
    """
    silent = []
    for case in sorted(REPO_ROOT.glob("evals/corpus/*/case.json")):
        provenance = json.loads(case.read_text())["provenance"]
        if "http" not in provenance:
            continue
        if not re.search(
            r"CC[- ]BY|Apache|MIT|BSD|public domain", provenance, re.IGNORECASE
        ):
            silent.append(case.parent.name)

    assert not silent, (
        f"{silent} name an upstream source and no licence. State the licence in"
        " provenance, and add the case to THIRD_PARTY and NOTICE if the licence"
        " asks for attribution."
    )


def _locked_packages() -> list[str]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    return sorted(
        package["name"]
        for package in lock["package"]
        if package["name"] != "analysis-service"
    )


def _declared_license(name: str) -> str:
    """Everything a distribution says about its licence, as one string."""
    try:
        metadata = distribution(name).metadata
    except PackageNotFoundError:
        return ""
    # ``get_all`` for every field, including the single-valued ones: the
    # ``PackageMetadata`` protocol declares it, while ``get`` is an
    # implementation detail of the email.message backing it.
    fields = ("License-Expression", "License", "Classifier")
    return " ".join(
        value
        for field in fields
        for value in metadata.get_all(field) or []
        if field != "Classifier" or value.startswith("License ::")
    )


def test_no_locked_dependency_is_copyleft():
    """A copyleft dependency puts a condition on the wheel, silently.

    ``pip-audit`` reads these same distributions and says nothing about their
    licences, so nothing else in this repo would notice. The check is over
    ``uv.lock`` rather than the live environment, because the lock is what a
    build resolves and a developer's virtual environment may hold anything.
    """
    offenders = {
        name: declared
        for name in _locked_packages()
        if name not in DECLARED_COPYLEFT
        and any(token in (declared := _declared_license(name)) for token in COPYLEFT)
    }

    assert not offenders, (
        f"copyleft in the locked dependencies: {offenders}. If the distribution"
        " is a development tool it is run rather than shipped, so declare it in"
        " DECLARED_COPYLEFT with that reason. If the wheel imports it, the"
        " condition reaches this project's users and the dependency is a"
        " licensing decision rather than a dependency update."
    )


def _is_installed(name: str) -> bool:
    try:
        distribution(name)
    except PackageNotFoundError:
        return False
    return True


def test_every_locked_dependency_states_a_license():
    """An unstated licence is not permission, so it cannot be assumed to be one."""
    silent = [
        name
        for name in _locked_packages()
        if _is_installed(name) and not _declared_license(name).strip()
    ]

    assert not silent, f"locked and state no licence: {silent}"


def test_the_wheel_ships_every_license_text_it_names():
    """``license-files`` is what puts the attribution in front of an integrator.

    A path here that does not resolve drops silently out of the built wheel, and
    the distribution then reproduces ASVS with no CC BY-SA text beside it.
    """
    named = _pyproject()["project"]["license-files"]
    missing = [path for path in named if not (REPO_ROOT / path).is_file()]

    assert not missing, f"license-files names {missing}, which do not exist"
    assert "NOTICE" in named, "the attribution file must reach the wheel"
