"""The audit's two rules, held to the registries they claim to partition.

Three groups. The first compares the phase table against the graph's own node
names, which is the check ``docs/agents/framework-parity.md`` asks for of any
table keyed by a vocabulary somebody else owns: a node added tomorrow fails
here rather than being attributed by whoever happens to be reading.

The second drives the ledger's refusals. A vocabulary is only closed if
something raises outside it, and an append-only file is only append-only if a
duplicate identifier is refused.

The third drives staleness, which is the whole reason a record names the paths
it reads. A conclusion drawn against a file that has since changed is not
automatically wrong, and the instrument says ``stale`` rather than ``refuted``
for that reason.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
from typing import get_args

import pytest

from analysis_service import graph
from analysis_service.frameworks import PACKAGES
from evals.harness import audit
from evals.harness.run import COMMANDS


def _namespace(**fields) -> argparse.Namespace:
    """The parsed arguments a command reads, without building the parser."""
    return argparse.Namespace(**fields)


def _record(**overrides) -> dict:
    """One well-formed row, which each test spoils in exactly one way."""
    row = {
        "experiment_id": "E-001",
        "audit_id": "A-001",
        "recorded": "2026-09-20T00:00:00Z",
        "revision": "0" * 40,
        "signature": "stride case-01 must-find missed at the checkout boundary",
        "hypothesis": "the lane never cited the place",
        "falsifier": "a rejected draft citing the place",
        "intervention": "read the archived losses block",
        "outcome": "null",
        "scope": "case 01, stride, one archived sweep",
    }
    row.update(overrides)
    return row


def _condition(**overrides) -> dict:
    """One well-formed condition block, spoiled the same way."""
    held = {
        "name": "corrected-extraction",
        "input": "evals/corpus/01-payments-checkout/model.json",
        "adapters": [],
        "unrepresentable": [],
        "exposure": "signed by the maintainer before this audit read any miss",
        "stage": "final-report",
    }
    held.update(overrides)
    return held


class TestThePhaseTable:
    """A partition of the graph's nodes, compared against the graph."""

    def test_every_shared_node_lands_in_exactly_one_phase(self):
        """The ``*_NODE`` constants, read off the module rather than listed."""
        names = [
            value
            for name, value in vars(graph).items()
            if name.endswith("_NODE") and isinstance(value, str)
        ]
        assert names, "no node constants found; this lint covers nothing"
        for node in names:
            owners = [
                phase for phase, entry in audit.PHASES.items() if node in entry.nodes
            ]
            assert len(owners) == 1, f"{node} is owned by {owners}, not one phase"

    def test_every_per_framework_role_lands_in_exactly_one_phase(self):
        for role in graph.ROLES:
            owners = [
                phase for phase, entry in audit.PHASES.items() if role in entry.nodes
            ]
            assert len(owners) == 1, f"{role} is owned by {owners}, not one phase"

    def test_every_lane_agent_is_attributed_to_analysis(self):
        """A lane node carries its framework and its lane, and still resolves."""
        for name, package in PACKAGES.items():
            for lane in package.lanes:
                node = graph.analyze_node_name(name, lane)
                assert audit.phase_of(node) == "analysis"

    def test_a_per_framework_node_resolves_through_its_role(self):
        for node, phase in (
            ("critic_stride", "criticism"),
            ("critic_failed_stride", "criticism"),
            ("merge_asvs", "fan-in"),
            ("reading_inventory", "extraction"),
        ):
            assert audit.phase_of(node) == phase

    def test_a_node_no_phase_owns_raises_by_name(self):
        with pytest.raises(KeyError, match="pontificate"):
            audit.phase_of("pontificate")

    def test_the_table_and_the_command_agree(self):
        """The printed table is rendered from the table, not restated beside it."""
        printed = "\n".join(audit.phase_table())
        for phase, entry in audit.PHASES.items():
            assert phase in printed
            assert entry.question in printed


