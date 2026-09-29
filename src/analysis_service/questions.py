"""The open facts a report's conditional findings rest on, asked of the submitter.

A ``needs-info`` verdict names the facts an answer would settle. This module
turns them into questions, ranks them, and writes the answers back (#1225).

**Every open fact is asked, in the order that completes the most findings.**
The facts a finding's own grounds cite come first, in an order the critic
cannot change; the facts only the critic named follow
(``QA-2026-09-26-03-E7``). With the STRIDE lane closing a conditional finding
waits on several facts, and six questions settled about a quarter of them
where asking every one settled all (``QA-2026-09-26-03-E6``). So no cap is
chosen here. The next question is the one that completes the most findings, so
that each has every fact it waits on answered (``QA-2026-09-26-03-E17``). Each
question states how many findings are covered once it and every question
before it is answered. That count is coverage, not a verdict: only the resumed
run rules on a finding again. A rejected draft still ranks the questions, and
is left out of every count. A submitter answers from the top as far as they
choose.

**Only an open fact is asked, and only an asked fact takes an answer.** An
attribute is open where :func:`open_attribute` says so. An answer to an
attribute the model states is refused, unless an earlier round answered it,
so a submission cannot overwrite what the sources said. An answer to a fact
the job did not ask is refused by :func:`~analysis_service.links.check_answers`,
so a submission cannot write a question of its own into every lane prompt.

**An answer's line fits the span that quotes it.** :func:`fact_line` writes
each answer as one line of the answers Source, and the check refuses a line
longer than :data:`~analysis_service.assertions.MAX_QUOTE_CHARS`.

**An answer of** ``unknown`` **says the submitter does not know.** It writes
nothing structured, the fact stays open, and it covers no finding. It reaches
the lanes as its line of the answers Source.

**Four kinds of fact, and an answer to each is written by code.**

* An **attribute** of an element the model left ``unknown``. The answer is
  written onto the model the resumed run analyses, and the element's notes say
  the submitter answered it.
* An **assertion** row the sources left open. The answer replaces the row with
  a stated one, quoting its line of the answers Source.
* A **question** of one of the kinds in
  :data:`~analysis_service.question_kinds.QUESTION_KINDS`, about one element,
  or a free-text **subject** where no kind fits. Nothing structured can hold
  either answer, so it reaches the lanes only as its line of the answers
  Source.
* A **capability** of the whole application, answered yes or no. The answer
  replaces what the sources stated about it with a statement on the model
  that quotes its line of the answers Source.

An answer to a question this service asked settles the fact, even against the
sources (the maintainer's decision of 2026-09-25 on #1225).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from analysis_service.analysis import control_state
from analysis_service.assertions import (
    ABSENT,
    MAX_QUOTE_CHARS,
    REGISTRY,
    UNKNOWN,
    Assertion,
    AssertionCatalog,
    CatalogIssue,
    SupportSpan,
    answered,
    apply_projection,
    assertion_id,
    projected_attribute,
)
from analysis_service.capabilities import CAPABILITIES, lineage
from analysis_service.claims import FrameworkAnalysis, UnknownKey, UnknownRef
from analysis_service.open_facts import element_names, label_of
from analysis_service.question_kinds import QUESTION_KINDS, Facet
from analysis_service.sources import ANSWERS_LABEL, plain_name
from analysis_service.system_model import (
    ZONE_ATTRIBUTE,
    SystemModel,
    attribute_names,
)
from analysis_service.validation import validate

__all__ = [
    "ANSWERS_LABEL",
    "CONTROL_SUGGESTIONS",
    "FACET_ANSWERS",
    "MAX_FACT_ANSWERS",
    "YES_NO",
    "AnswerForm",
    "FactAnswer",
    "FactQuestion",
    "Fallback",
    "answer_choices",
    "answer_facets",
    "answer_form",
    "answer_suggestions",
    "answered_keys",
    "answered_model",
    "check_fact_answers",
    "facets_json",
    "fact_kind",
    "fact_line",
    "fact_questions",
    "fact_rows",
    "merged_facts",
    "open_attribute",
    "prepared_model",
    "question_fallback",
    "refuse_repeated_facts",
]

#: How many fact answers one submission carries. The largest report measured
#: raised 85 open facts, so this bounds the body above any real use.
MAX_FACT_ANSWERS = 200

#: The fields a flow's ID is built from. An answer cannot move one, because the
#: flow would then carry an ID its endpoints no longer derive.
_ENDPOINTS = frozenset({"source", "destination"})

FactKind = Literal["attribute", "assertion", "question", "subject", "capability"]
#: The fields of :class:`~analysis_service.claims.UnknownRef`, in key order.
_KEY_FIELDS = (
    "element_id",
    "attribute",
    "assertion",
    "subject",
    "question",
    "capability",
)
Basis = Literal["evidence", "critic"]
#: One finding: its framework and its claim ID.
Finding = tuple[str, str]


class FactAnswer(BaseModel):
    """One submitter answer to one open fact, named by the fact's key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: UnknownKey
    value: str = Field(min_length=1, max_length=1000)
    #: A ``facets`` kind's answer: each facet's ID and one of
    #: :data:`FACET_ANSWERS` or ``unknown``. The service writes ``value`` from
    #: it, so a submission sends one or the other.
    facets: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _facet_value(cls, data: Any) -> Any:
        """Write ``value`` from ``facets``, one facet a clause, in the kind's order."""
        if not isinstance(data, dict) or data.get("facets") is None:
            return data
        facets, key = data["facets"], data.get("key")
        question = key[4] if isinstance(key, list | tuple) and len(key) == 6 else ""
        kind = QUESTION_KINDS.get(question)
        if kind is None or kind.answer != "facets":
            raise ValueError("facets answer only a question kind that has facets")
        if not isinstance(facets, dict) or not facets:
            raise ValueError("a facet answer names at least one facet")
        ids = {facet.id for facet in kind.facets}
        for facet, answer in facets.items():
            if facet not in ids:
                raise ValueError(f"no facet {facet!r} of {question!r}")
            if answer not in (*FACET_ANSWERS, UNKNOWN):
                raise ValueError(
                    f"{answer!r} is not one of {', '.join(FACET_ANSWERS)} or"
                    f" {UNKNOWN} for {facet!r}"
                )
        given = [facet for facet in kind.facets if facet.id in facets]
        known = any(facets[facet.id] != UNKNOWN for facet in given)
        value = (
            "; ".join(f"{facet.question} {facets[facet.id]}" for facet in given)
            if known
            else UNKNOWN
        )
        if data.get("value") not in (None, value):
            raise ValueError("the service writes a facet answer's value")
        return {**data, "value": value}

    @field_validator("key", mode="before")
    @classmethod
    def _one_spelling(cls, key: Any) -> Any:
        """Bound each part, and blank every part the fact's spelling does not read.

        Two keys that differ only in a part nobody reads name one fact, and
        :func:`merged_facts` would keep both. A key that
        uses more than one spelling is left as sent, for the check to refuse.
        """
        if not isinstance(key, list | tuple) or len(key) != 6:
            return key
        ref = UnknownRef.model_validate(dict(zip(_KEY_FIELDS, key, strict=True)))
        match ref.spellings:
            case ("question",):
                return (ref.element_id, "", "", "", ref.question, "")
            case ("attribute",):
                return (ref.element_id, ref.attribute, "", "", "", "")
            case ("assertion",):
                return ("", "", ref.assertion, "", "", "")
            case ("subject",):
                return ("", "", "", ref.subject, "", "")
            case ("capability",):
                return ("", "", "", "", "", ref.capability)
        return ref.key

    @field_validator("value")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return plain_name(value)

    @property
    def kind(self) -> FactKind:
        return fact_kind(self.key)

    @property
    def known(self) -> bool:
        """False where the submitter answered that they do not know."""
        return self.value != UNKNOWN

    @property
    def settles(self) -> bool:
        """True where the answer settles its fact for a finding that waits on it.

        A facet answer settles it only once every facet has an answer other
        than "I don't know": a finding names the kind, not a facet, so a
        facet left unknown can be the one it waits on.
        """
        if self.facets is None:
            return self.known
        return all(
            self.facets.get(facet.id, UNKNOWN) != UNKNOWN
            for facet in answer_facets(self.key)
        )


