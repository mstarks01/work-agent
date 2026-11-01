"""The input a job is built from, and how it reaches a model.

A **Source** is one piece of untrusted input text: a ``kind``, a caller-supplied
``label``, and the ``text`` itself. A job carries an ordered, non-empty list of
them. A transcript-only job is a one-element list with no special case, and so
is a description-only job. There is no separate single-text path.

``kind`` is a closed vocabulary, and it is load-bearing: it selects the register
:func:`render_sources` names around that source's text. A new kind needs a
register, and a change to the extraction prompt where the register alone does
not say how to read the text.

``label`` is the key a ``source_excerpt`` cites, which is what keeps the
traceability chain intact across any number of sources — threat, then element,
then the user's own words, then which source spoke them. It is therefore unique
within a job, because a citation that named two sources at once would resolve
while pointing nowhere.

Order is presentation order only. The contract makes no authority claim, so an
earlier source does not override a later one.

Rendering is the whole untrusted-input surface (OWASP LLM01). Every caller byte
lands inside a fenced block, and the fence is sized to its own content, so a
submitted transcript cannot close the block it sits in and continue in
instruction position. The label rides inside the fence for the same reason: it
is caller-controlled, so it must never sit on the marker line. What is left
outside carries only this module's own bytes — an index, a count, and the
register.

The render is a pure function of the sources, called from the one place the
graph is driven, so the service and the eval harness cannot show the same job to
a model differently.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from analysis_service.grounding import normalize

# Closed and load-bearing: each kind names the register rendered around its
# text. A new kind needs a register in _REGISTERS, and an edit to
# prompts/extract.md where the register alone does not say how to read it.
SourceKind = Literal["description", "transcript", "diagram", "answers"]

# What a caller gets when they use the convenience constructors rather than
# naming a source themselves. The wire never defaults a label — an unnamed
# source there is a caller error, because the label is a citation key and
# inventing one on the caller's behalf puts a name they never chose into the
# report.
DEFAULT_DESCRIPTION_LABEL = "System description"
DEFAULT_TRANSCRIPT_LABEL = "Call transcript"

#: The label of the Source the submitter's answers become. The answers Source
#: carries it and no other Source does: :class:`Source` refuses any other
#: pairing of the label and the kind, so a reader that sees the label reads
#: the answers Source.
ANSWERS_LABEL = "Answers to link questions"

# A label is a citation key, so it is bounded but never rewritten: trimming or
# escaping one would mean the report cites something the caller did not submit.
MAX_LABEL_CHARS = 200

# A system name is optional metadata rather than a citation key, so unlike a
# label it *is* trimmed: nobody cites it, and a caller who pads one means the
# name without the padding. Measured after the trim, so the bound counts what a
# reader sees. See :func:`clean_system_name`.
MAX_SYSTEM_NAME_CHARS = 200

# Also rejected in a label, by Unicode general category. ``Cc`` is the C0 and C1
# control characters; ``Cf`` the invisible formatting ones — the bidi overrides
# and isolates, the zero-width space and joiners, the soft hyphen, the BOM.
#
# Both render as something other than what they are, and a label is rendered as
# chrome beside a quote the report attributes to the caller. On the loopback
# webapp the submitter is both attacker and victim and it hardly matters; for an
# integrator rendering a *third party's* submission, a label that reorders the
# text around it is UI spoofing — which the viewer's ``textContent`` rule does
# not reach, because nothing here is executing.
#
# Rejected rather than stripped, for the reason ``MAX_LABEL_CHARS`` gives: a
# label is bounded but never rewritten. Normalising one would also silently
# break the uniqueness check and the gate that resolves a ``source_excerpt``'s
# ``source_label`` against the job's labels.
#
# Categories rather than an enumerated list of code points: a list is a thing
# that rots as Unicode grows, and the property is what actually matters.
FORMATTING_CATEGORIES = frozenset({"Cc", "Cf"})


def carries_line_break(value: str) -> bool:
    """Whether ``value`` spans lines, by any terminator ``str`` splits on.

    **Asked of the producer rather than of a list.** ``str.splitlines`` splits
    on ten characters — the newline and carriage return, the vertical tab and
    form feed, the three information separators, U+0085, and the two Unicode
    line separators a caller can paste without seeing. A tuple of four listed
    the ones outside the ``Cc`` category and left the other six to the
    formatting-category check beside it, so a caller that imported the tuple
    alone got half a rule: the reading document's line type did, and every C1
    terminator passed into a value it joins into Markdown behind ``- ``.

    Comparing the split against the value itself answers for whatever
    ``splitlines`` does, and catches a *trailing* break too — ``"a\n"`` splits
    to one line and is still two. A tab is not a terminator and is not refused
    here; a value that must also carry no control character asks
    :func:`plain_name`, which puts both questions together.
    """
    return bool(value) and value.splitlines() != [value]


def _carries_formatting(value: str) -> bool:
    """Whether ``value`` holds a control, bidi or zero-width character."""
    return any(unicodedata.category(char) in FORMATTING_CATEGORIES for char in value)


# All that ``kind`` still selects: one phrase telling the model what register
# the text below is in. It sits outside the fence, so it is this module's bytes
# and never the caller's. The diagram phrase also says how to read a diagram,
# so a job with no diagram source sends the same prompt as before the kind.
_REGISTERS: dict[SourceKind, str] = {
    "description": "a written description of the system",
    "transcript": "a transcribed conversation about the system",
    "diagram": (
        "a diagram of the system in Mermaid text. A node can be an element, and"
        " an arrow can be a data flow in the direction of the arrow. A subgraph"
        " or a group is a trust zone only where its label says so. A junction"
        " is a point where edges meet, and never an element. Where a comment in the"
        " diagram states its shape convention, a node's shape states its type"
        " as the comment says. Otherwise the shape of a node states no"
        " attribute. An excerpt quotes the line of the diagram as written."
        " Where the block below the diagram gives the structure Mermaid's"
        " parser read, model each of its nodes as an element named with the"
        " node's label, and each of its edges as a data flow between those"
        " elements in the direction of its arrow"
    ),
    "answers": "the submitter's answers to questions this service asked",
}

# Separates the label from the text inside a block. Positional, not parsed: the
# label is single-line by construction, so the text is simply everything from
# the third line on.
_HEADER_RULE = "----"

_BACKTICK_RUN = re.compile(r"`+")


#: What a job's input actually holds, as kinds of evidence a **Framework** can
#: settle a claim from. One entry today, because a **Source** is text and this
#: service accepts nothing else.
#:
#: A constant rather than a field on the job, and that is the honest shape while
#: there is one value: a field would imply a caller chooses, and none can.
#: **It is threaded as an argument everywhere it is read**, so the day the
#: service accepts source code or a configuration dump, this becomes a job field
#: and no package changes — which is the whole reason a package declares what
#: kind of evidence settles its claims rather than declaring what it cannot
#: answer.
CARRIED_EVIDENCE_KINDS: tuple[str, ...] = ("prose",)


def check_answer_quotes(origin: str, labels: Sequence[str]) -> None:
    """Raise :class:`ValueError` unless the row of ``origin`` that quotes the
    source labels ``labels`` quotes the answers Source as its origin says.

    **The one rule of an answer's quotes.** A row whose origin is ``answer``
    quotes the answers Source in each label, and in at least one. A row of
    another origin quotes it in none. So the writer of a row and its quotes
    cannot disagree. An :class:`~analysis_service.assertions.Assertion` and a
    :class:`~analysis_service.system_model.CapabilityStatement` each ask it.
    """
    quoted = [label == ANSWERS_LABEL for label in labels]
    if origin == "answer":
        agrees = bool(quoted) and all(quoted)
    else:
        agrees = not any(quoted)
    if not agrees:
        raise ValueError(
            "every quote of an answer row is of the answers Source, and no quote"
            f" of another row is; this {origin} row quotes {sorted(set(labels))}"
        )


class Source(BaseModel):
    """One piece of untrusted input text a job is built from.

    Well-formedness is enforced here rather than at each entry point, so the
    HTTP route and the in-process engine reject the same shapes for the same
    reasons.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SourceKind
    label: str = Field(max_length=MAX_LABEL_CHARS)
    text: str

    @field_validator("label")
    @classmethod
    def _single_line_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("label must not be blank")
        # :func:`plain_name` is the rule, called rather than inlined: a label
        # and a caller-supplied name ask one question, and this validator held
        # its own copy of both halves.
        return plain_name(value)

    @field_validator("text")
    @classmethod
    def _non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def _reserved_label(self) -> Self:
        """The answers source carries :data:`ANSWERS_LABEL`, and no other does.

        **The one rule of the reserved label.** A quote's ``source_label``
        is all that a ground carries, so each reader of an answer's line
        reads the label, and this rule makes the label mean the kind.
        """
        if (self.kind == "answers") != (self.label == ANSWERS_LABEL):
            raise ValueError(
                f"only the answers source carries the label {ANSWERS_LABEL!r},"
                " and this service composes it"
            )
        return self

    @classmethod
    def description(cls, text: str, *, label: str = DEFAULT_DESCRIPTION_LABEL) -> Self:
        """A written description, labelled for the caller who did not name one."""
        return cls(kind="description", label=label, text=text)

    @classmethod
    def transcript(cls, text: str, *, label: str = DEFAULT_TRANSCRIPT_LABEL) -> Self:
        """A transcribed conversation, labelled for the caller who did not."""
        return cls(kind="transcript", label=label, text=text)

    def size_bytes(self) -> int:
        """This source's contribution to the job's budget, in UTF-8 bytes."""
        return len(self.text.encode("utf-8"))

    def digest(self) -> str:
        """This source's content digest — :func:`text_digest` of its text."""
        return text_digest(self.text)