class TestTheRowAndItsFieldsAreOneList:
    """`Experiment` spells its fields four times, and only one is the record.

    The dataclass declares them; `to_json` writes them; `parse` reads them back;
    `_REQUIRED` names the ones a row may not omit. A field added to the first
    and missed by the second leaves the ledger silently, and the round trip
    below only catches it when the fixture happens to set that field away from
    its default. So the three derived lists are checked against the dataclass
    rather than against each other.
    """

    def _fields(self):
        return {field.name for field in dataclasses.fields(audit.Experiment)}

    def test_the_row_writes_every_field_the_record_declares(self):
        written = set(audit.parse(_record(), source="test").to_json())

        assert written == self._fields(), (
            "to_json and Experiment name different fields; a field it omits"
            " never reaches the ledger file."
        )

    def test_the_loader_reads_every_field_the_row_writes(self):
        """Every field survives a write and a read, set to a non-default value.

        The round trip beside this one uses a fixture, so a field left at its
        default round-trips through a `to_json` that drops it. This fills each
        one first, which is what makes the trip prove anything.
        """
        filled = dataclasses.replace(
            audit.parse(_record(), source="test"),
            phase="analysis",
            framework="stride",
            case="01-payments-checkout",
            reads=("evals/harness/audit.py",),
            artifacts=("evals/runs/x.json",),
            estimated_usd=0.5,
            actual_usd=0.25,
            parent="QA-x-01",
            supersedes="QA-x-00",
            reconsider_when="the scorer moves",
            condition=audit.Condition(
                name="direct-facts",
                input="evals/corpus/01-payments-checkout/facts.json",
                adapters=("facts rendered as a table",),
                unrepresentable=("placement of the card processor",),
                exposure="built by an agent that had read the case-01 misses",
                stage="analysis",
            ),
        )

        assert audit.parse(filled.to_json(), source="test") == filled

    def test_every_required_field_is_one_the_record_declares(self):
        """A required name the dataclass does not carry can never be missing."""
        assert set(audit._REQUIRED) <= self._fields()