def fact_line(fact: FactAnswer) -> str:
    """The answer as its line of the answers Source, which its span quotes."""
    element_id, attribute, assertion, subject, question, capability = fact.key
    if fact.kind == "capability":
        asked = CAPABILITIES[capability].question
        return f'Asked "{asked}", the answer is "{fact.value}".'
    if fact.kind == "question":
        kind = QUESTION_KINDS[question]
        asked = kind.template.format(element=element_id)
        return f'Asked "{asked}", the answer is "{fact.value}".'
    if fact.kind == "attribute":
        return f'The {attribute} of {element_id} is "{fact.value}".'
    if fact.kind == "assertion":
        return f'The open question {assertion} is answered "{fact.value}".'
    return f'Asked "{subject}", the answer is "{fact.value}".'


def refuse_repeated_facts(facts: Sequence[FactAnswer]) -> None:
    """Refuse two answers to one open fact in one submission.

    Refused rather than resolved by order: the service cannot know which of the
    two the submitter meant.
    """
    keys = [fact.key for fact in facts]
    if len(set(keys)) != len(keys):
        raise ValueError("facts answers one open fact twice")


def merged_facts(
    earlier: Sequence[FactAnswer], later: Sequence[FactAnswer]
) -> list[FactAnswer]:
    """A resumed job's fact answers: the parent's, with the new ones over them.

    **A facet answer adds to the earlier one.** A page sends only the facets
    the submitter chose, so a facet the later answer leaves out keeps its
    earlier answer, and a later answer to a facet replaces it. Every other
    answer replaces the earlier answer to its fact.
    """
    refuse_repeated_facts(later)
    merged = {fact.key: fact for fact in earlier}
    for fact in later:
        before = merged.get(fact.key)
        if fact.facets is not None and before is not None and before.facets:
            fact = FactAnswer.model_validate(
                {"key": fact.key, "facets": {**before.facets, **fact.facets}}
            )
        merged[fact.key] = fact
    return list(merged.values())


