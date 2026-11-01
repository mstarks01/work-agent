"""Owner Files: the supporting files an **Owner** keeps in the web app's Files
section, for use in any analysis of that owner (ADR 0118).

The text of an Owner File is an object under
:func:`~analysis_service.objects.file_key`, and one row names it. The row holds
what a page lists and what a job copies: the name, the kind, the format, the
SHA-256 of the object, its size and the time of the upload. A job copies the
text as a :class:`~analysis_service.sources.Source`, so a delete of the Owner
File changes no job (ADR 0119).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analysis_service.parsing import Sha256Hex
from analysis_service.sources import (
    MAX_LABEL_CHARS,
    SourceFormat,
    SourceKind,
    plain_name,
    text_digest,
)

#: An Owner File ID carries its type, as a job ID does.
OWNER_FILE_ID_PATTERN = re.compile(r"^file-[0-9a-f]{32}$")

#: What one :meth:`~analysis_service.jobs.JobStore.add_file` call did.
#: ``name_in_use`` and ``over_limit`` refuse the upload, and
#: ``principal_exists`` refuses a new principal for an identity that holds one.
FileAdmission = Literal["added", "name_in_use", "over_limit", "principal_exists"]


class OwnerFile(BaseModel):
    """The row of one Owner File: everything but its text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=OWNER_FILE_ID_PATTERN.pattern)
    owner_id: str
    #: The Source label that a job gives the copy, so it obeys the label rule.
    name: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    kind: SourceKind
    format: SourceFormat
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    created_at: datetime

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        return plain_name(value)

    @classmethod
    def create(
        cls,
        *,
        owner_id: str,
        name: str,
        kind: SourceKind,
        format: SourceFormat,
        text: str,
    ) -> Self:
        """The row of a new upload of ``text``, with a new ID."""
        return cls(
            id=f"file-{uuid4().hex}",
            owner_id=owner_id,
            name=name,
            kind=kind,
            format=format,
            sha256=text_digest(text),
            size_bytes=len(text.encode("utf-8")),
            created_at=datetime.now(UTC),
        )
