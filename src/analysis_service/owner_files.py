"""Owner Files: the supporting files an **Owner** keeps in the web app's Files
section, for use in any analysis of that owner (ADR 0118).

The text of an Owner File is an object under
:func:`~analysis_service.objects.file_key`, and one row names it. The row holds
what a page lists and what a job copies: the name, the kind, the SHA-256 of
the object, its size and the time of the upload. The kind gives the format. A
job copies the text as a :class:`~analysis_service.sources.Source`, so a
delete of the Owner File changes no job (ADR 0119).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from analysis_service.errors import error_place
from analysis_service.parsing import Sha256Hex
from analysis_service.sources import (
    DIAGRAM_TYPES,
    FORMATS,
    KIND_FORMATS,
    MAX_LABEL_CHARS,
    OWNER_FILE_ID,
    Source,
    SourceFormat,
    SourceKind,
    is_read_diagram_type,
    plain_name,
    text_digest,
)

OWNER_FILE_ID_PATTERN = re.compile(OWNER_FILE_ID)

#: What one :meth:`~analysis_service.jobs.JobStore.add_file` call did.
#: ``name_in_use`` and ``over_limit`` refuse the upload, and
#: ``principal_exists`` refuses a new principal for an identity that holds one.
FileAdmission = Literal["added", "name_in_use", "over_limit", "principal_exists"]


class OwnerFile(BaseModel):
    """The row of one Owner File: everything but its text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=OWNER_FILE_ID)
    owner_id: str
    #: The Source label that a job gives the copy, so it obeys the label rule.
    name: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    kind: SourceKind
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    created_at: datetime

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        return plain_name(value)

    @field_validator("kind")
    @classmethod
    def _uploaded_kind(cls, value: SourceKind) -> SourceKind:
        if value not in KIND_FORMATS:
            raise ValueError(f"no upload format allows the {value} kind")
        return value

    @property
    def format(self) -> SourceFormat:
        """What the file is written in: the one :data:`FORMATS` row whose kinds
        hold its kind."""
        return KIND_FORMATS[self.kind]

    @classmethod
    def create(
        cls,
        *,
        owner_id: str,
        name: str,
        kind: SourceKind,
        text: str,
    ) -> Self:
        """The row of a new upload of ``text``, with a new ID."""
        return cls(
            id=f"file-{uuid4().hex}",
            owner_id=owner_id,
            name=name,
            kind=kind,
            sha256=text_digest(text),
            size_bytes=len(text.encode("utf-8")),
            created_at=datetime.now(UTC),
        )


#: A character that a plain text file does not hold: each C0 and C1 control
#: character but a tab, a line feed and a carriage return. The page holds the
#: same class in ``webapp/static/text_file.js``, and a test runs both.
CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

_QUOTED = [f"“{name}”" for name in DIAGRAM_TYPES]
_TYPES = ", ".join(_QUOTED[:-1]) + " or " + _QUOTED[-1]


def name_in_use(name: str) -> str:
    """The refusal of a name that the owner already gives another file."""
    return f"You already have a file named “{name}”. Give this file a different name."


def over_limit(limit: int) -> str:
    """The refusal of an upload over the owner's count limit."""
    return (
        f"Files holds {limit} files, the most it can keep. Delete a file before"
        " you add another."
    )


def upload_refusal(
    name: str,
    kind: SourceKind,
    format: SourceFormat,
    text: str,
    *,
    max_bytes: int,
    page_labels: Collection[str],
) -> str | None:
    """Why an upload to Files is refused, or ``None`` where it passes.

    **The one rule of an upload's content.** The checks run in the page's
    order: the kind the format's row allows, the size, the text, the name and
    the diagram type. A name in ``page_labels`` is refused, because a job
    that also holds the page's own Source of that label would repeat it. The
    name and the text then pass the :class:`~analysis_service.sources.Source`
    rules, because a job copies the file as a Source with the name as its
    label. Only the store can refuse a name in use or a count over the limit.
    """
    row = FORMATS[format]
    if kind not in row.kinds:
        return f"A {row.label} file cannot be a {kind}."
    if len(text.encode("utf-8")) > max_bytes:
        return f"This file is larger than the {max_bytes:,} bytes that one analysis can carry."
    if CONTROL_CHARACTER.search(text):
        return "This file holds control characters, so it is not a plain text file."
    if name in page_labels:
        return (
            f"The page uses the name “{name}” for its own sources. Give this file a"
            " different name."
        )
    if kind == "diagram" and not is_read_diagram_type(text):
        return (
            "This Mermaid file is not a flowchart or an architecture-beta diagram."
            f" Its first diagram line must start with {_TYPES}."
        )
    try:
        Source(kind=kind, label=name, text=text)
    except ValidationError as exc:
        if "text" in {error_place(error) for error in exc.errors()}:
            return "This file holds no text."
        return (
            f"Give the file a name of at most {MAX_LABEL_CHARS} characters, on one"
            " line, with no control or formatting characters."
        )
    return None
