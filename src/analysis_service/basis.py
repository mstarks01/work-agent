"""Does a stated control share any word with the source it cites?

A **diagnostic**, never a gate. :mod:`analysis_service.grounding` proves a
quoted span is present in a source; it cannot prove the span supports the claim
that cites it. This module asks the same question from the other end and answers
a much weaker form of it: a stated free-text control whose value shares **no
content token** with the source it cites was written out of nothing the
submitter wrote.

The failure it names is the one #465 measured. Given one sentence::

    The API sends payment requests to Stripe.

an extraction wrote ``authentication = "OAuth 2.0"`` and
``encryption_in_transit = "TLS 1.3"``. Both pass every mechanical check the
service has: the leading token is neither sentinel, so
:func:`~analysis_service.analysis.control_state` reads them as ``stated``, the
candidate rules that ask about a missing control stay quiet, and the evidence
catalog offers nothing. Measured on that model, four of the five evidence
entries the flow would otherwise carry are erased. Nothing anywhere says the
source never mentioned OAuth or TLS.

**Why it flags and never fails.** Token overlap is not entailment. A value can
share a word with its source and still misread it, and a correct value can be
worded from the reader's vocabulary rather than the writer's. So the output is
logged beside the job and nothing reads it: no route branches on it, no repair
pass is asked for, and no report carries it. Promotion to a gate is a separate
decision with its own evidence (#470), and this module does not make it.

Scope
=====

:data:`CONTROL_SCOPE` keys :data:`~analysis_service.analysis.CONTROL_ATTRIBUTES`
— every control attribute answers, and an attribute added tomorrow is compared
against that registry by a test rather than defaulting into the measurement
unread. Two of the five are out of
scope, and the reason is the same one twice: **they hold schema words rather
than source words**, so overlap with the source measures nothing about them.
``exposure`` is a closed vocabulary on the schema. ``data_classification`` is
free text whose vocabulary is a classification scheme — a source that says
"not exposed outside the cluster" is correctly written ``internal``, which
shares no word with it.

The three that remain — ``authentication``, ``encryption_in_transit``,
``encryption_at_rest`` — name a mechanism, and a mechanism the source described
is described in the source's words.

The token rule
==============

A **content token** is a run of letters and digits, with an internal dot kept so
a version survives whole: ``"TLS 1.3"`` gives ``tls`` and ``1.3``, and ``"OAuth
2.0"`` gives ``oauth`` and ``2.0``. Single characters and :data:`FUNCTION_WORDS`
drop out. What is left is matched against the cited source through
:func:`~analysis_service.analysis.matches_term`, the repo's one reader of "does
this text name this term", which matches at the start of a word — so ``encrypt``
reaches ``encrypted`` and ``tls`` reaches ``TLSv1.2``.

:data:`FUNCTION_WORDS` is an ordinary English closed-class list: articles,
conjunctions, prepositions, pronouns and auxiliaries. It was written from that
rule and not from the values it went on to flag, and it is frozen there. That
matters, because #465's first pass grew its list after seeing which values
failed, which fits the list to the sample and makes the rate it reports mean
nothing. So the list is ordinary rather than complete — a word it omits reads
as content, which costs a diagnostic line and never a rejection.

The measurement
===============

Over the 13 corpus models and their sources, every stated value of the three
in-scope attributes. A flag on a blessed value is a **false rejection**: the
corpus is the closest thing to ground truth this repo has, so a value it carries
is treated as one a person would keep.

=====================================  ======  =======  ============
rung                                   values  flagged  false-reject
=====================================  ======  =======  ============
every content token present                22       16        72.7%
**at least one content token present**     22        0         0.0%
=====================================  ======  =======  ============

**The strict rung is dead, and not because the values are wrong.** It fails on
the prose around the mechanism: ``"static per-partner key issued at onboarding,
never rotated"`` names a key the source names, and then loses on ``issued``,
``onboarding`` and ``rotated`` — the writer's summary of a source that said the
same thing in other words. Sixteen of the 22 fail that way. So the rule is the
weak rung, and the strict one is recorded here to say it was measured rather
than assumed.

:func:`~evals.harness.modes.score_extraction` measures the same failure from the
other side, as ``unverified -> stated``: it asks whether an extraction invented
a control the blessed model leaves ``unknown``. Neither replaces the other. That
score needs a blessed model to compare against and so exists only inside the
eval corpus; this one needs only the job's own sources and so runs on every job.
It is carried on :class:`~evals.harness.modes.ExtractionScore` beside that
score, so a sweep reports the pair.

**Read the rate for what it is.** 22 values is a small sample beside the 206
element excerpts that calibrated the grounding ladder, and the corpus is
agent-authored — no case has been read by a person (#226). A 0% flag rate may
mean a blessed model reuses its source's wording, which is exactly what an agent
writing one would do. So the number measures agreement with an unreviewed
reference, not correctness, and it is a floor under the gate question rather
than an answer to it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.analysis import (
    CONTROL_ATTRIBUTES,
    control_state,
    matches_term,
)
from analysis_service.system_model import Element, SystemModel

__all__ = [
    "CONTROL_SCOPE",
    "FUNCTION_WORDS",
    "IN_SCOPE",
    "UnbasedControl",
    "content_tokens",
    "unbased_controls",
]

#: Why a control attribute is out of scope, or ``None`` where it is measured.
#: Keyed by :data:`~analysis_service.analysis.CONTROL_ATTRIBUTES` so the registry
#: decides the membership: a sixth control attribute has no entry here, so it
#: is measured only once somebody declares it. ``tests/test_basis.py`` compares
#: the two and names the attribute that is missing — here rather than at import,
#: so the guard still runs on a half-added attribute.
CONTROL_SCOPE: Mapping[str, str | None] = MappingProxyType(
    {
        "authentication": None,
        "encryption_in_transit": None,
        "encryption_at_rest": None,
        "exposure": "a closed vocabulary on the schema, so its value is a schema"
        " word and never the source's",
        "data_classification": "free text whose vocabulary is a classification"
        " scheme, so a source saying 'not exposed outside' is correctly written"
        " 'internal' and shares no word with it",
    }
)

#: Stands in for an attribute :data:`CONTROL_SCOPE` does not answer for, so an
#: undeclared attribute reads as out of scope rather than as declared in.
_UNDECLARED = "undeclared"

#: The attributes the diagnostic reads, in registry order.
IN_SCOPE: tuple[str, ...] = tuple(
    attribute
    for attribute in CONTROL_ATTRIBUTES
    if CONTROL_SCOPE.get(attribute, _UNDECLARED) is None
)

#: English closed-class words: a token that carries no mechanism. Written from
#: that rule, before the measurement, and deliberately not revised after it —
#: a list grown from the values it failed to match is fitted to its sample.
FUNCTION_WORDS: frozenset[str] = frozenset(
    # A block rather than a list literal: the point of the list is that a reader
    # can check it against the closed-class rule at a glance.
    """
    a an the and or but nor so yet for of to in on at by with from into onto
    over under above below through during before after between among across
    is are was were be been being am do does did done has have had having
    can could may might must shall should will would
    it its this that these those there here they them their we us our you your
    he him his she her as if then than when where which who whom whose what why
    how all any both each few more most other some such no not only own same
    too very just also per via about against within without upon while until
    """.split()  # noqa: SIM905 -- one block reads as the rule it was written from
)

#: A run of letters and digits, keeping an internal dot between digits so a
#: version number survives as one token: ``1.3`` rather than ``1`` and ``3``.
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")


class UnbasedControl(BaseModel):
    """One stated control whose value shares no word with its cited source.

    Carries the tokens it looked for, because the diagnostic is only useful if
    a reader can see what was searched: "``oauth``, ``2.0`` appear nowhere in
    'System description'" is actionable, and "this value looks invented" is not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str = Field(min_length=1, max_length=300)
    attribute: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)
    source_label: str = Field(min_length=1, max_length=200)
    tokens: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"{self.attribute!r} on {self.element_id} states {self.value!r}, and"
            f" none of {', '.join(self.tokens)} appears in"
            f" {self.source_label!r}"
        )


