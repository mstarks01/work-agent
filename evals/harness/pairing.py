"""The reading view behind one applicability disagreement (#447).

A score says a case expected a ruling on 17 requirements and the run ruled on
64. It cannot say which side is wrong, because that is a judgement about what
"this requirement applies" means for a system of this shape, and only a person
makes it. What a person needs is the two sides beside each other: every
requirement the run applied that the case did not expect, with the standard's
own text and the argument the run made for it, and every requirement the case
expected that the run did not deliver, with the reason it went missing.

Nothing here scores anything. :mod:`evals.harness.applicability` owns the four
cells and this module reads them, so a pairing can never disagree with the
number it is helping somebody explain.

Nothing here rules on anything either. The claim's own argument is carried
through verbatim for a reader to weigh. No agent marks a requirement right or
wrong, because the whole point of the sitting is that no agent has read this
reference set.

On licensing: a requirement's ``text`` is OWASP ASVS 5.0.0, which OWASP
publishes under CC BY-SA 4.0. This module reads it out of the governed catalog
at run time rather than carrying any of it. A rendered pairing therefore
reproduces ShareAlike text, so :func:`render_html` stamps the attribution and
the licence onto the page, and :func:`refuse_path_inside_repo` keeps the output
from landing in this Apache-2.0 tree, where it would be an undeclared governed
file and would fail
``test_no_upstream_sentence_appears_in_an_ungoverned_file``.

On security: a claim's prose is model output and reaches an HTML page here, so
every interpolated value is escaped (OWASP LLM05, A03). Nothing in a report is
read as markup.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analysis_service.frameworks.asvs.catalog import Requirement, requirements_for
from analysis_service.frameworks.asvs.record import requirement_of
from analysis_service.report import FrameworkAnalysis, RuledClaim
from evals.harness.applicability import (
    FRAMEWORK,
    ApplicabilityScore,
    declared_level,
    score_applicability,
)
from evals.harness.reference import GoldenCase, ReferenceRequirement

#: The repository this module lives in. A rendered pairing may not be written
#: inside it; see the licensing note above.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: What the page says about the text it reproduces. Required rather than
#: optional: the page carries ASVS sentences, so it carries ASVS's terms.
ATTRIBUTION = (
    "Requirement text is OWASP Application Security Verification Standard"
    " 5.0.0, © OWASP Foundation, used under CC BY-SA 4.0. This page is"
    " distributed under the same licence."
)


class PairingError(ValueError):
    """A pairing that cannot be built, or would be written somewhere unsafe."""


@dataclass(frozen=True)
class AppliedPair:
    """One requirement the run applied, with what it said and what the case said.

    ``expected`` is the whole of the disagreement: ``False`` is an over-applied
    requirement, which is the side of the question this view exists for.
    """

    requirement: str
    level: int
    lane: str
    section_name: str
    text: str
    verdict: str
    argument: str
    expected: bool
    #: Matched only through a ``needs-other-evidence`` scope entry: a lane
    #: raised it and the service withheld the claim. It counts as applied
    #: (#456), and it is not the same answer as a ruling — which is exactly
    #: what a reader has to weigh.
    deferred: bool
    #: Whether the case marks this requirement ``must-find``.
    must_find: bool


@dataclass(frozen=True)
class MissedPair:
    """One requirement the case expected and the run did not deliver.

    ``deferral`` carries the service's own reason where a lane raised the
    requirement and the claim was withheld for want of another kind of
    evidence, and is empty where no lane raised it at all. The two are different
    findings and the reference set cannot tell them apart on its own.
    """

    requirement: str
    level: int
    lane: str
    text: str
    must_find: bool
    expectation: str
    deferral: str


@dataclass(frozen=True)
class CasePairing:
    """Both sides of one case's applicability disagreement."""

    case: str
    level: int
    expected: int
    applied: int
    agreed: int
    applied_pairs: tuple[AppliedPair, ...]
    missed_pairs: tuple[MissedPair, ...]

    @property
    def over_applied(self) -> tuple[AppliedPair, ...]:
        """The requirements the run applied and the case did not expect."""
        return tuple(pair for pair in self.applied_pairs if not pair.expected)

    @property
    def agreed_pairs(self) -> tuple[AppliedPair, ...]:
        """Expected, applied, and settled by a claim rather than by a deferral."""
        return tuple(
            pair for pair in self.applied_pairs if pair.expected and not pair.deferred
        )

    @property
    def deferred_pairs(self) -> tuple[AppliedPair, ...]:
        """Expected, and answered only by withholding the claim.

        The second half of #447's question. Each of these counts as applied, so
        it moves recall, and none of them is a ruling — so whether the input
        could ever have settled it is the thing a reader is being asked.
        """
        return tuple(
            pair for pair in self.applied_pairs if pair.expected and pair.deferred
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "framework": FRAMEWORK,
            "level": self.level,
            "expected": self.expected,
            "applied": self.applied,
            "agreed": self.agreed,
            "over_applied": [
                {
                    "requirement": pair.requirement,
                    "lane": pair.lane,
                    "verdict": pair.verdict,
                    "argument": pair.argument,
                }
                for pair in self.over_applied
            ],
            "deferred": [
                {
                    "requirement": pair.requirement,
                    "lane": pair.lane,
                    "must_find": pair.must_find,
                    "argument": pair.argument,
                }
                for pair in self.deferred_pairs
            ],
            "missed": [
                {
                    "requirement": pair.requirement,
                    "lane": pair.lane,
                    "must_find": pair.must_find,
                    "deferral": pair.deferral,
                }
                for pair in self.missed_pairs
            ],
        }