def text_digest(text: str) -> str:
    """The sha256 hex digest of ``text``, over its UTF-8 bytes.

    **What a support span pins a quote to.** A span names offsets into the
    exact retained text, so a source that changes underneath it names different
    words. The digest is what lets a reader see that, and it is recomputed and
    compared rather than trusted: nothing but this function writes one.

    Over the bytes rather than over a normalized form, because the question is
    whether the submission changed, and every rung of the citation ladder is a
    concession about *quoting* rather than about identity.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def total_bytes(sources: Sequence[Source]) -> int:
    """The whole job's size in UTF-8 bytes — what the budget is spent against.

    Bytes rather than tokens because each model tier selects its vendor
    independently, so a token budget would make the public contract depend on
    which vendor a deployment happens to run.
    """
    return sum(source.size_bytes() for source in sources)


@dataclass(frozen=True)
class LimitBreach:
    """Why a submission was refused before any model ran.

    ``rung`` is what the breach *is*, so each surface can map it to its own
    vocabulary — an HTTP status, an engine exception — without re-deriving the
    reason. ``message`` is caller-facing and names no internal detail.
    """

    rung: Literal["empty", "duplicate-label", "count", "total"]
    message: str


@dataclass(frozen=True)
class SourceLimits:
    """One deployment's bounds on what a single job may carry.

    Held as a value rather than read from config at each check, so the HTTP
    route, the in-process engine and any future entry point enforce the same
    numbers — the ones their deployment resolved once at startup.
    """

    max_total_bytes: int
    max_sources: int

    def breach(self, sources: Sequence[Source]) -> LimitBreach | None:
        """The first bound ``sources`` breaks, or ``None`` if it fits.

        Ordered shape before size: an empty list is not a small job, it is a
        job with no input, and saying so is more use than quoting a byte count
        of zero against a cap. A repeated label is shape too — it is wrong at
        any size — so it is answered before either budget.
        """
        if not sources:
            return LimitBreach("empty", "a job carries at least one source")
        repeated = self._repeated_label(sources)
        if repeated is not None:
            at, first = repeated
            return LimitBreach(
                "duplicate-label",
                "source labels must be unique within a job; "
                f"sources[{at}] repeats the label of sources[{first}]",
            )
        if len(sources) > self.max_sources:
            return LimitBreach(
                "count",
                f"{len(sources)} sources submitted, over the "
                f"{self.max_sources} source limit",
            )
        total = total_bytes(sources)
        if total > self.max_total_bytes:
            return LimitBreach(
                "total",
                f"sources total {total} bytes, over the "
                f"{self.max_total_bytes} byte limit; "
                f"per source: {self._breakdown(sources)}",
            )
        return None

    @staticmethod
    def _repeated_label(sources: Sequence[Source]) -> tuple[int, int] | None:
        """The position of the first source whose label an earlier one holds,
        and the position of that earlier one, or ``None``.

        A label is a **citation key**, not a caption: every ``source_excerpt``
        in the report names one, and the validity gate checks that name against
        the job's label set. Set membership cannot see a duplicate — two
        sources sharing a label both resolve — so a report would cite ``'Notes'``
        with no way to say *which* ``'Notes'`` it quoted, and the traceability
        chain the gate exists to protect would be broken while passing it.

        Rejected rather than de-duplicated for the reason labels are never
        rewritten anywhere else here: renaming a caller's label would make the
        report cite something they did not submit.
        """
        first: dict[str, int] = {}
        for at, source in enumerate(sources):
            if source.label in first:
                return at, first[source.label]
            first[source.label] = at
        return None

    @staticmethod
    def _breakdown(sources: Sequence[Source]) -> str:
        """Each source's size, keyed by its position in the job.

        The error names **no culprit**: there is no per-source cap, so nothing
        here is individually too big and the overspend belongs to the sum. What
        the caller needs is the arithmetic, so they can decide what to cut.
        """
        return ", ".join(
            f"sources[{at}] {source.size_bytes()} bytes"
            for at, source in enumerate(sources)
        )


def plain_name(value: str) -> str:
    """A caller-supplied name with nothing a renderer reads as structure.

    **The rule, and the one reader of it.** Every entry point that takes a name
    somebody will see calls this: :meth:`Source._single_line_label` for a source
    label, :func:`clean_system_name` for a system name. A value carried into a
    report somebody reads must not hold a line break or a bidirectional
    override: either changes what they see without changing what they are told
    they are seeing.
    """
    if carries_line_break(value):
        raise ValueError("carries a line break")
    if _carries_formatting(value):
        raise ValueError("carries a control or formatting character")
    return value


def quotable(value: str) -> bool:
    """Whether ``value`` can sit between double quotes in one line.

    **The one rule of a quoted phrase.** A line of the answers Source quotes
    a question, and a lane quotes that line with the submitter's authority. A
    phrase that holds a line break, a control or formatting character, or a
    character that the grounding ladder reads as a double quote could close
    its quote and add a sentence that nobody wrote.
    """
    return (
        '"' not in normalize(value)
        and not carries_line_break(value)
        and not _carries_formatting(value)
    )


def clean_system_name(value: str | None) -> str | None:
    """The one rule for a caller-supplied system name, at every entry point.

    The HTTP route, the in-process engine and the web app's form route all ask
    this, so a name one accepts the others accept, and a name one refuses the
    others refuse. ``tests/test_system_name_readers.py`` holds the doors
    against each other rather than each against an expectation of its own.

    ``None`` is the answer for a name that is absent or blank. Both mean the
    caller named no system, so both fall through to the report's own default
    rather than putting whitespace where a name belongs.

    The value is trimmed before it is measured and before its characters are
    read, so the bound counts what a reader sees and padding cannot carry a
    name past either check.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_SYSTEM_NAME_CHARS:
        raise ValueError(f"exceeds {MAX_SYSTEM_NAME_CHARS} characters")
    return plain_name(trimmed)


