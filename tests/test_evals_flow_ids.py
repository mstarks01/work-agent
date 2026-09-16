"""Moving the corpus and the ledger to a newer flow identity version (#989).

ADR 0037's migration contract, driven twice: over synthetic graphs, where each
undecidable condition can be built, and over the corpus and the archived
artifacts, where the real data has to survive it.

The corpus on disk is already at the version the code writes, so the tests that
need a version 1 graph build one with version 1's own rule rather than holding a
copy of the corpus as it was. A fixture spelling the earlier IDs by hand would
be a second reader of a rule the table already carries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from analysis_service.system_model import (
    FLOW_ID_VERSION,
    flow_id_rule,
    flow_id_version,
)
from evals.harness import flow_ids
from evals.harness.artifact import load_artifact
from evals.harness.fingerprint import Components, fingerprint
from evals.harness.flow_ids import (
    CASE_FILES,
    DEPENDENT_FILES,
    REGENERATED,
    MigrationError,
    flow_id_version_of_graph,
    lift_value,
    mapping_for,
    migrate_case,
    migrate_votes,
    rewrite_text,
    table_between,
    union_of,
)
from evals.harness.ledger import Vote
from evals.harness.provenance import REPO_ROOT
from tests.eval_factories import SAMPLE_CONTENT, SAMPLE_PROSE

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


class TestTheMappingComesFromTheGraph:
    """Rule 5: the endpoint types survive only in the graph, so it is read."""

    def test_each_flow_maps_to_the_id_the_new_rule_derives(self):
        raw = graph(
            flow("entity:shopper", "process:storefront", "place order"),
            elements=ENDPOINTS,
        )

        renames = mapping_for("case", raw, 1)

        assert renames.clean
        assert renames.renames == {
            "flow:shopper-to-storefront:place-order": (
                "flow:entity:shopper>process:storefront>place-order"
            )
        }

    def test_two_same_named_endpoints_of_two_types_map_apart(self):
        """The defect, as a mapping: one old ID cannot serve two new ones.

        Version 1 derived one ID for both flows, so this graph is exactly the
        colliding one the migration refuses rather than the one it splits — a
        reference to that ID names neither flow, and no mapping can recover
        which.
        """
        raw = graph(
            flow("entity:x", "store:y", "read"),
            flow("process:x", "store:y", "read"),
            elements=(
                ("entity:x", "external_entities"),
                ("process:x", "processes"),
                ("store:y", "data_stores"),
            ),
        )

        renames = mapping_for("case", raw, 1)

        assert not renames.clean
        assert "two flows already share this ID" in "\n".join(renames.flags)

    def test_a_dangling_endpoint_stops_the_case(self):
        raw = graph(
            flow("entity:shopper", "process:ghost", "place order"), elements=ENDPOINTS
        )

        renames = mapping_for("case", raw, 1)

        assert "names no element in the graph" in "\n".join(renames.flags)

    def test_a_graph_that_does_not_derive_its_own_ids_stops_the_case(self):
        raw = graph(
            flow(
                "entity:shopper",
                "process:storefront",
                "place order",
                element_id="flow:something-else:entirely",
            ),
            elements=ENDPOINTS,
        )

        renames = mapping_for("case", raw, 1)

        assert "does not derive its own IDs" in "\n".join(renames.flags)

    def test_a_flagged_case_is_never_half_rewritten(self, tmp_path):
        (tmp_path / "model.json").write_text(
            json.dumps(
                graph(
                    flow("entity:shopper", "process:ghost", "place order"),
                    elements=ENDPOINTS,
                )
            ),
            encoding="utf-8",
        )

        with pytest.raises(MigrationError, match="not decidable"):
            migrate_case(tmp_path, 1)


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


class TestTheUnionAcrossCases:
    def test_one_id_two_cases_spell_apart_refuses(self):
        with pytest.raises(MigrationError, match="not one-to-one"):
            union_of(
                {
                    "01": {"flow:a-to-b:read": "flow:entity:a>store:b>read"},
                    "02": {"flow:a-to-b:read": "flow:process:a>store:b>read"},
                }
            )

    def test_the_corpus_union_is_one_to_one(self):
        """The property every :data:`DEPENDENT_FILES` rewrite rests on."""
        tables = {
            case.name: table_between(
                json.loads((case / "model.json").read_text(encoding="utf-8")),
                1,
                FLOW_ID_VERSION,
            )
            for case in CASES
        }

        assert len(union_of(tables)) == sum(len(table) for table in tables.values())


class TestTheCorpusIsAtOneVersion:
    @pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
    def test_every_case_is_at_the_version_the_code_writes(self, case):
        raw = json.loads((case / "model.json").read_text(encoding="utf-8"))

        assert flow_id_version_of_graph(raw) == FLOW_ID_VERSION

    @pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
    def test_no_case_file_still_spells_an_earlier_version(self, case):
        """Every dependent reference moved, not only the model's own IDs.

        Derived from the case's own graph: what the earlier version called each
        of its flows is computable, so this asks for those strings by name
        rather than for a shape that would also match a legitimate ID.
        """
        raw = json.loads((case / "model.json").read_text(encoding="utf-8"))
        earlier = set(table_between(raw, 1, FLOW_ID_VERSION))

        for pattern in CASE_FILES:
            for path in case.glob(pattern):
                text = path.read_text(encoding="utf-8")
                assert not [old for old in earlier if old in text], path

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


class TestTheDeclaredDependents:
    @pytest.mark.parametrize("relative", DEPENDENT_FILES)
    def test_every_declared_dependent_exists(self, relative):
        """A path in the table that named nothing would migrate nothing, quietly."""
        assert (REPO_ROOT / relative).is_file()

    @pytest.mark.parametrize("relative", DEPENDENT_FILES)
    def test_no_dependent_still_spells_an_earlier_version(self, relative):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        tables = {
            case.name: table_between(
                json.loads((case / "model.json").read_text(encoding="utf-8")), 1
            )
            for case in CASES
        }

        assert not [old for old in union_of(tables) if old in text], relative

    @pytest.mark.parametrize("pattern,generator", REGENERATED)
    def test_every_generated_file_names_a_generator_that_exists(
        self, pattern, generator
    ):
        assert (REPO_ROOT / generator).is_file()
        assert list(REPO_ROOT.glob(pattern)), pattern


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


def vote(targets, case="01-payments-checkout", verdict="up"):
    components = Components(
        framework="stride",
        lane="spoofing",
        targets=tuple(sorted(targets)),
        verb="replay",
        scope=case,
    )
    return Vote(
        fingerprint=fingerprint(components, version=6),
        components=components,
        content=SAMPLE_CONTENT,
        prose=SAMPLE_PROSE,
        case=case,
        verdict=verdict,
        voter="mstarks01",
        recorded="2026-09-16T00:00:00Z",
    )


class TestAVoteCarriesOnlyWhereTheChangeIsOneToOne:
    """Rule 6, over the one shape a flow ID reaches a vote's targets in."""

    TABLE: ClassVar[dict[str, dict[str, str]]] = {
        "01-payments-checkout": {"flow:a-to-b:read": "flow:process:a>store:b>read"}
    }

    def test_an_endpoint_resolved_vote_does_not_move(self):
        """The ordinary case, and the reason the ledger is nearly untouched.

        ``targets`` arrives endpoint-resolved, so a flow the case records is
        already spelled as its two endpoints and a rename cannot reach it.
        """
        before = vote(("process:storefront-api", "entity:shopper"))

        carried, review = migrate_votes([before], self.TABLE)

        assert carried == [before]
        assert review == []

    def test_a_flow_the_mapping_names_is_carried_and_re_keyed(self):
        before = vote(("flow:a-to-b:read",))

        [after], review = migrate_votes([before], self.TABLE)

        assert review == []
        assert after.components.targets == ("flow:process:a>store:b>read",)
        assert after.fingerprint != before.fingerprint
        assert (after.voter, after.recorded, after.verdict) == (
            before.voter,
            before.recorded,
            before.verdict,
        )

    def test_a_flow_the_mapping_does_not_name_is_marked_for_review(self):
        before = vote(("flow:c-to-d:write",))

        carried, review = migrate_votes([before], self.TABLE)

        assert carried == [before]
        assert "does not name" in review[0]

    def test_a_case_with_no_mapping_is_marked_for_review(self):
        before = vote(("flow:a-to-b:read",), case="99-not-a-case")

        carried, review = migrate_votes([before], self.TABLE)

        assert carried == [before]
        assert "no mapping" in review[0]