def refuse_path_inside_repo(out: Path) -> Path:
    """``out`` resolved, or a refusal if it lands in this repository.

    A rendered pairing carries ASVS sentences. Inside this tree that is an
    undeclared CC BY-SA file in an Apache-2.0 distribution, which the licence
    lints are right to fail on — so it is refused here, where the message can
    say why, rather than there.
    """
    resolved = out.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise PairingError(
            f"{resolved} is inside {REPO_ROOT}. A pairing reproduces ASVS"
            " requirement text, which OWASP publishes under CC BY-SA 4.0, and"
            " this repository's code is Apache-2.0 — write it outside the tree"
        )
    return resolved


def _claims_by_requirement(claims: Sequence[RuledClaim]) -> dict[str, RuledClaim]:
    """The block's claims keyed by the requirement each rules on."""
    return {
        requirement: claim
        for claim in claims
        if (requirement := requirement_of(claim.id))
    }


def _catalog(level: int) -> dict[str, Requirement]:
    return {entry.id: entry for entry in requirements_for(level)}


def _references(case: GoldenCase) -> dict[str, ReferenceRequirement]:
    return {
        reference.requirement: reference
        for reference in case.references.get(FRAMEWORK) or ()
        if isinstance(reference, ReferenceRequirement)
    }


def _deferrals(block: FrameworkAnalysis) -> Mapping[str, str]:
    """Every unit the service withheld for want of another kind of evidence."""
    return {
        entry.unit: entry.reason
        for entry in block.scope
        if entry.state == "needs-other-evidence"
    }