def fence_for(body: str) -> str:
    """The shortest fence ``body`` cannot close.

    CommonMark's own rule: a fenced block ends at a line of backticks at least
    as long as the one that opened it, so one backtick longer than the longest
    run anywhere in the body is enough — and is what makes a hostile transcript
    carrying its own fence stay inside the block.

    Shared with the seam that renders the System Model into a category agent's prompt:
    ``json.dumps`` escapes quotes, ``\\n`` and ``\\r`` but **not** backticks, and
    **not** U+2028, U+2029 or U+0085 — three characters it passes through as
    themselves and that ``str.splitlines`` and most renderers break a line on.
    So a ``notes`` or ``source_excerpt`` value carrying a fence and one of those
    has both halves of a closing fence, and would close a fence written into a
    prompt file. Sizing the fence to the body is what makes that unspellable,
    here and downstream.
    """
    longest = max((len(run.group()) for run in _BACKTICK_RUN.finditer(body)), default=0)
    return "`" * max(3, longest + 1)


def fenced(body: str) -> str:
    """``body`` inside the shortest fence it cannot close.

    The one spelling of the block layout. :func:`render_sources` wraps each
    source in it, and :func:`render_fenced` wraps each
    rendered value a prompt interpolates in it, so the two seams cannot come to
    lay a block out differently.
    """
    fence = fence_for(body)
    return f"{fence}\n{body}\n{fence}"


