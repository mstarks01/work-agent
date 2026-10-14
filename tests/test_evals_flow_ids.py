"""Reading a graph's flow identity version, and lifting old flow IDs (#989).

ADR 0037 rule 4 keeps version 1 computable, so an archived artifact spelled at
version 1 still meets the corpus in one spelling.

The corpus on disk is already at the version the code writes, so the tests that
need a version 1 graph build one with version 1's own rule rather than holding a
copy of the corpus as it was. A fixture spelling the earlier IDs by hand would
be a second reader of a rule the table already carries.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from analysis_service.system_model import (
    FLOW_ID_VERSION,
    flow_id_rule,
    flow_id_version,
)
from evals.harness import flow_ids
from evals.harness.artifact import load_artifact
from evals.harness.flow_ids import (
    MigrationError,
    flow_id_version_of_graph,
    lift_value,
    rewrite_text,
    table_between,
)
from evals.harness.provenance import REPO_ROOT

CORPUS = REPO_ROOT / "evals" / "corpus"
EMISSIONS = REPO_ROOT / "evals" / "emissions"
CASES = sorted(path for path in CORPUS.iterdir() if (path / "model.json").exists())


def graph(*flows, elements=(("entity:shopper", "external_entities"),)):
    """A recorded graph shaped as ``model.json`` is, with the flows given."""
    raw: dict = {
        "external_entities": [],
        "processes": [],
        "data_stores": [],
        "data_flows": list(flows),
    }
    for element_id, collection in elements:
        raw[collection].append({"id": element_id, "name": element_id.split(":", 1)[1]})
    return raw


def flow(source, destination, name, version=1, element_id=None):
    """One recorded flow, its ID derived under ``version`` unless overridden."""
    return {
        "id": element_id or flow_id_rule(version).build(source, destination, name),
        "name": name,
        "source": source,
        "destination": destination,
    }


ENDPOINTS = (
    ("entity:shopper", "external_entities"),
    ("process:storefront", "processes"),
    ("store:orders", "data_stores"),
)


class TestWhatARenameReaches:
    def test_a_longer_label_beside_a_shorter_one_is_left_alone(self):
        """The boundary, over the shape the corpus actually holds.

        ``read-orders`` is a prefix of ``read-write-orders``, so an unbounded
        substitution turns the second into a mixture of both.
        """
        table = {"flow:a-to-b:read": "flow:process:a>process:b>read"}

        moved = rewrite_text("flow:a-to-b:read and flow:a-to-b:read-write", table)

        assert moved == ("flow:process:a>process:b>read and flow:a-to-b:read-write")

    def test_a_composed_evidence_reference_moves_with_the_id_inside_it(self):
        table = {"flow:a-to-b:read": "flow:process:a>process:b>read"}

        assert rewrite_text("crossing:flow:a-to-b:read", table) == (
            "crossing:flow:process:a>process:b>read"
        )
        assert rewrite_text("unknown:flow:a-to-b:read:authentication", table) == (
            "unknown:flow:process:a>process:b>read:authentication"
        )

    def test_a_longer_old_id_is_applied_before_one_it_starts_with(self):
        table = {
            "flow:a-to-b:read": "flow:process:a>process:b>read",
            "flow:a-to-b:read-more": "flow:process:a>process:b>read-more",
        }

        assert rewrite_text("flow:a-to-b:read-more", table) == (
            "flow:process:a>process:b>read-more"
        )

    def test_a_value_moves_in_its_keys_and_its_prose(self):
        table = {"flow:a-to-b:read": "flow:process:a>process:b>read"}

        lifted = lift_value(
            {"flow:a-to-b:read": ["cited by flow:a-to-b:read", 3]}, table
        )

        assert lifted == {
            "flow:process:a>process:b>read": [
                "cited by flow:process:a>process:b>read",
                3,
            ]
        }


class TestTheCorpusIsAtOneVersion:
    @pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
    def test_every_case_is_at_the_version_the_code_writes(self, case):
        raw = json.loads((case / "model.json").read_text(encoding="utf-8"))

        assert flow_id_version_of_graph(raw) == FLOW_ID_VERSION

    def test_a_graph_holding_both_shapes_is_refused(self):
        raw = graph(
            flow("entity:shopper", "process:storefront", "place order", version=1),
            flow("process:storefront", "store:orders", "write order", version=2),
            elements=ENDPOINTS,
        )

        with pytest.raises(MigrationError, match="mixture"):
            flow_id_version_of_graph(raw)

    def test_a_graph_with_no_flows_is_at_this_version(self):
        assert flow_id_version_of_graph(graph()) == FLOW_ID_VERSION


class TestTheArchiveKeepsItsOwnVersion:
    """ADR 0037: no historical artifact is rewritten, and every one still loads."""

    ARTIFACTS = sorted(EMISSIONS.rglob("*.json"))

    @pytest.mark.parametrize(
        "path", ARTIFACTS, ids=[str(p.relative_to(EMISSIONS)) for p in ARTIFACTS]
    )
    def test_every_archived_file_is_still_readable(self, path):
        """The whole archive, read after the derivation changed.

        A validator change is checked against every artifact already paid for,
        because a fatal rule shipped without that read makes data nobody can
        re-run unloadable. An emission's flows are read for their version too,
        so an archive at an earlier one is named rather than assumed away.
        """
        raw = json.loads(path.read_text(encoding="utf-8"))

        if "artifact_version" in raw:
            load_artifact(path)
            return
        model = raw.get("normalized") or {}
        if model.get("data_flows"):
            assert flow_id_version_of_graph(model) in (1, FLOW_ID_VERSION)

    def test_an_archived_emission_lifts_to_the_corpus_version(self):
        """Rule 4's version check, as the lift the readers actually use.

        An archived emission spells its flows the way the version it ran under
        did; the table derived from the case's graph says what each is called
        now, so the reference and the emission meet in one spelling.
        """
        case = CORPUS / "01-payments-checkout"
        raw = json.loads((case / "model.json").read_text(encoding="utf-8"))
        table = table_between(raw, 1)
        old = next(iter(table))

        lifted = lift_value({"subject": old}, table)

        assert flow_id_version(lifted["subject"]) == FLOW_ID_VERSION


def test_the_generated_reading_documents_carry_no_earlier_id():
    """The generator's output, after it was re-run. Not migrated — regenerated."""
    for case in CASES:
        raw = json.loads((case / "model.json").read_text(encoding="utf-8"))
        earlier = set(table_between(raw, 1))
        document = case / "REVIEW.md"
        if not document.is_file():
            continue
        text = document.read_text(encoding="utf-8")
        assert not [old for old in earlier if old in text], document