def answered_keys(answers: Sequence[FactAnswer]) -> frozenset[UnknownKey]:
    """The facts these answers answer in full, which a later round does not ask.

    An answer of "I don't know" answers its fact: asking it again in every
    round would ask a submitter who does not know until they stop. A facet
    answer answers its fact once every facet has an answer, "I don't know"
    included, so a later round still asks the facets left out.
    """
    return frozenset(
        answer.key
        for answer in answers
        if answer.facets is None
        or {facet.id for facet in answer_facets(answer.key)} <= answer.facets.keys()
    )


def open_attribute(model: SystemModel, element_id: str, attribute: str) -> bool:
    """True where this attribute is a fact the model leaves open.

    **The one reader of "may this attribute be asked, and answered".** Open is
    unverified by :func:`~analysis_service.analysis.control_state`, the
    reading the evidence catalog uses, or a zone the service inferred. The
    early list, the report list and the answer check all ask it.
    """
    element = model.get(element_id)
    value = getattr(element, attribute, None)
    unverified = isinstance(value, str) and control_state(value) == "unverified"
    assumed = (
        attribute == ZONE_ATTRIBUTE and element_id in model.assumed_zone_elements()
    )
    return element is not None and (unverified or assumed)


def prepared_model(model: SystemModel, catalog: AssertionCatalog | None) -> SystemModel:
    """The model with the catalog's projection applied, as the lanes read it.

    **The model every question reader asks :func:`open_attribute` of.** A
    paused job's checkpoint holds the model before ``prepare`` projects the
    catalog, so a fact the catalog states can still read ``unknown`` there.
    Asked of that model, the early list would offer the fact and the check
    would admit an answer that then supersedes what the sources stated. A
    report's model is projected already, and projecting it again changes
    nothing.
    """
    return model if catalog is None else apply_projection(model, catalog)[0]


def fact_kind(key: UnknownKey) -> FactKind:
    """Which of the five kinds of fact this key names."""
    element_id, attribute, assertion, _, question, capability = key
    if capability:
        return "capability"
    if question:
        return "question"
    if assertion:
        return "assertion"
    return "attribute" if element_id or attribute else "subject"


@dataclass(frozen=True)
class FactQuestion:
    """One open fact, the findings that wait on it, and the answers it takes."""

    key: UnknownKey
    kind: FactKind
    #: ``evidence`` where a finding's own grounds cite the fact, which does not
    #: change when the critic is sampled again; ``critic`` where only the
    #: critic's verdict names it, which does.
    basis: Basis
    #: What a reader sees: an element's name and the attribute, or the subject.
    label: str
    #: How many still-open findings cite it when it is asked.
    cited_by: int
    #: For how many findings every fact is answered once it and every question
    #: before it is answered. Coverage, not a verdict.
    covered_so_far: int
    #: The answers it takes, or empty where the answer is free text.
    choices: tuple[str, ...]
    #: How a page takes the answer; see :data:`AnswerForm`.
    form: AnswerForm
    #: Common mechanisms a ``control`` answer may start from.
    suggestions: tuple[str, ...]
    #: The parts a ``facets`` answer is given in.
    facets: tuple[Facet, ...]
    #: Every finding that waits on it, as ``framework/claim``. A finding is
    #: covered once every question that names it has an answer, whichever
    #: questions those are.
    findings: tuple[str, ...]
    #: True where the pause showed this question and got no answer, so a page
    #: can say it was skipped before the analysis.
    asked_before: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "kind": self.kind,
            "basis": self.basis,
            "asked_before": self.asked_before,
            "label": self.label,
            "cited_by": self.cited_by,
            "covered_so_far": self.covered_so_far,
            "choices": list(self.choices),
            "form": self.form,
            "suggestions": list(self.suggestions),
            "facets": facets_json(self.facets),
            "findings": list(self.findings),
        }


