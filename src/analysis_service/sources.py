"""The input a job is built from, and how it reaches a model.

A **Source** is one piece of untrusted input text: a ``kind``, a caller-supplied
``label``, and the ``text`` itself. A job carries an ordered, non-empty list of
them. A transcript-only job is a one-element list with no special case, and so
is a description-only job. There is no separate single-text path.

``kind`` is a closed vocabulary, and it is load-bearing: it selects the register
:func:`render_sources` names around that source's text, so a new kind means a
change to the extraction prompt and this enum together.

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

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Closed and load-bearing: each kind names the register rendered around its
# text. Adding one means editing prompts/extract.md in the same change.
SourceKind = Literal["description", "transcript"]

# What a caller gets when they use the convenience constructors rather than
# naming a source themselves. The wire never defaults a label — an unnamed
# source there is a caller error, because the label is a citation key and
# inventing one on the caller's behalf puts a name they never chose into the
# report.
DEFAULT_DESCRIPTION_LABEL = "System description"
DEFAULT_TRANSCRIPT_LABEL = "Call transcript"

# A label is a citation key, so it is bounded but never rewritten: trimming or
# escaping one would mean the report cites something the caller did not submit.
MAX_LABEL_CHARS = 200

# Rejected in a label. The header inside the fence is positional — first line
# the label, second the separator — so a label spanning lines would make the
# text below it unreadable as text. Includes the Unicode line separators, which
# a caller can paste without seeing.

_LINE_BREAKS = ("\n", "\r", "\u2028", "\u2029")

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
_FORMATTING_CATEGORIES = frozenset({"Cc", "Cf"})

# All that ``kind`` still selects: one phrase telling the model what register
# the text below is in. It sits outside the fence, so it is this module's bytes
# and never the caller's.
_REGISTERS: dict[str, str] = {
    "description": "a written description of the system",
    "transcript": "a transcribed conversation about the system",
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


class Source(BaseModel):
    """One piece of untrusted input text a job is built from.

    Well-formedness is enforced here rather than at each entry point, so the
    HTTP route and the in-process engine reject the same shapes for the same
    reasons.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SourceKind
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    text: str = Field(min_length=1)

    @field_validator("label")
    @classmethod
    def _single_line_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("label must not be blank")
        if any(char in value for char in _LINE_BREAKS):
            raise ValueError("label must be a single line")
        if any(unicodedata.category(char) in _FORMATTING_CATEGORIES for char in value):
            raise ValueError(
                "label must not contain control, bidi or zero-width characters"
            )
        return value

    @field_validator("text")
    @classmethod
    def _non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

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
        repeated = self._repeated_labels(sources)
        if repeated:
            return LimitBreach(
                "duplicate-label",
                f"source labels must be unique within a job; repeated: "
                f"{', '.join(repr(label) for label in repeated)}",
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
    def _repeated_labels(sources: Sequence[Source]) -> list[str]:
        """Labels used by more than one source, in first-seen order.

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
        counts = Counter(source.label for source in sources)
        seen: dict[str, None] = {}
        for source in sources:
            if counts[source.label] > 1:
                seen[source.label] = None
        return list(seen)

    @staticmethod
    def _breakdown(sources: Sequence[Source]) -> str:
        """Each source's size, keyed by label.

        The error names **no culprit**: there is no per-source cap, so nothing
        here is individually too big and the overspend belongs to the sum. What
        the caller needs is the arithmetic, so they can decide what to cut.
        """
        return ", ".join(
            f"{source.label!r} {source.size_bytes()} bytes" for source in sources
        )


def plain_name(value: str) -> str:
    """A caller-supplied name with nothing a renderer reads as structure.

    The rule :meth:`Source._single_line_label` applies, exported because the
    same question is asked of a name that arrives at another entry point. A
    value carried into a report somebody reads must not hold a line break or a
    bidirectional override: either changes what they see without changing what
    they are told they are seeing.
    """
    if any(char in value for char in _LINE_BREAKS):
        raise ValueError("carries a line break")
    if any(unicodedata.category(char) in _FORMATTING_CATEGORIES for char in value):
        raise ValueError("carries a control or formatting character")
    return value


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


def render_sources(sources: Sequence[Source]) -> str:
    """Render the job's sources as the untrusted-data section of a prompt.

    One block per source. The marker line carries only the position and the
    register; every caller byte — the label included — sits inside a fence
    sized to that block's own content, so nothing submitted can escape into
    instruction position (OWASP LLM01).

    Raises :class:`ValueError` on an empty sequence: a job always has at least
    one source, and rendering nothing would hand a model a prompt whose input
    section is silently absent.
    """
    if not sources:
        raise ValueError("a job carries at least one source")

    total = len(sources)
    blocks = []
    for index, source in enumerate(sources, start=1):
        body = f"label: {source.label}\n{_HEADER_RULE}\n{source.text}"
        fence = fence_for(body)
        blocks.append(
            f"### Source {index} of {total} — {_REGISTERS[source.kind]}\n\n"
            f"{fence}\n{body}\n{fence}"
        )
    return "\n\n".join(blocks)
