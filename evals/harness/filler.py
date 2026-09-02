"""Whether a package's required justifications say anything.

This module exists to catch one class of defect. A mechanical check on model
output can be satisfiable by construction for one package's claim shape and not
for another's. The model, required to satisfy it, finds the value that always
passes. Every check goes green, and the output tells a reader nothing.

Two instances reached production before anybody looked:

* A ``needs-info`` verdict had to name an element and one of its attributes. A
  framework that rules on requirements asks most of its questions about a
  codebase rather than about an element, so it pointed at ``notes``. Every
  element type carries that field, so it always resolves and never informs.
* A claim that rules a requirement not applicable has to carry grounds, and
  every ground kind names something that exists. The justifying fact is that the
  model contains no such component, so the agent cited an arbitrary verified
  quote: "no LDAP directory query path is identified", grounded on a sentence
  about the authorization code flow with PKCE.

Neither is visible offline. The suite scripts the agents, so pointers always
resolve and quotes always verify, and every check here passes against a scripted
model. This instrument therefore reads finished reports rather than fixtures.

There are two readings, and both are neutral. Each reads only the shared
:class:`~analysis_service.report.Claim` and
:class:`~analysis_service.report.Verdict` shape, so a package nobody has written
is measured on arrival, with no entry added here.

Ineligible pointers is the crisper of the two, and it is not a threshold. The
evidence catalog already decides which attributes carry information.
:func:`~analysis_service.system_model.attribute_names` admits the type-specific
ones and refuses ``notes``, ``description`` and the rest, on the stated grounds
that a note "is a sentence, not an unstated control". A ``needs-info`` that
points at an attribute that rule refuses is naming a field the repository has
already ruled says nothing. The good case is zero, and across fifteen archived
STRIDE sweeps it is 0 of 378.

Ground concentration is softer, and this module reports it as a number rather
than judging it. It is the share of a package's claims whose grounds use the
single most common combination of kinds. A package that justifies most of its
claims the same way is either analysing a very uniform system or reaching for
one filler. Across the same fifteen sweeps STRIDE sits at 23%, and the two ASVS
runs that carried the bug above sit at 46% and 73%.

Neither reading gates a sweep. An instrument reports, and a person decides
whether a number is a finding. That is the reason both defects are described
above rather than encoded as a threshold nobody could defend.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from analysis_service.report import Report
from analysis_service.system_model import attribute_names

__all__ = ["FillerRow", "artifact", "render", "rows"]


@dataclass(frozen=True)
class FillerRow:
    """One package's two readings, pooled over every case in a sweep."""

    framework: str
    #: ``related_unknowns`` entries written in the element spelling. The
    #: ``subject`` spelling names no attribute and so has none to be refused.
    pointers: int
    #: Of those, how many name an attribute the evidence catalog would refuse.
    ineligible: int
    claims: int
    #: The most common combination of ground kinds, and the share of claims
    #: whose grounds use exactly it.
    modal_grounds: str
    modal_share: float

    @property
    def ineligible_share(self) -> float:
        return self.ineligible / self.pointers if self.pointers else 0.0


def _kind_set(claim: Any) -> str:
    """One claim's ground kinds as a comparable string, order removed."""
    return "+".join(sorted({ground.kind for ground in claim.grounds})) or "(none)"


def rows(reports: Iterable[Report]) -> tuple[FillerRow, ...]:
    """Both readings per framework, pooled across a sweep's cases.

    Pooled rather than per case for the reason
    :mod:`evals.harness.coverage` gives about its own rows: one case's share is
    one sample of one system, and the number that means anything is the fold.
    """
    pointers: Counter[str] = Counter()
    ineligible: Counter[str] = Counter()
    kinds: dict[str, Counter[str]] = {}

    for report in reports:
        # Eligibility is a property of the element's *type*, so it is resolved
        # against the model this report embeds rather than against a name.
        eligible = {
            element.id: set(attribute_names(element))
            for element in report.system_model.elements()
        }
        for block in report.analyses:
            name = block.framework
            seen = kinds.setdefault(name, Counter())
            for claim in (*block.claims, *block.rejected_claims):
                seen[_kind_set(claim)] += 1
                for ref in claim.verdict.related_unknowns:
                    if not ref.names_an_element:
                        continue
                    pointers[name] += 1
                    if ref.attribute not in eligible.get(ref.element_id, set()):
                        ineligible[name] += 1

    built = []
    for framework in sorted(kinds):
        counted = kinds[framework]
        total = sum(counted.values())
        modal, count = counted.most_common(1)[0] if counted else ("(none)", 0)
        built.append(
            FillerRow(
                framework=framework,
                pointers=pointers[framework],
                ineligible=ineligible[framework],
                claims=total,
                modal_grounds=modal,
                modal_share=count / total if total else 0.0,
            )
        )
    return tuple(built)


def render(measured: Sequence[FillerRow]) -> None:
    """The table, and the one line that says how to read it."""
    print("\n## Filler")
    if not measured:
        print("\nNo claims. Nothing to read.")
        return
    print(
        "\nA required field carrying a value that always passes and never"
        " informs. Ineligible pointers should be 0; ground concentration is a"
        " number to judge, not a threshold."
    )
    print("\n| framework | claims | ineligible pointers | modal grounds | share |")
    print("| --- | ---: | ---: | --- | ---: |")
    for row in measured:
        pointers = (
            f"{row.ineligible}/{row.pointers} ({row.ineligible_share:.0%})"
            if row.pointers
            else "0/0"
        )
        print(
            f"| {row.framework} | {row.claims} | {pointers} |"
            f" `{row.modal_grounds}` | {row.modal_share:.0%} |"
        )


def artifact(measured: Sequence[FillerRow]) -> dict[str, Any]:
    """The same readings, keyed by framework, for the sweep's artifact."""
    return {
        "filler": {
            row.framework: {
                "claims": row.claims,
                "pointers": row.pointers,
                "ineligible_pointers": row.ineligible,
                "ineligible_share": round(row.ineligible_share, 4),
                "modal_grounds": row.modal_grounds,
                "modal_grounds_share": round(row.modal_share, 4),
            }
            for row in measured
        }
    }