def _closed(model: SystemModel, element_id: str, attribute: str) -> tuple[str, ...]:
    """A literal-typed attribute's legal values, except ``unknown``."""
    element = model.get(element_id)
    if element is None:
        return ()
    annotation = type(element).model_fields[attribute].annotation
    if get_origin(annotation) is not Literal:
        return ()
    return tuple(value for value in get_args(annotation) if value != UNKNOWN)


#: The answers an attribute takes, where they are not free text. Keyed by
#: attribute, so an attribute with a closed set it does not name here falls
#: back to its own annotation rather than to free text.
_ATTRIBUTE_CHOICES: Mapping[str, Callable[[SystemModel, str], tuple[str, ...]]] = (
    MappingProxyType(
        {
            "data_classification": lambda model, element_id: tuple(
                sorted(REGISTRY["data-classification"].terms)
            ),
            "trust_zone": lambda model, element_id: tuple(
                boundary.id for boundary in model.trust_boundaries
            ),
        }
    )
)


def answer_choices(
    key: UnknownKey, model: SystemModel, catalog: AssertionCatalog | None
) -> tuple[str, ...]:
    """The values an answer to this fact may take, or empty for free text."""
    element_id, attribute, assertion, _, question, _ = key
    kind = fact_kind(key)
    if kind == "capability":
        return YES_NO
    if kind == "question":
        asked = QUESTION_KINDS.get(question)
        return YES_NO if asked is not None and asked.answer == "yes-no" else ()
    if kind == "attribute":
        special = _ATTRIBUTE_CHOICES.get(attribute)
        return (
            special(model, element_id)
            if special
            else _closed(model, element_id, attribute)
        )
    if kind == "assertion" and catalog is not None:
        row = _answered_row(catalog, assertion)
        if row is None:
            return ()
        predicate = REGISTRY.get(row.predicate)
        if predicate is None:
            return ()
        if predicate.value == "term":
            return (*sorted(predicate.terms), ABSENT)
        if predicate.value == "reference":
            return tuple(
                subject.id
                for subject in catalog.subjects
                if subject.type in predicate.refers_to
            )
    return ()


#: The answers a ``yes-no`` question kind takes.
YES_NO: tuple[str, ...] = ("yes", "no")

#: The answers a facet takes, beside ``unknown``.
FACET_ANSWERS: tuple[str, ...] = ("yes", "no", "not applicable")

#: Starting points for a control answered in free text: common mechanisms, so
#: a submitter can pick one and add what matters, such as how a key is rotated.
#: Keyed by every control attribute with no closed set, which
#: ``tests/test_questions.py`` derives from the element classes. The words come
#: from the corpus's signed models where they name one, and a suggestion is
#: never an answer until the submitter sends it.
CONTROL_SUGGESTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "authentication": (
            "session cookie after a password login",
            "password and a second factor",
            "company SSO",
            "OAuth 2.0 bearer token",
            "API key in a header",
            "service account",
            "mutual TLS",
            "pre-shared key",
        ),
        "encryption_in_transit": (
            "HTTPS",
            "TLS 1.3",
            "TLS 1.2",
            "mutual TLS",
            "SSH transport",
            "VPN tunnel",
        ),
        "encryption_at_rest": (
            "provider-managed key",
            "customer-managed key (CMEK)",
            "full-disk encryption",
            "database transparent data encryption",
        ),
    }
)

#: How a page takes an answer: one of ``choices``; a control, where the
#: submitter says there is none, does not know, or names the mechanism in
#: text; or free text.
AnswerForm = Literal["choice", "control", "facets", "text"]


def answer_form(
    key: UnknownKey, model: SystemModel, catalog: AssertionCatalog | None
) -> AnswerForm:
    """How a page takes an answer to this fact."""
    if answer_facets(key):
        return "facets"
    if answer_choices(key, model, catalog):
        return "choice"
    control = fact_kind(key) == "attribute" and key[1] in CONTROL_SUGGESTIONS
    return "control" if control else "text"


def answer_facets(key: UnknownKey) -> tuple[Facet, ...]:
    """The facets a question kind is answered in, or empty."""
    kind = QUESTION_KINDS.get(key[4]) if fact_kind(key) == "question" else None
    return () if kind is None else kind.facets


def answer_suggestions(key: UnknownKey) -> tuple[str, ...]:
    """Common mechanisms for a control answered in free text, or empty."""
    return CONTROL_SUGGESTIONS.get(key[1], ()) if fact_kind(key) == "attribute" else ()