def render(value: Any) -> str:
    """The one way a structured value enters a prompt: pretty, stable JSON."""
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def render_fenced(value: Any) -> str:
    """A rendered value plus a fence it cannot close.

    ``render`` is ``json.dumps`` with ``ensure_ascii=False``. It escapes quotes,
    backslashes, ``\\n`` and ``\\r`` — and **not** backticks, and **not**
    U+2028, U+2029 or U+0085, which it passes through as themselves. Those three
    are line terminators to ``str.splitlines`` and to most renderers, so a value
    holding one plus a backtick run has both halves of a closing fence.

    A System Model carries caller words by rule — ``source_excerpt`` verbatim,
    and ``notes`` quoting what a speaker said — and a merged draft carries a
    lane agent's own prose. Against a fence written into the prompt file, such a
    value closes the block and lands the bytes after it in instruction position.
    Against this one it cannot, because the fence is sized over the body: a body
    holding ``` gets a four-backtick fence.

    That is the hole :func:`render_sources` closes one
    node upstream, with the same technique and the same sizing rule — sized once
    over the whole rendered document, because this seam renders one JSON blob
    rather than N caller-controlled blocks.

    **Every rendered key a prompt interpolates goes through here**, and no
    prompt file writes a fence of its own. A static fence is only ever as long
    as itself, so the two cannot be mixed: one written in the file would be the
    one a body could close.
    """
    return fenced(render(value))