class TestTheLedgerRefusals:
    """A closed vocabulary that never raises is a comment."""

    def test_a_well_formed_row_parses(self):
        experiment = audit.parse(_record(), source="test")

        assert experiment.outcome == "null"
        assert experiment.phase is None

    def test_an_outcome_outside_the_set_is_refused_by_name(self):
        with pytest.raises(audit.ExperimentError, match="marvellous"):
            audit.parse(_record(outcome="marvellous"), source="test")

    def test_a_phase_outside_the_table_is_refused_by_name(self):
        with pytest.raises(audit.ExperimentError, match="postprocessing"):
            audit.parse(_record(phase="postprocessing"), source="test")

    def test_a_framework_outside_the_registry_is_refused_by_name(self):
        """The registry is ``PACKAGES``, so a package added tomorrow is legal."""
        with pytest.raises(audit.ExperimentError, match="linddun"):
            audit.parse(_record(framework="linddun"), source="test")

    def test_every_registered_package_is_a_legal_row(self):
        for name in PACKAGES:
            assert audit.parse(_record(framework=name), source="test").framework == name

    def test_a_framework_neutral_row_is_legal(self):
        """Neutral is a different claim from holding for one package."""
        assert audit.parse(_record(), source="test").framework is None

    def test_a_missing_field_is_named_rather_than_defaulted(self):
        spoiled = _record()
        del spoiled["falsifier"]

        with pytest.raises(audit.ExperimentError, match="falsifier"):
            audit.parse(spoiled, source="test")

    def test_an_unattributed_phase_is_a_legitimate_row(self):
        """Attribution may stay unknown, and a row saying so is not a defect."""
        experiment = audit.parse(_record(phase=None), source="test")

        assert experiment.phase is None

    def test_every_condition_in_the_vocabulary_has_its_stages(self):
        assert set(audit.CONDITION_STAGES) == set(get_args(audit.ConditionName))

    def test_a_condition_outside_the_table_is_refused_by_name(self):
        with pytest.raises(audit.ExperimentError, match="oracle-everything"):
            audit.parse(
                _record(condition=_condition(name="oracle-everything")), source="test"
            )

    def test_direct_facts_are_never_read_as_a_final_report(self):
        """No route carries direct-facts output past analysis, so no report exists."""
        with pytest.raises(audit.ExperimentError, match="never at 'final-report'"):
            audit.parse(
                _record(
                    condition=_condition(name="direct-facts", stage="final-report")
                ),
                source="test",
            )

    @pytest.mark.parametrize("field", ["input", "exposure"])
    def test_a_condition_names_its_input_and_its_builders_exposure(self, field):
        with pytest.raises(audit.ExperimentError, match=f"has no {field}"):
            audit.parse(_record(condition=_condition(**{field: " "})), source="test")

    def test_an_empty_adapter_list_is_an_answer(self):
        """The shipped route has no adapter, and saying so is a legal row."""
        row = audit.parse(_record(condition=_condition(adapters=[])), source="test")

        assert row.condition is not None and row.condition.adapters == ()

    def test_a_missing_adapter_list_is_refused(self):
        held = _condition()
        del held["adapters"]

        with pytest.raises(audit.ExperimentError, match="adapters is not a list"):
            audit.parse(_record(condition=held), source="test")

    def test_a_row_round_trips_through_its_own_json(self):
        experiment = audit.parse(_record(phase="criticism"), source="test")

        assert audit.parse(experiment.to_json(), source="test") == experiment

    def test_two_rows_may_not_share_an_identifier(self, tmp_path: Path):
        audit.append(audit.parse(_record(), source="test"), tmp_path)
        audit.append(audit.parse(_record(audit_id="A-002"), source="test"), tmp_path)

        with pytest.raises(audit.ExperimentError, match="already used"):
            audit.load(tmp_path)

    def test_appending_never_rewrites_an_earlier_row(self, tmp_path: Path):
        first = audit.parse(_record(), source="test")
        second = audit.parse(
            _record(experiment_id="E-002", outcome="refuted"), source="test"
        )
        audit.append(first, tmp_path)
        audit.append(second, tmp_path)

        loaded = audit.load(tmp_path)
        assert [one.experiment_id for one in loaded] == ["E-001", "E-002"]
        assert [one.outcome for one in loaded] == ["null", "refuted"]

    def test_an_empty_directory_is_an_empty_ledger(self, tmp_path: Path):
        """The first audit has no history, and refusing there is unstartable."""
        assert audit.load(tmp_path) == ()

    def test_a_line_that_is_not_json_names_its_file_and_line(self, tmp_path: Path):
        (tmp_path / "A-001.jsonl").write_text("{not json\n", encoding="utf-8")

        with pytest.raises(audit.ExperimentError, match="A-001.jsonl:1"):
            audit.load(tmp_path)

    def test_the_ledger_on_disk_loads(self):
        """The repository's own records, read by the loader that refuses."""
        audit.load()


class TestRetrieval:
    """What an audit must read before it proposes anything."""

    def test_a_shared_signature_matches(self):
        rows = (
            audit.parse(_record(), source="test"),
            audit.parse(
                _record(experiment_id="E-002", signature="something else"),
                source="test",
            ),
        )

        found = audit.relevant(rows, signature=rows[0].signature)
        assert [one.experiment_id for one in found] == ["E-001"]

    def test_a_shared_phase_matches_a_different_signature(self):
        rows = (
            audit.parse(_record(phase="criticism"), source="test"),
            audit.parse(
                _record(experiment_id="E-002", signature="another", phase="extraction"),
                source="test",
            ),
        )

        found = audit.relevant(rows, phase="criticism")
        assert [one.experiment_id for one in found] == ["E-001"]

    def test_a_framework_neutral_row_answers_for_every_package(self):
        rows = (
            audit.parse(_record(), source="test"),
            audit.parse(
                _record(experiment_id="E-002", framework="stride"), source="test"
            ),
        )

        for name in PACKAGES:
            found = audit.relevant(rows, framework=name)
            assert "E-001" in {one.experiment_id for one in found}

    def test_a_row_for_one_package_does_not_answer_for_another(self):
        rows = (audit.parse(_record(framework="stride"), source="test"),)

        assert audit.relevant(rows, framework="asvs") == ()

    def test_no_argument_returns_the_whole_ledger(self):
        rows = (audit.parse(_record(), source="test"),)

        assert audit.relevant(rows) == rows

    def test_a_superseded_row_is_marked_and_never_dropped(self):
        """A correction says what was wrong; dropping the row loses that."""
        rows = (
            audit.parse(_record(), source="test"),
            audit.parse(
                _record(experiment_id="E-002", supersedes="E-001"), source="test"
            ),
        )

        assert audit.superseded(rows) == frozenset({"E-001"})
        assert len(audit.relevant(rows, signature=rows[0].signature)) == 2