def facets_json(facets: Sequence[Facet]) -> list[dict[str, str]]:
    """The facets as a page reads them."""
    return [{"id": facet.id, "question": facet.question} for facet in facets]


def _answered_row(catalog: AssertionCatalog, identity: str) -> Assertion | None:
    """The row an answer to the open row ``identity`` writes over, if any.

    **The one reader of "which row does this answer settle".** That is the open
    row itself, or the stated row an earlier answer wrote in its place. The
    second is the row a resumed job's checkpoint holds, and a later round
    writes it again, so that its quote reads the answers Source this round
    composed. A row the sources settled is neither, so no answer names it.
    """
    for entry in catalog.entries:
        if entry.value == UNKNOWN:
            reopened = entry
        elif answered(entry):
            reopened = entry.model_copy(update={"value": UNKNOWN})
        else:
            continue
        if assertion_id(reopened) == identity:
            return entry
    return None


def _greedy(open_facts: Mapping[Finding, set[UnknownKey]]) -> list[UnknownKey]:
    """The facts in the order that completes the most findings, asked one by one.

    Where one question completes findings, the next is the one that completes
    the most. Where none does, the next are the facts of the finding with the
    fewest left, the most cited first. A tie goes to more citations, then to
    the lower key, so one input always gives one order. Measured against
    asking the most cited fact first, it covers more findings at every
    depth (``QA-2026-09-26-03-E17``).
    """
    left = {finding: set(keys) for finding, keys in open_facts.items() if keys}
    order: list[UnknownKey] = []
    while left:
        cites = Counter(key for keys in left.values() for key in keys)
        completes = Counter(
            next(iter(keys)) for keys in left.values() if len(keys) == 1
        )
        if completes:
            chosen = [
                min(completes, key=lambda key: (-completes[key], -cites[key], key))
            ]
        else:
            nearest = min(left.values(), key=lambda keys: (len(keys), sorted(keys)))
            chosen = sorted(nearest, key=lambda key: (-cites[key], key))
        order.extend(chosen)
        asked = set(chosen)
        left = {finding: keys - asked for finding, keys in left.items() if keys - asked}
    return order


def fact_questions(
    analyses: Sequence[FrameworkAnalysis],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    earlier: Sequence[FactAnswer] = (),
) -> tuple[FactQuestion, ...]:
    """Every open fact a report's findings rest on, the most completing first.

    ``earlier`` is the fact answers of the earlier rounds. A fact they answer
    in full (:func:`answered_keys`) is not asked again. A finding that waits on
    a fact they answer without settling it (:attr:`FactAnswer.settles`), such
    as "I don't know" or a facet answered so, is not counted, because no
    answer in this list can cover it.

    **Two sections, and the first does not depend on the critic.** The
    evidence section ranks the open facts each finding's own grounds cite, for
    every finding the lanes wrote, rejected ones included. A critic that is
    sampled again rules differently about a third of the time, and a list read
    from its verdicts shared 7 of 18 questions with the list from a second
    sample; a list read from the grounds is the same for both, and settled 82%
    as many findings as each sample's own list (``QA-2026-09-26-03-E7``). The
    critic section follows, with the facts only the critic named: its
    free-text subjects, mostly, which change from one sample to the next.

    Each question counts the findings of every framework together, because one
    answer settles a fact for every framework that cites it. ``cited_by`` and
    ``covered_so_far`` count only the conditional findings, which are the ones
    waiting on an answer, by their grounds and their verdict. A confirmed
    finding and a rejected draft rank the questions and are not counted: the
    first waits on nothing, and no answer brings the second into the report.

    An attribute the model states is not asked, even where the critic names
    it, because :func:`check_fact_answers` refuses an answer to it.
    """
    evidence: dict[Finding, set[UnknownKey]] = {}
    named: dict[Finding, set[UnknownKey]] = {}
    refs: dict[UnknownKey, UnknownRef] = {}
    conditional: set[Finding] = set()
    prepared = prepared_model(model, catalog)
    done = answered_keys(earlier)
    unknown = {answer.key for answer in earlier if not answer.settles} & done
    for block in analyses:
        for claim in block.all_claims():
            finding = (block.framework, claim.id)
            cites = [*claim.unknown_grounds(), *claim.verdict.related_unknowns]
            stuck = bool(unknown & {ref.key for ref in cites})
            if claim.verdict.status == "needs-info" and not stuck:
                conditional.add(finding)
            grounds = [
                ref
                for ref in claim.unknown_grounds()
                if _open(ref, prepared) and ref.key not in done
            ]
            evidence[finding] = {ref.key for ref in grounds}
            cited = list(grounds)
            if claim.verdict.status == "needs-info":
                verdict = [
                    ref
                    for ref in claim.verdict.related_unknowns
                    if _open(ref, prepared) and ref.key not in done
                ]
                named[finding] = {ref.key for ref in verdict}
                cited += verdict
            for ref in cited:
                refs.setdefault(ref.key, ref)
    first = _greedy(evidence)
    asked_first = set(first)
    later = _greedy({f: keys - asked_first for f, keys in named.items()})
    waiting = {
        finding: evidence.get(finding, set()) | named.get(finding, set())
        for finding in evidence.keys() | named.keys()
    }
    waiting = {
        finding: keys
        for finding, keys in waiting.items()
        if keys and finding in conditional
    }
    names = element_names(model)
    asked: list[FactQuestion] = []
    answered: set[UnknownKey] = set()
    sections: tuple[tuple[Basis, list[UnknownKey]], ...] = (
        ("evidence", first),
        ("critic", later),
    )
    for basis, keys in sections:
        for key in keys:
            cited_by = sum(
                1
                for facts in waiting.values()
                if key in facts and not facts <= answered
            )
            answered.add(key)
            asked.append(
                FactQuestion(
                    key=key,
                    kind=fact_kind(key),
                    basis=basis,
                    label=label_of(refs[key], names),
                    cited_by=cited_by,
                    covered_so_far=sum(
                        1 for facts in waiting.values() if facts <= answered
                    ),
                    choices=answer_choices(key, model, catalog),
                    form=answer_form(key, model, catalog),
                    suggestions=answer_suggestions(key),
                    facets=answer_facets(key),
                    findings=tuple(
                        sorted(
                            f"{framework}/{claim}"
                            for (framework, claim), facts in waiting.items()
                            if key in facts
                        )
                    ),
                )
            )
    return tuple(asked)