#: The line above a diagram's reading, by whether the parser read it. This
#: module's bytes: the reading itself is caller words, so it sits in a fence.
_READING_LINES = {
    True: "Mermaid's parser read this structure from the diagram above:",
    False: (
        "Mermaid's parser did not read the diagram above, so read it as text."
        " The parser said:"
    ),
}


def render_sources(
    sources: Sequence[Source],
    diagrams: Mapping[str, Mapping[str, Any]] = MappingProxyType({}),
) -> str:
    """Render the job's sources as the untrusted-data section of a prompt.

    One block per source. The marker line carries only the position and the
    register; every caller byte — the label included — sits inside a fence
    sized to that block's own content, so nothing submitted can escape into
    instruction position (OWASP LLM01).

    ``diagrams`` maps each ``diagram`` Source's label to its reading as JSON,
    a :class:`~analysis_service.diagrams.DiagramReading` dumped (ADR 0121). The
    reading follows the diagram in its own block, in a fence of its own.

    Raises :class:`ValueError` on an empty sequence: a job always has at least
    one source, and rendering nothing would hand a model a prompt whose input
    section is silently absent. It raises too for a ``diagram`` Source with no
    reading, because that diagram would reach a model without the block its
    register names.
    """
    if not sources:
        raise ValueError("a job carries at least one source")

    total = len(sources)
    blocks = []
    for index, source in enumerate(sources, start=1):
        body = f"label: {source.label}\n{_HEADER_RULE}\n{source.text}"
        block = f"### Source {index} of {total} — {_REGISTERS[source.kind]}\n\n{fenced(body)}"
        if source.kind == "diagram":
            if source.label not in diagrams:
                raise ValueError(f"the diagram {source.label!r} has no reading")
            reading = diagrams[source.label]
            read = "refused" not in reading
            shown = reading if read else reading["refused"]
            block += f"\n\n{_READING_LINES[read]}\n\n{render_fenced(shown)}"
        blocks.append(block)
    return "\n\n".join(blocks)
