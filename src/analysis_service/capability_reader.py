"""The capabilities a job's sources state, read by a model and checked by code.

A description that says "customers sign in through Auth0" states that the
application authenticates its users, and asking the owner again only spends
an answer. The ``capabilities`` node reads the sources and the capability
table and proposes each capability the text says the application has, with
the words that say it (ADR 0086). This module is what code makes of that
proposal.

**A row only states presence.** The schema has no state field: a source is
silent about most of what an application lacks, and a "no" from one choice
already costs the owner little (ADR 0084). A wrong "present" makes a
requirement apply that does not; it cannot rule one out.

**A row carries its words, or it is dropped.** Code keeps a row only where its
key is in the table, its label names one of the job's submitted sources, and
its quote is in that source under the validity gate's own matcher. The
answers source is not one of them: an answer writes its own capability
(:func:`~analysis_service.fact_writes.capability_statements`), and a row that
quoted it would carry the answers' label with nothing to say a model wrote it. **A statement always
wins**: a capability an answer, a closed field or another statement already
names is left as it is, and a later answer replaces what this module wrote.

The node runs as a separate pass, never inside extraction: asking extraction
for capabilities cost element identity (ADR 0051).
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from analysis_service.capabilities import CAPABILITIES
from analysis_service.grounding import verify_quote
from analysis_service.references import canonical
from analysis_service.sources import ANSWERS_LABEL
from analysis_service.system_model import CapabilityStatement, SystemModel


class CapabilityRow(BaseModel):
    """One capability the sources state, and the words that state it."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(
        min_length=1,
        max_length=60,
        json_schema_extra={"enum": [*sorted(CAPABILITIES)]},
    )
    source_label: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=1000)


class CapabilityReading(BaseModel):
    """What the ``capabilities`` node emits: one flat list of rows."""

    model_config = ConfigDict(
        extra="forbid",
        title="CapabilityReading",
        json_schema_extra={
            "description": "Each capability the sources state the application"
            " has, with the source label and the exact words."
        },
    )

    capabilities: list[CapabilityRow] = Field(default_factory=list)


def with_stated_capabilities(
    model: SystemModel, reading: CapabilityReading, sources: Mapping[str, str]
) -> SystemModel:
    """The model with each checked row of ``reading`` written as present.

    **The one writer of a capability the sources state.** A row is dropped
    where its key is not in the table, its label names no submitted source, its
    quote is not in that source, or a statement already names its capability.
    """
    named = {statement.capability for statement in model.capabilities}
    updated = model.model_copy(deep=True)
    for row in reading.capabilities:
        label = canonical(row.source_label, list(sources))
        submitted = label in sources and label != ANSWERS_LABEL
        known = row.capability in CAPABILITIES and submitted
        if not known or row.capability in named:
            continue
        if not verify_quote(row.quote, sources[label]):
            continue
        updated.capabilities.append(
            CapabilityStatement(
                capability=row.capability,
                state="present",
                source_excerpt=row.quote,
                source_label=label,
            )
        )
        named.add(row.capability)
    return updated