def _open(ref: UnknownRef, model: SystemModel) -> bool:
    """False for an attribute the model states; every other fact is open."""
    element_id, attribute, *_ = ref.key
    return fact_kind(ref.key) != "attribute" or open_attribute(
        model, element_id, attribute
    )


@dataclass(frozen=True)
class Fallback:
    """How the critic named the open facts that have no place in the model.

    ``typed`` names a kind from the question table and an element; ``free_text``
    fell back to a ``subject``. The share that fell back is what says whether
    the table is enough (``QA-2026-09-26-03-E8``): a low, steady share means it
    is, and the fallback texts name the kinds it lacks.
    """

    typed: int
    free_text: int

    @property
    def rate(self) -> float | None:
        total = self.typed + self.free_text
        return None if total == 0 else round(self.free_text / total, 3)

    def to_json(self) -> dict[str, object]:
        return {"typed": self.typed, "free_text": self.free_text, "rate": self.rate}


def question_fallback(analyses: Sequence[FrameworkAnalysis]) -> Fallback:
    """Count a report's typed and free-text open facts across its verdicts."""
    refs = [
        ref
        for block in analyses
        for claim in block.all_claims()
        for ref in claim.verdict.related_unknowns
    ]
    return Fallback(
        typed=sum(1 for ref in refs if ref.spellings == ("question",)),
        free_text=sum(1 for ref in refs if ref.spellings == ("subject",)),
    )


