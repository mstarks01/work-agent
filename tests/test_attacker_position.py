"""A STRIDE lane states where its attacker stands, from a closed set.

A verb and a place do not decide a threat's mechanism (QA-2026-11-02-05-E8),
so each proposal names an :data:`AttackerPosition`, and a stored proposal from
a run before the field reads with none.
"""

from __future__ import annotations

from typing import get_args

import pytest

from analysis_service.frameworks.stride.record import (
    ArchivedThreatProposals,
    AttackerPosition,
    ThreatProposal,
    ThreatProposals,
)

PROPOSAL = {
    "sequence": 1,
    "title": "t",
    "description": "d",
    "affected_element_ids": ["process:a"],
    "evidence_refs": ["e"],
    "verb": "read",
    "severity": {"likelihood": "high", "impact": "high", "justification": "j"},
}


def test_the_agent_names_a_position_from_the_closed_set():
    schema = ThreatProposal.model_json_schema()

    assert "attacker_position" in schema["required"]
    assert schema["properties"]["attacker_position"]["enum"] == list(
        get_args(AttackerPosition)
    )


@pytest.mark.parametrize("position", [None, "inside"], ids=["missing", "unknown"])
def test_a_proposal_without_a_known_position_is_set_aside(position):
    """The batch keeps the claims that validate, as it does for any field."""
    fields = (
        PROPOSAL if position is None else {**PROPOSAL, "attacker_position": position}
    )

    batch = ThreatProposals.model_validate({"claims": [fields]})

    assert batch.claims == []
    assert "attacker_position" in str(batch.invalid)


def test_a_stored_proposal_from_before_the_field_reads_with_none():
    (claim,) = ArchivedThreatProposals.model_validate({"claims": [PROPOSAL]}).claims

    assert claim.attacker_position is None