def pair_case(
    case: GoldenCase, block: FrameworkAnalysis, score: ApplicabilityScore | None = None
) -> CasePairing:
    """One case's two sides, built from the score the harness already computes.

    ``score`` is taken rather than recomputed where a caller holds one, so a
    pairing and the number it explains can never disagree.
    """
    score = score or score_applicability(case, block)
    level = declared_level(case)
    catalog = _catalog(level)
    claims = _claims_by_requirement(block.claims)
    references = _references(case)
    deferrals = _deferrals(block)

    applied_pairs = []
    for requirement in sorted(set(score.matched) | set(score.over_applied)):
        entry = catalog.get(requirement)
        claim = claims.get(requirement)
        if entry is None:
            continue
        applied_pairs.append(
            AppliedPair(
                requirement=requirement,
                level=entry.level,
                lane=entry.lane,
                section_name=entry.section_name,
                text=entry.text,
                # A requirement matched only through a scope entry carries no
                # claim, so the entry's own reason is what the run said about it.
                verdict=claim.verdict.status if claim else "deferred",
                argument=(
                    claim.description if claim else deferrals.get(requirement, "")
                ),
                expected=requirement in references,
                deferred=requirement in score.matched_by_deferral,
                must_find=bool(
                    (reference := references.get(requirement)) and reference.must_find
                ),
            )
        )

    missed_pairs = []
    for requirement in score.missed:
        entry = catalog.get(requirement)
        reference = references.get(requirement)
        if entry is None or reference is None:
            continue
        missed_pairs.append(
            MissedPair(
                requirement=requirement,
                level=entry.level,
                lane=entry.lane,
                text=entry.text,
                must_find=reference.must_find,
                expectation=reference.claim,
                deferral=deferrals.get(requirement, ""),
            )
        )

    return CasePairing(
        case=case.id,
        level=level,
        expected=len(score.expected),
        applied=len(score.matched) + len(score.over_applied),
        agreed=len(score.matched),
        applied_pairs=tuple(applied_pairs),
        missed_pairs=tuple(missed_pairs),
    )


def render(pairing: CasePairing) -> None:
    """The terminal reading: counts and identifiers, never the argument.

    A claim's argument runs to hundreds of characters and a terminal is the
    wrong place to read 56 of them, which is why :func:`render_html` exists.
    """
    print(
        f"{pairing.case} at level {pairing.level}:"
        f" the case expects {pairing.expected},"
        f" the run applied {pairing.applied},"
        f" both agree on {pairing.agreed}"
    )
    print(f"\nover-applied ({len(pairing.over_applied)}):")
    for applied in pairing.over_applied:
        print(f"  {applied.requirement:<10} {applied.lane:<34} {applied.verdict}")
    print(f"\nexpected, answered only by a deferral ({len(pairing.deferred_pairs)}):")
    for deferred in pairing.deferred_pairs:
        mark = "must-find" if deferred.must_find else "         "
        print(f"  {deferred.requirement:<10} {mark}  {deferred.lane}")
    print(f"\nmissed ({len(pairing.missed_pairs)}):")
    for missed in pairing.missed_pairs:
        mark = "must-find" if missed.must_find else "         "
        why = "deferred" if missed.deferral else "never raised"
        print(f"  {missed.requirement:<10} {mark}  {why:<13} {missed.lane}")