class TestStaleness:
    """Computed from the tree, and never a verdict on its own."""

    def test_a_record_whose_read_changed_is_stale(self):
        experiment = audit.parse(_record(reads=["prompts/extract.md"]), source="test")

        read = audit.staleness(experiment, ["prompts/extract.md", "README.md"])
        assert read.state == "stale"
        assert read.changed == ("prompts/extract.md",)

    def test_a_record_whose_reads_are_untouched_is_current(self):
        experiment = audit.parse(_record(reads=["prompts/extract.md"]), source="test")

        assert audit.staleness(experiment, ["README.md"]).state == "current"

    def test_a_record_anchored_to_nothing_says_so(self):
        """Not current: a conclusion naming no file cannot be checked at all."""
        experiment = audit.parse(_record(), source="test")

        assert audit.staleness(experiment, ["README.md"]).state == "unanchored"

    def test_a_revision_git_cannot_reach_is_undecidable(self):
        assert audit.Staleness(undecidable="no checkout").state == "undecidable"

    def test_an_unreachable_revision_reads_undecidable_end_to_end(self, tmp_path: Path):
        audit.append(audit.parse(_record(reads=["README.md"]), source="test"), tmp_path)

        found = audit.lookup(directory=tmp_path, root=tmp_path)
        assert [one.staleness.state for one in found] == ["undecidable"]

    def test_changed_since_returns_nothing_where_git_cannot_answer(
        self, tmp_path: Path
    ):
        assert audit.changed_since("HEAD", tmp_path) is None


class TestRendering:
    """What the reader of a lookup is told."""

    def test_an_empty_lookup_says_it_is_new_ground(self):
        assert "new ground" in str(audit.render([]))

    def test_a_match_prints_its_outcome_and_its_staleness(self):
        found = [
            audit.Lookup(
                experiment=audit.parse(_record(outcome="refuted"), source="test"),
                staleness=audit.Staleness(changed=("prompts/extract.md",)),
                superseded=True,
            )
        ]

        printed = str(audit.render(found))
        assert "refuted" in printed
        assert "stale" in printed
        assert "superseded" in printed
        assert "prompts/extract.md" in printed


class TestTheCommands:
    """The two entry points, driven the way the skill drives them."""

    def test_both_commands_are_in_the_table(self):
        assert COMMANDS["experiments"].run is audit.command_experiments
        assert COMMANDS["phases"].run is audit.command_phases

    def test_recording_refuses_a_row_outside_the_vocabulary(
        self, tmp_path: Path, capsys
    ):
        path = tmp_path / "row.json"
        path.write_text(json.dumps(_record(outcome="splendid")), encoding="utf-8")

        code = audit.command_experiments(
            _namespace(record=str(path), signature="", phase=None, framework=None)
        )

        assert code == 1
        assert "splendid" in capsys.readouterr().err

    def test_a_lookup_over_the_shipped_ledger_prints(self, capsys):
        code = audit.command_experiments(
            _namespace(record=None, signature="", phase=None, framework=None)
        )

        assert code == 0
        assert capsys.readouterr().out

    def test_the_phase_command_prints_every_phase(self, capsys):
        assert audit.command_phases(_namespace(node=None)) == 0
        printed = capsys.readouterr().out
        for phase in audit.PHASES:
            assert phase in printed

    def test_the_phase_command_answers_for_one_node(self, capsys):
        assert audit.command_phases(_namespace(node="analyze_stride_spoofing")) == 0

        assert capsys.readouterr().out.strip() == "analysis"

    def test_the_phase_command_refuses_a_node_no_phase_owns(self, capsys):
        assert audit.command_phases(_namespace(node="pontificate")) == 1

        assert "pontificate" in capsys.readouterr().err