def content_tokens(value: str) -> tuple[str, ...]:
    """The tokens of ``value`` that could name a mechanism, lower-cased.

    Order preserved and duplicates kept: the caller reports them back to a
    reader, who is looking for the words of the value they wrote.
    """
    return tuple(
        token
        for token in _TOKEN.findall(value.lower())
        if len(token) > 1 and token not in FUNCTION_WORDS
    )


def unbased_controls(
    model: SystemModel, sources: Mapping[str, str]
) -> list[UnbasedControl]:
    """Every stated in-scope control that its cited source does not echo.

    Walks elements in :meth:`SystemModel.elements` order and attributes in
    :data:`IN_SCOPE` order, so the output is stable and complete.

    Three values are passed over, each for a reason that is not a pass:

    * a control that is not ``stated`` — there is no assertion to have a basis.
    * a value whose content tokens are empty, so there is nothing to look for.
    * an element whose ``source_label`` names no source the job carried. The
      validity gate already refuses that shape
      (:func:`~analysis_service.validation.validate`), and an element citing
      nothing is a different failure from one citing a source that does not
      support it.
    """
    return [
        flag
        for element in model.elements()
        for flag in _element_flags(element, sources)
    ]


def _element_flags(
    element: Element, sources: Mapping[str, str]
) -> Iterator[UnbasedControl]:
    """This element's unbased controls, in :data:`IN_SCOPE` order."""
    source = sources.get(element.source_label, "").lower()
    if not source:
        return
    for attribute in IN_SCOPE:
        value = getattr(element, attribute, None)
        if not isinstance(value, str) or control_state(value) != "stated":
            continue
        tokens = content_tokens(value)
        if not tokens or any(matches_term(token, source) for token in tokens):
            continue
        yield UnbasedControl(
            element_id=element.id,
            attribute=attribute,
            value=value[:200],
            source_label=element.source_label,
            tokens=tokens,
        )