def _section(title: str, body: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2>{body}</section>"


def _applied_html(pairs: Sequence[AppliedPair]) -> str:
    rows = []
    for pair in pairs:
        rows.append(
            "<article>"
            f"<h3>{html.escape(pair.requirement)}"
            f" <span class=v>{html.escape(pair.verdict)}</span>"
            + (" <span class=v>must-find</span>" if pair.must_find else "")
            + "</h3>"
            f"<p class=lane>{html.escape(pair.lane)} &middot;"
            f" {html.escape(pair.section_name)} &middot; L{pair.level}</p>"
            f"<blockquote>{html.escape(pair.text)}</blockquote>"
            f"<p class=arg>{html.escape(pair.argument)}</p>"
            "</article>"
        )
    return "".join(rows)


def _missed_html(pairs: Sequence[MissedPair]) -> str:
    rows = []
    for pair in pairs:
        tag = "must-find" if pair.must_find else "should-find"
        why = (
            f"deferred: {pair.deferral}"
            if pair.deferral
            else "no lane raised this requirement"
        )
        rows.append(
            "<article>"
            f"<h3>{html.escape(pair.requirement)}"
            f" <span class=v>{html.escape(tag)}</span></h3>"
            f"<p class=lane>{html.escape(pair.lane)} &middot; L{pair.level}</p>"
            f"<blockquote>{html.escape(pair.text)}</blockquote>"
            f"<p class=arg><b>the case expects:</b> {html.escape(pair.expectation)}</p>"
            f"<p class=why>{html.escape(why)}</p>"
            "</article>"
        )
    return "".join(rows)


#: The page's own styles. Inline, because a pairing is read from a file rather
#: than served, and a stylesheet it cannot reach is a page nobody can read.
_STYLE = """
:root{--fg:#1a1a1a;--bg:#fff;--mut:#666;--line:#e2e2e2;--acc:#8a3324}
@media(prefers-color-scheme:dark){
:root{--fg:#e8e8e8;--bg:#161616;--mut:#9a9a9a;--line:#2e2e2e;--acc:#e0a08a}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem 4rem;background:var(--bg);color:var(--fg);
font:16px/1.6 ui-serif,Georgia,serif;max-width:52rem;margin-inline:auto}
h1{font-size:1.5rem;margin:0 0 .25rem}
h2{font-size:1.15rem;margin:2.5rem 0 .75rem;padding-bottom:.3rem;
border-bottom:2px solid var(--line)}
h3{font-size:1rem;margin:0 0 .2rem;font-family:ui-monospace,monospace}
article{padding:1rem 0;border-bottom:1px solid var(--line)}
blockquote{margin:.5rem 0;padding-left:.9rem;border-left:3px solid var(--acc);
color:var(--fg)}
.lead{color:var(--mut);margin:0 0 1.5rem}
.lane{color:var(--mut);font-size:.85rem;margin:0 0 .5rem;
font-family:ui-sans-serif,system-ui,sans-serif}
.arg{margin:.5rem 0 0;font-size:.95rem}
.why{margin:.4rem 0 0;font-size:.9rem;color:var(--mut)}
.v{font-family:ui-sans-serif,system-ui,sans-serif;font-size:.7rem;
text-transform:uppercase;letter-spacing:.06em;color:var(--acc);
border:1px solid var(--acc);border-radius:3px;padding:.1rem .35rem;
vertical-align:middle}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);
color:var(--mut);font-size:.85rem}
"""


def render_html(pairing: CasePairing, source: str) -> str:
    """The reading view as one self-contained page.

    ``source`` names the artifact the pairing was built from, so a reader can
    tell which run they are ruling on and regenerate it.
    """
    generated = datetime.now(UTC).strftime("%Y-%m-%d")
    lead = (
        f"The case expects a ruling on <b>{pairing.expected}</b> requirements."
        f" The run applied <b>{pairing.applied}</b>."
        f" Both agree on <b>{pairing.agreed}</b>."
        " Either the reference set is narrower than the standard warrants, or"
        " the lane agent applies requirements too loosely, and reading these"
        " settles it."
    )
    heading = (
        f"<h1>{html.escape(pairing.case)}: {len(pairing.over_applied)}"
        f" over-applied, {len(pairing.missed_pairs)} missed</h1>"
    )
    body = "".join(
        (
            heading,
            f"<p class=lead>{lead}</p>",
            _section(
                f"Over-applied — {len(pairing.over_applied)} the case did not expect",
                _applied_html(pairing.over_applied),
            ),
            _section(
                f"Missed — {len(pairing.missed_pairs)} the case expected",
                _missed_html(pairing.missed_pairs),
            ),
            _section(
                f"Answered only by a deferral — {len(pairing.deferred_pairs)}"
                " the case expected",
                _applied_html(pairing.deferred_pairs),
            ),
            _section(
                f"Agreed — {len(pairing.agreed_pairs)} both sides ruled on",
                _applied_html(pairing.agreed_pairs),
            ),
            (
                "<footer>"
                f"<p>ASVS level {pairing.level}."
                f" Generated {html.escape(generated)} from"
                f" {html.escape(source)}.</p>"
                f"<p>{html.escape(ATTRIBUTION)}</p>"
                "</footer>"
            ),
        )
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(pairing.case)} applicability pairing</title>"
        f"<style>{_STYLE}</style></head><body>{body}</body></html>"
    )
