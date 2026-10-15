"""The capabilities a job's sources state, read by a model and checked by code (ADR 0086)."""

from __future__ import annotations

import asyncio
import json

import pytest

from analysis_service import graph
from analysis_service.budgets import llm_calls_for
from analysis_service.capabilities import CAPABILITIES
from analysis_service.capability_reader import (
    CapabilityReading,
    CapabilityRow,
    with_stated_capabilities,
)
from analysis_service.frameworks import PACKAGES, reads_capabilities
from analysis_service.jobs import JobRecord, PipelineAwaiting
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.pipeline import AdkPipelineRunner
from analysis_service.prompts import compose_capabilities_prompt
from analysis_service.report import FrameworkSelection
from analysis_service.sources import ANSWERS_LABEL, DEFAULT_DESCRIPTION_LABEL, Source
from analysis_service.system_model import CapabilityStatement
from tests.factories import (
    DESCRIPTION_TEXT,
    PROJECT_ROOT,
    emitted,
    sample_selection,
    scripted_pipeline,
    valid_model,
)
from tests.test_graph import FakeContext

SOURCES = {DEFAULT_DESCRIPTION_LABEL: DESCRIPTION_TEXT}
READER = next(name for name in PACKAGES if reads_capabilities((name,)))
OTHERS = tuple(name for name in PACKAGES if not reads_capabilities((name,)))


def _row(capability, quote, label=DEFAULT_DESCRIPTION_LABEL):
    return CapabilityRow(capability=capability, source_label=label, quote=quote)


def _stated(*rows, model=None):
    model = model or valid_model()
    updated = with_stated_capabilities(
        model, CapabilityReading(capabilities=list(rows)), SOURCES
    )
    return {s.capability: s for s in updated.capabilities}


class TestTheChecks:
    def test_a_row_whose_quote_is_in_its_source_is_stated_present(self):
        stated = _stated(_row("authentication", "Customers log in to the web app"))
        statement = stated["authentication"]
        assert statement.state == "present"
        assert statement.source_label == DEFAULT_DESCRIPTION_LABEL
        assert statement.source_excerpt == "Customers log in to the web app"
        assert (statement.origin, statement.basis) == ("reader", "")

    @pytest.mark.parametrize(
        ("row", "why"),
        [
            (_row("authentication", "customers sign in with a passkey"), "quote"),
            (_row("authentication", "Customers log in", "Kickoff call"), "label"),
        ],
    )
    def test_a_row_its_source_does_not_hold_is_dropped(self, row, why):
        assert "authentication" not in _stated(row), why

    def test_a_key_outside_the_table_is_dropped(self):
        assert "teleport" not in _stated(_row("teleport", "Customers log in"))

    def test_a_row_that_quotes_the_answers_is_dropped(self):
        """An answer writes its own capability; a row quoting it would look
        like one."""
        sources = SOURCES | {ANSWERS_LABEL: "Does it use OAuth? yes"}
        updated = with_stated_capabilities(
            valid_model(),
            CapabilityReading(capabilities=[_row("oauth", "yes", ANSWERS_LABEL)]),
            sources,
        )
        assert "oauth" not in {s.capability for s in updated.capabilities}

    def test_a_label_in_another_spelling_is_the_job_s_own(self):
        label = DEFAULT_DESCRIPTION_LABEL.upper()
        stated = _stated(
            _row("authentication", "Customers log in to the web app", label)
        )
        assert stated["authentication"].source_label == DEFAULT_DESCRIPTION_LABEL

    def test_a_statement_always_wins(self):
        model = valid_model()
        answer = CapabilityStatement(
            capability="authentication",
            state="absent",
            source_excerpt="no",
            source_label="Answers",
            origin="answer",
        )
        model.capabilities.append(answer)
        stated = _stated(
            _row("authentication", "Customers log in to the web app"), model=model
        )
        assert stated["authentication"] == answer

    def test_a_second_row_for_one_capability_adds_nothing(self):
        stated = with_stated_capabilities(
            valid_model(),
            CapabilityReading(
                capabilities=[
                    _row("database", "orders are stored in Postgres"),
                    _row("database", "The web app runs on Cloud Run"),
                ]
            ),
            SOURCES,
        )
        assert [s.capability for s in stated.capabilities] == ["database"]

    def test_the_schema_has_no_state_so_a_row_cannot_state_an_absence(self):
        assert "state" not in CapabilityRow.model_json_schema()["properties"]
        enum = CapabilityRow.model_json_schema()["properties"]["capability"]["enum"]
        assert set(enum) == set(CAPABILITIES)