def check_fact_answers(
    answers: Sequence[FactAnswer],
    model: SystemModel,
    catalog: AssertionCatalog | None,
    earlier: Sequence[FactAnswer] = (),
) -> None:
    """Refuse an answer that names no open fact here, or a value the fact cannot hold.

    Checked before a resumed run is admitted, against the model and catalog
    the questions were asked about, so a wrong answer costs nothing. A key
    names one fact in one of the five spellings, as
    :attr:`~analysis_service.claims.UnknownRef.spellings` reads them.

    ``earlier`` is what the earlier rounds answered. An attribute one of them
    answered is stated now, and may be answered again with another value. An
    ``unknown`` answer to a fact an earlier round settled is refused, because
    it would reopen the fact.
    """
    prepared = prepared_model(model, catalog)
    answered_before = {answer.key for answer in earlier}
    settled_before = {answer.key for answer in earlier if answer.known}
    stated = prepared.capability_facts()
    for answer in answers:
        element_id, attribute, assertion, subject, question, capability = answer.key
        ref = UnknownRef.model_construct(
            element_id=element_id,
            attribute=attribute,
            assertion=assertion,
            subject=subject,
            question=question,
            capability=capability,
        )
        if len(ref.spellings) != 1:
            raise ValueError(f"an answer's key names one fact, not {answer.key!r}")
        kind = answer.kind
        if kind == "question":
            if question not in QUESTION_KINDS or model.get(element_id) is None:
                raise ValueError(f"no question {question!r} about {element_id!r}")
            if answer_facets(answer.key) and answer.facets is None and answer.known:
                raise ValueError(f"{question!r} is answered in its facets")
        elif kind == "attribute":
            element = model.get(element_id)
            answerable = () if element is None else attribute_names(element)
            if attribute not in answerable or attribute in _ENDPOINTS:
                raise ValueError(f"no attribute {attribute!r} on {element_id!r}")
            if answer.key not in answered_before and not open_attribute(
                prepared, element_id, attribute
            ):
                raise ValueError(
                    f"{attribute!r} on {element_id!r} is stated, so it takes no answer"
                )
        elif kind == "assertion" and (
            catalog is None or _answered_row(catalog, assertion) is None
        ):
            raise ValueError(f"no open assertion row {assertion!r}")
        elif kind == "capability":
            if capability not in CAPABILITIES:
                raise ValueError(f"no capability {capability!r}")
            known = stated.get(capability)
            if (
                answer.key not in answered_before
                and known is not None
                and known.state != UNKNOWN
            ):
                raise ValueError(
                    f"the sources state {capability!r}, so it takes no answer"
                )
        if len(fact_line(answer)) > MAX_QUOTE_CHARS:
            raise ValueError(
                f"an answer's line may hold {MAX_QUOTE_CHARS} characters; shorten"
                f" the answer to {answer.key!r}"
            )
        if not answer.known:
            if answer.key in settled_before:
                raise ValueError(
                    "an earlier answer settled this fact; send a value to change it"
                )
            continue
        choices = answer_choices(answer.key, model, catalog)
        if choices and answer.value not in choices:
            raise ValueError(
                f"{answer.value!r} is not one of {', '.join(choices)} for"
                f" {attribute or assertion or subject or capability!r}"
            )
    _check_attribute_values(answers, model)
    _check_capability_lineage(answers, prepared, earlier)


def _check_capability_lineage(
    answers: Sequence[FactAnswer],
    prepared: SystemModel,
    earlier: Sequence[FactAnswer],
) -> None:
    """Refuse a round that would leave a capability present under an absent ancestor.

    **Checked on the model the round produces**, with every earlier answer and
    every source statement in it, so a contradiction is refused whether it
    arrives in one round, across two, or against what the sources state. A
    contradiction this round's answers take no part in is left as it is:
    :func:`~analysis_service.capabilities.resolve` keeps it for a person to
    settle.
    """
    answering = {
        answer.key[5]
        for answer in answers
        if answer.kind == "capability" and answer.known
    }
    if not answering:
        return
    held = answered_model(prepared, merged_facts(earlier, answers)).capability_facts()
    for key, fact in held.items():
        if fact.state != "present" or key not in CAPABILITIES:
            continue
        for ancestor in lineage(key):
            absent = ancestor in held and held[ancestor].state == "absent"
            if absent and answering & {key, ancestor}:
                raise ValueError(
                    f"{key!r} is part of {ancestor!r}, and these answers would"
                    f" leave {key!r} present while {ancestor!r} is absent;"
                    " answer both"
                )


def _check_attribute_values(answers: Sequence[FactAnswer], model: SystemModel) -> None:
    """Refuse an attribute answer the validity gate would refuse in that field.

    A resumed run starts at ``prepare`` and never meets the gate, so the gate's
    own rules are asked here: a field's length, and a control that is blank or
    opens with a negation other than ``none``, which
    :func:`~analysis_service.analysis.control_state` would read as stated.
    """
    attributes = [
        answer for answer in answers if answer.kind == "attribute" and answer.known
    ]
    if not attributes:
        return
    try:
        answered = answered_model(model, attributes)
    except ValidationError as error:
        first = error.errors()[0]
        raise ValueError(
            f"an answer does not fit {first['loc'][-1]!r}: {first['msg']}"
        ) from None
    asked = {(answer.key[0], answer.key[1]) for answer in attributes}
    for issue in validate(answered):
        if (issue.element_id, issue.field) in asked:
            raise ValueError(issue.message)


