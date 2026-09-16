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

**The cost is submitted text times submitted text**, which is the one thing
here worth reading twice. A search costs the length of a source, and the number
of searches is how many different words a model's controls name — so a
150-element model of invented controls over a 100 KiB source cost 13.5 seconds
of CPU before :class:`_Scan` existed. Memoizing takes the repeated half of that
away and :data:`MAX_SCAN_WORK` bounds the rest. Stopping is affordable for the
same reason the whole module is safe: nothing downstream reads the result.

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
every content token present                21       14        66.7%
**at least one content token present**     21        0         0.0%
=====================================  ======  =======  ============

**The strict rung is dead, and not because the values are wrong.** It fails on
the prose around the mechanism: ``"static per-partner key issued at onboarding,
never rotated"`` names a key the source names, and then loses on ``issued``,
``onboarding`` and ``rotated`` — the writer's summary of a source that said the
same thing in other words. Fourteen of the 21 fail that way. So the rule is the
weak rung, and the strict one is recorded here to say it was measured rather
than assumed.

**This table supersedes the one in #470, which read 12 of 22 on the strict
rung.** That figure came from #465's first pass, whose function-word list grew
after seeing which values failed — fitted to the sample, which is why #470 made
freezing the list an acceptance criterion. The list here was written from the
closed-class rule before the run. The two figures are not a difference to take:
the corpus itself has changed since, and ``tests/test_basis.py`` re-derives
every number in this table from the corpus it ships beside. The weak rung reads
0 either way.

