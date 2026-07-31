"""The input a job is built from, and how it reaches a model.

A **Source** is one piece of untrusted input text: a ``kind``, a caller-supplied
``label``, and the ``text`` itself. A job carries an ordered, non-empty list of
them. A transcript-only job is a one-element list with no special case, and so
is a description-only job — there is no separate single-text path.

``kind`` is a closed vocabulary and is **load-bearing**: it selects the register
:func:`render_sources` names around that source's text, so adding a kind means
changing the extraction prompt and this enum together. ``label`` is the key a
``source_excerpt`` cites, which is what keeps the traceability chain — threat to
element to the user's own words to *which source spoke them* — intact across N
sources. Order is presentation order only: the contract makes no authority
claim, so an earlier source does not override a later one.

Rendering is the whole untrusted-input surface (OWASP LLM01). Every caller byte
lands inside a fenced block, and the fence is sized to its own content so a
submitted transcript cannot close the block it sits in and continue in
instruction position. The label rides *inside* the fence for the same reason:
it is caller-controlled, so it can never sit on the marker line. What is left
outside carries only this module's own bytes — an index, a count, and the
register.

The render is a pure function of the sources, called from the one place the
graph is driven, so the service and the eval harness cannot show the same job to
a model differently.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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


def _fence_for(body: str) -> str:
    """The shortest fence ``body`` cannot close.

    CommonMark's own rule: a fenced block ends at a line of backticks at least
    as long as the one that opened it, so one backtick longer than the longest
    run anywhere in the body is enough — and is what makes a hostile transcript
    carrying its own fence stay inside the block.
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
        fence = _fence_for(body)
        blocks.append(
            f"### Source {index} of {total} — {_REGISTERS[source.kind]}\n\n"
            f"{fence}\n{body}\n{fence}"
        )
    return "\n\n".join(blocks)