def answered_model(model: SystemModel, answers: Sequence[FactAnswer]) -> SystemModel:
    """The model with every attribute and capability answer written in.

    An attribute answer settles the attribute, so an
    :class:`~analysis_service.system_model.Assumption` that named it as inferred
    is removed. The note keeps who settled it. An ``unknown`` answer writes only
    the note, so the attribute stays open.

    A capability answer replaces every statement about that capability with
    one that quotes the answer's line of the answers Source: "yes" is
    ``present`` and "no" is ``absent``. An ``unknown`` answer writes nothing,
    so the capability stays open.
    """
    data = model.model_dump(mode="json")
    by_id = {
        element["id"]: element
        for group in (
            "external_entities",
            "processes",
            "data_stores",
            "data_flows",
            "trust_boundaries",
        )
        for element in data.get(group, [])
    }
    for answer in answers:
        if answer.kind != "attribute":
            continue
        element_id, attribute, *_ = answer.key
        element = by_id.get(element_id)
        if element is None:
            continue
        if answer.known:
            element[attribute] = answer.value
            note = f'The submitter answered {attribute}: "{answer.value}".'
        else:
            note = f"The submitter does not know {attribute}."
        # A resumed job's checkpoint already holds an earlier round's answers.
        notes = element.get("notes", "")
        if note not in notes:
            element["notes"] = f"{notes} {note}".strip()[:2000]
    answered = {
        answer.key[:2]
        for answer in answers
        if answer.kind == "attribute" and answer.known
    }
    data["assumptions"] = [
        assumption
        for assumption in data.get("assumptions", [])
        if (assumption["element_id"], assumption["attribute"]) not in answered
    ]
    capabilities = {
        answer.key[5]: answer
        for answer in answers
        if answer.kind == "capability" and answer.known
    }
    data["capabilities"] = [
        statement
        for statement in data.get("capabilities", [])
        if statement["capability"] not in capabilities
    ] + [
        {
            "capability": key,
            "state": "present" if answer.value == "yes" else "absent",
            "source_excerpt": fact_line(answer),
            "source_label": ANSWERS_LABEL,
        }
        for key, answer in capabilities.items()
    ]
    return SystemModel.model_validate(data)


def fact_rows(
    catalog: AssertionCatalog,
    answers: Sequence[FactAnswer],
    spans: Sequence[SupportSpan],
) -> tuple[AssertionCatalog, list[CatalogIssue]]:
    """The catalog with each answer written over the rows it settles.

    ``spans`` are where each answer's line sits in the answers Source, in the
    order of ``answers``. An assertion answer replaces the open row it answers,
    and the stated row keeps the open row's subject, predicate and scope, so it
    answers exactly what was asked. An earlier round's answer is written again
    over the row it wrote, so its quote reads this round's answers Source.

    **An attribute answer removes every unscoped row that reaches its
    attribute.** The answer settles the attribute, and such a row left beside it
    would reach the lanes as the opposite fact. Each removed row is named by a
    ``superseded-by-answer`` issue, which later rounds keep. **A scoped row
    stays**, because it states a narrower fact that the answer does not settle.
    Where one stays, the answer is written as an unscoped row beside it, which
    :func:`~analysis_service.assertions.project` lets outrank it.
    """
    replaced: dict[str, Assertion] = {}
    issues: list[CatalogIssue] = []
    attributes = {
        answer.key[:2]: (answer, span)
        for answer, span in zip(answers, spans, strict=True)
        if answer.kind == "attribute" and answer.known
    }

    def reached(entry: Assertion) -> tuple[str, str]:
        return entry.subject, projected_attribute(entry.predicate, entry.subject)

    superseded = {
        assertion_id(entry)
        for entry in catalog.entries
        if reached(entry) in attributes and not entry.scope
    }
    issues.extend(
        CatalogIssue(
            code="superseded-by-answer",
            message="the submitter's answer to this row's attribute replaced it",
            subject=entry.subject,
            assertion=assertion_id(entry),
        )
        for entry in catalog.entries
        if assertion_id(entry) in superseded and not answered(entry)
    )
    scoped = sorted(
        (
            entry
            for entry in catalog.entries
            if reached(entry) in attributes and entry.scope
        ),
        key=assertion_id,
    )
    written: dict[tuple[str, str], Assertion] = {}
    for entry in scoped:
        answer, span = attributes[reached(entry)]
        value = ABSENT if control_state(answer.value) == "absent" else answer.value
        written.setdefault(
            reached(entry),
            Assertion(
                subject=entry.subject,
                predicate=entry.predicate,
                value=value,
                basis="stated",
                support=[span],
            ),
        )
    for answer, span in zip(answers, spans, strict=True):
        if answer.kind != "assertion" or not answer.known:
            continue
        row = _answered_row(catalog, answer.key[2])
        if row is None:
            issues.append(
                CatalogIssue(
                    code="unmatched-answer",
                    message=f"no open row {answer.key[2]!r} in this catalog, so"
                    " the answer placed nothing",
                )
            )
            continue
        replaced[assertion_id(row)] = row.model_copy(
            update={
                "value": answer.value,
                "basis": "stated",
                "reason": None,
                "support": [span],
            }
        )
    entries = [
        replaced.get(assertion_id(entry), entry)
        for entry in catalog.entries
        if assertion_id(entry) not in superseded
    ]
    return (
        catalog.model_copy(update={"entries": [*entries, *written.values()]}),
        issues,
    )