class TestThePrompt:
    def test_the_table_is_rendered_from_the_registry(self):
        prompt = compose_capabilities_prompt(MarkdownLoader(PROJECT_ROOT / "prompts"))
        assert all(f"`{key}`" in prompt for key in CAPABILITIES)
        assert prompt.replace("{input_text}", "").count("{") == 0


class TestTheGraph:
    def _run(self, reading):
        pipeline, _ = scripted_pipeline(
            {
                "extract": emitted(valid_model()),
                graph.CAPABILITIES_NODE: json.dumps(reading),
            },
            entry=graph.ENTRY_HEAD_ONLY,
            frameworks=(READER,),
            capability_reader=True,
        )
        record = JobRecord.create(
            owner_id="alice",
            sources=[Source.description(DESCRIPTION_TEXT)],
            frameworks=sample_selection((READER,)),
            ask_questions=True,
        )
        record.transition("running")
        visited: list[str] = []

        async def on_node(node: str) -> None:
            visited.append(node)

        outcome = asyncio.run(AdkPipelineRunner(pipeline).run(record, on_node))
        return outcome, visited

    def test_a_paused_job_holds_the_capabilities_its_sources_state(self):
        outcome, visited = self._run(
            {
                "capabilities": [
                    {
                        "capability": "database",
                        "source_label": DEFAULT_DESCRIPTION_LABEL,
                        "quote": "orders are stored in Postgres",
                    },
                    {
                        "capability": "browser-frontend",
                        "source_label": DEFAULT_DESCRIPTION_LABEL,
                        "quote": "customers browse a catalogue",
                    },
                ]
            }
        )
        assert isinstance(outcome, PipelineAwaiting)
        stated = {s.capability: s for s in outcome.checkpoint.system_model.capabilities}
        assert stated["database"].origin == "reader"
        assert "browser-frontend" not in stated, "its quote is not in the source"
        assert stated["tls-use"].origin == "field", "a closed field named it first"
        assert visited.index(graph.CAPABILITIES_NODE) < visited.index(graph.STATED_NODE)

    def test_a_reader_that_wrote_nothing_stops_the_job(self):
        """A completion with no text writes no key; the next node says so."""
        stated = graph._stated_node_func(graph.GraphKeys.of((READER,)))
        with pytest.raises(graph.SilentNodeError, match="capability reader"):
            stated(valid_model().model_dump(mode="json"), FakeContext(), SOURCES)

    @pytest.mark.parametrize(
        "entry", [graph.ENTRY_PREPARE, graph.ENTRY_RESUME, graph.ENTRY_EXTRACT_ONLY]
    )
    def test_an_entry_that_passes_no_extracted_model_on_refuses_the_reader(self, entry):
        with pytest.raises(ValueError, match="capability reader"):
            scripted_pipeline(
                {}, entry=entry, frameworks=(READER,), capability_reader=True
            )

    @pytest.mark.skipif(not OTHERS, reason="every package reads capabilities")
    def test_a_selection_that_reads_no_capability_refuses_the_reader(self):
        with pytest.raises(ValueError, match="capabilities"):
            scripted_pipeline({}, frameworks=OTHERS, capability_reader=True)


class TestTheCost:
    def test_the_reader_counts_once_where_a_selection_reads_capabilities(self):
        reader = [FrameworkSelection(name=READER)]
        expected = 3 + 1 + len(PACKAGES[READER].lanes) + 2
        assert llm_calls_for(reader) == expected
        for name in OTHERS:
            selection = [FrameworkSelection(name=name)]
            assert llm_calls_for(selection) == 3 + len(PACKAGES[name].lanes) + 2