def test_the_migration_is_idempotent_on_a_migrated_corpus():
    """Re-running it finds nothing to do rather than moving the IDs again."""
    for case in CASES:
        renames, written = migrate_case(case, FLOW_ID_VERSION, FLOW_ID_VERSION)

        assert renames.clean
        assert renames.renames == {}
        assert written == {}


def test_a_case_directory_pattern_reaches_every_file_a_case_carries():
    """The file table against the corpus, so a file a case gains is not missed.

    ``CASE_FILES`` is a list, which goes stale the day a case grows a file kind.
    The corpus is the registry to compare it against, exactly as
    ``VERSION_FOR`` is compared against ``PACKAGES``.
    """
    reached = {
        path.relative_to(case).as_posix()
        for case in CASES
        for path in flow_ids.case_paths(case)
    }
    present = {
        path.relative_to(case).as_posix()
        for case in CASES
        for path in case.rglob("*")
        if path.is_file()
    }
    # Everything but the submitted source and the generated reading document,
    # which no rename reaches: a source is the submitter's own bytes and a
    # generator owns the document.
    assert present - reached == {"source.md", "REVIEW.md"}


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


def test_the_repository_root_is_where_dependents_are_resolved():
    """The default the command passes, so a test tree cannot rewrite the real one."""
    assert flow_ids.CORPUS_DIR == CORPUS
    assert Path(flow_ids.VOTES_DIR).parts[-3:] == ("evals", "review", "votes")


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