**The denominator moves when the corpus is corrected, which is the point of
re-deriving it.** Case 09's fax leg stated a destination-verification gap in its
``authentication`` field, so a missing safeguard sat in this measurement as a
stated control; ruling it to ``unknown`` (#925) took it out. The #961 step 3
rulings reworded case 04's API-key value to what the source reports, and one
strict flag left with the word the source never used. Case 07 carried the
same family of fact as ``none`` and was never in here at all — one pair of
inconsistent values, visible from this side as a denominator of 22 that should
always have been 21.

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

import logging
import re
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.analysis import (
    CONTROL_ATTRIBUTES,
    CONTROL_VALUE_MAX_CHARS,
    control_state,
    matches_term,
)
from analysis_service.system_model import SystemModel

__all__ = [
    "CONTROL_SCOPE",
    "FUNCTION_WORDS",
    "IN_SCOPE",
    "MAX_SCAN_WORK",
    "Coverage",
    "Disposition",
    "StatedControl",
    "UnbasedControl",
    "content_tokens",
    "coverage",
    "dispositions",
    "read_controls",
    "stated_controls",
    "unbased_controls",
]

logger = logging.getLogger(__name__)

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

#: The most source characters one job's diagnostic will read, summed over every
#: token search it makes. **A budget the scan spends, not a size it is refused
#: for**, on the same reasoning as :data:`~analysis_service.grounding.MAX_REPAIR_WORK`:
#: the cost is the number of distinct tokens times the length of the source each
#: is searched against, and submitted text sets both terms, so no function of
#: either one alone can see it.
#:
#: Measured, on the tree that carries this constant: a search runs at about
#: 70,000 characters per millisecond, so 20 million characters is about 285 ms
#: of CPU. The worst of the 13 corpus cases spends 45,448, which is 440 times
#: under the budget. A job that spends it names over a thousand different words
#: across its controls and matches none of them, which is not a model this
#: measures usefully.
MAX_SCAN_WORK = 20_000_000

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
    value: str = Field(min_length=1, max_length=CONTROL_VALUE_MAX_CHARS)
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


#: Why one stated in-scope control is or is not something this can read.
#: ``measurable`` is the only one the scan sees; the other two name a control
#: this diagnostic has nothing to say about, which is a different answer from
#: "looked and found no problem".
Disposition = Literal["measurable", "uncited", "tokenless"]


class StatedControl(NamedTuple):
    """One stated in-scope control, with the tokens to look for and where.

    **The one reader of "which values does this diagnostic measure".** Both
    rungs of the published measurement and the shipped
    :func:`unbased_controls` walk the model through :func:`stated_controls`, so
    a denominator and a rate can never be computed over two different sets.
    """

    element_id: str
    attribute: str
    value: str
    source_label: str
    tokens: tuple[str, ...]


class Coverage(BaseModel):
    """What this diagnostic read, and what it could not read at all.

    **An empty list of flags is not a clean bill.** Four different things
    produce one: every value echoed its source, or no value was readable, or
    there were no stated values to read, or the scan budget ran out before
    the values were searched. A caller holding only :func:`unbased_controls`
    cannot tell those apart; the second is the shape an erased citation or a
    function-word value arrives in (#925), and the last was counted as
    ``measured`` until #961 — a budget set to zero read as five controls
    measured and none flagged.

    ``flagged`` is a subset of ``measured``, so ``measured - flagged`` is what
    the diagnostic looked at and let through. ``uncited``, ``tokenless`` and
    ``exhausted`` are outside it entirely: no search decided them, so nothing
    is claimed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Stated in-scope controls the model carries, whatever came of them.
    stated: int = Field(ge=0)
    #: Those the scan searched a source for.
    measured: int = Field(ge=0)
    #: Those whose element cites no source this job carried.
    uncited: int = Field(ge=0)
    #: Those whose value holds no content token to look for.
    tokenless: int = Field(ge=0)
    #: Those the scan reached after :data:`MAX_SCAN_WORK` was spent, so no
    #: search decided them either way.
    exhausted: int = Field(ge=0)
    #: The measured ones whose cited source echoed none of their tokens.
    flagged: int = Field(ge=0)

    @classmethod
    def empty(cls) -> Coverage:
        """A model nothing was read from, which is not a model that read clean."""
        return cls(stated=0, measured=0, uncited=0, tokenless=0, exhausted=0, flagged=0)

    @property
    def unmeasured(self) -> int:
        return self.uncited + self.tokenless + self.exhausted

    def to_json(self) -> dict[str, int]:
        return {
            "stated": self.stated,
            "measured": self.measured,
            "uncited": self.uncited,
            "tokenless": self.tokenless,
            "exhausted": self.exhausted,
            "flagged": self.flagged,
        }


def dispositions(
    model: SystemModel, sources: Mapping[str, str]
) -> Iterator[tuple[StatedControl, Disposition]]:
    """Every stated in-scope control, each with whether this can read it.

    Walks elements in :meth:`SystemModel.elements` order and attributes in
    :data:`IN_SCOPE` order, so the output is stable and complete.

    **The one walk.** :func:`stated_controls` filters it and :func:`coverage`
    counts it, so the rate and the denominator cannot be taken over two
    different sets. A control that is not ``stated`` is not here at all: there
    is no assertion to have a basis, which is not the same as an assertion
    nobody could check.

    Two dispositions are outside the measurement, each for a reason that is not
    a pass:

    * ``uncited`` — the element's ``source_label`` names no source the job
      carried, so there is no text to search. An element citing nothing is a
      different failure from one citing a source that does not support it, and
      the validity gate refuses both shapes
      (:func:`~analysis_service.validation.validate`).
    * ``tokenless`` — the value holds no content token, so there is nothing to
      look for. A value of function words alone is one; so is one written in a
      script :data:`_TOKEN` does not read, since the token rule is ASCII.
    """
    for element in model.elements():
        cited = bool(sources.get(element.source_label))
        for attribute in IN_SCOPE:
            value = getattr(element, attribute, None)
            if not isinstance(value, str) or control_state(value) != "stated":
                continue
            tokens = content_tokens(value)
            control = StatedControl(
                element_id=element.id,
                attribute=attribute,
                value=value[:CONTROL_VALUE_MAX_CHARS],
                source_label=element.source_label,
                tokens=tokens,
            )
            if not cited:
                yield control, "uncited"
            elif not tokens:
                yield control, "tokenless"
            else:
                yield control, "measurable"


def stated_controls(
    model: SystemModel, sources: Mapping[str, str]
) -> Iterator[StatedControl]:
    """Every control this diagnostic has something to say about.

    The measurable half of :func:`dispositions`. What the other half holds is
    :func:`coverage`'s to report, and a caller reading this alone is reading a
    denominator rather than a population.
    """
    for control, disposition in dispositions(model, sources):
        if disposition == "measurable":
            yield control


def read_controls(
    model: SystemModel, sources: Mapping[str, str]
) -> tuple[list[UnbasedControl], Coverage]:
    """One walk and one budget: the flags, and what they were drawn from.

    Every other reading here comes from this one, so a report cannot pair a
    flag count with a denominator taken over a second walk. A caller wanting
    both takes this and pays :data:`MAX_SCAN_WORK` once.
    """
    scan = _Scan(sources)
    flags: list[UnbasedControl] = []
    counts = {"measurable": 0, "uncited": 0, "tokenless": 0}
    exhausted = 0
    for control, disposition in dispositions(model, sources):
        counts[disposition] += 1
        if disposition != "measurable":
            continue
        echoes_none = scan.echoes_none(control)
        if echoes_none is None:
            exhausted += 1
        elif echoes_none:
            flags.append(
                UnbasedControl(
                    element_id=control.element_id,
                    attribute=control.attribute,
                    value=control.value,
                    source_label=control.source_label,
                    tokens=control.tokens,
                )
            )
    return flags, Coverage(
        stated=sum(counts.values()),
        measured=counts["measurable"] - exhausted,
        uncited=counts["uncited"],
        tokenless=counts["tokenless"],
        exhausted=exhausted,
        flagged=len(flags),
    )


def unbased_controls(
    model: SystemModel, sources: Mapping[str, str]
) -> list[UnbasedControl]:
    """Every stated in-scope control that its cited source does not echo.

    Spends :data:`MAX_SCAN_WORK` and then stops, which is why this returns a
    list rather than a generator: the budget is the whole job's, and a caller
    that abandoned the walk part-way would leave it half spent.

    **A short list is not a clean model.** :func:`coverage` says how much of the
    model this was able to read at all, and a caller reporting the flags
    without it reports a rate with no denominator.
    """
    return read_controls(model, sources)[0]


def coverage(model: SystemModel, sources: Mapping[str, str]) -> Coverage:
    """What the diagnostic read of this model, and what it could not read.

    Spends the same :data:`MAX_SCAN_WORK` as :func:`unbased_controls`, because
    it is the same scan: a caller wanting both takes this and reads
    :attr:`Coverage.flagged`, rather than paying the budget twice.
    """
    return read_controls(model, sources)[1]


class _Scan:
    """One call's token searches: cached, and spent against a budget.

    Two things make the naive walk cost what it does. It lower-cases the source
    once per element, and it searches the same token against the same source
    once per value that carries it — and a model's control vocabulary repeats
    heavily, because the systems it describes use the same few mechanisms. Both
    are memoized here, which is what takes the 150-element worst case from 13.5
    seconds of CPU to 71 milliseconds.

    Memoizing is not a bound: distinct tokens can keep arriving, and the cost
    of each is the length of the source it is searched against — both set by
    submitted text. :data:`MAX_SCAN_WORK` is the bound, and the fact that this
    is a diagnostic is what makes stopping affordable. Nothing downstream reads
    the result, so a job that runs out of budget loses a log line and keeps
    every guarantee it had — and every control the stopped scan reaches is
    reported as unmeasured, never as read clean.
    """

    def __init__(self, sources: Mapping[str, str]) -> None:
        self._sources = sources
        self._lowered: dict[str, str] = {}
        self._present: dict[tuple[str, str], bool] = {}
        self._work = 0
        self._stopped = False

    def echoes_none(self, control: StatedControl) -> bool | None:
        """Whether the cited source carries none of this value's tokens.

        ``None`` where the budget ran out before a search settled it: a
        token found present decides the control as echoed, every token
        searched and absent decides it as flagged, and a token the scan
        could not search leaves it undecided. A memoized token costs nothing
        and still decides, because it was searched once.
        """
        for token in control.tokens:
            present = self._present_in(token, control.source_label)
            if present is None:
                return None
            if present:
                return False
        return True

    def _present_in(self, token: str, label: str) -> bool | None:
        key = (token, label)
        if key in self._present:
            return self._present[key]
        if label not in self._lowered:
            self._lowered[label] = self._sources[label].lower()
        source = self._lowered[label]
        if self._work + len(source) > MAX_SCAN_WORK:
            self._stop()
            # Undecided, so the control is counted as unmeasured and never
            # flagged: a diagnostic that ran out of budget must not start
            # accusing, and must not claim to have read what it did not.
            return None
        self._work += len(source)
        self._present[key] = matches_term(token, source)
        return self._present[key]

    def _stop(self) -> None:
        """Say once that the rest of this model went unmeasured."""
        if self._stopped:
            return
        self._stopped = True
        logger.warning(
            "stated-control diagnostic stopped after reading %d source"
            " characters; the rest of this model is unmeasured",
            self._work,
        )