class TestTheRenameIsFencedOnBothSides:
    """A rename matches a whole ID, never a tail of a longer name.

    The right guard stops ``flow:a>b>read`` rewriting inside
    ``flow:a>b>read-more``. The left guard stops the mirror case, which
    ``_ID_PREFIX`` admits: it is ``[a-z_]+``, so a prefix ending in an existing
    one is a legal spelling and would make a shorter ID the suffix of a longer.
    """

    RENAMES: ClassVar[dict[str, str]] = {"entity:shopper": "entity:buyer"}

    def test_the_intended_sites_still_rewrite(self):
        """A JSON field, an evidence reference and prose all carry the ID."""
        for text in (
            '"element": "entity:shopper"',
            '"ref": "crossing:flow:entity:shopper>process:api>place-order"',
            "the entity:shopper row",
            "entity:shopper",
        ):
            assert "entity:buyer" in flow_ids.rewrite_text(text, self.RENAMES), text

    def test_a_longer_slug_is_not_rewritten(self):
        held = flow_ids.rewrite_text('"entity:shopper-group"', self.RENAMES)
        assert held == '"entity:shopper-group"'

    def test_a_prefix_ending_in_another_prefix_is_not_rewritten(self):
        """The left guard's own case: ``my_entity:shopper`` holds the ID as a tail."""
        held = flow_ids.rewrite_text('"my_entity:shopper"', self.RENAMES)
        assert held == '"my_entity:shopper"'
