"""The compact extraction transport, and the adapter that expands it.

Two questions, and the second is the one that makes the first worth asking.
Does a compact payload expand into exactly the System Model the full route
would have produced from the same facts — and does a payload that says
something malformed fail loudly rather than expanding into a model that looks
fine? An adapter that quietly resolved an ambiguous reference would pass every
equivalence test here and poison a report.

The equivalence half is checked twice over: once against a hand-written
fixture whose expected full model is the shared ``valid_model`` factory, and
once against all thirteen blessed corpus models, which is the only data in the
repo that looks like real extraction output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from analysis_service.compact import (
    COMPACT_ELEMENTS,
    COMPACT_FORMAT,
    EXTRACTION_FORMATS,
    FULL_FORMAT,
    PROVISIONAL_PREFIX,
    REFERENCE_FIELDS,
    CompactSystemModel,
    ExtractionFormat,
    expand,
    parse_extraction,
)
from analysis_service.graph import EXTRACTION_SCHEMAS
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.prompts import (
    EXTRACT_COMPACT_PROMPT_NAME,
    EXTRACT_PROMPT_NAME,
    EXTRACTION_DELTAS,
    compose_extract_prompt,
)
from analysis_service.sources import DEFAULT_DESCRIPTION_LABEL
from analysis_service.system_model import (
    ELEMENT_GROUPS,
    Element,
    SystemModel,
    _Element,
    normalize_element_ids,
)
from analysis_service.validation import repair_scope
from evals.bench.deterministic import as_compact
from tests.factories import valid_model
from tests.test_validation import codes

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_MODELS = sorted((PROJECT_ROOT / "evals" / "corpus").glob("*/model.json"))


def compact_fixture() -> dict:
    """The compact payload whose expansion is ``valid_model()``.

    Written out rather than converted, because a converter would be the
    adapter's own logic run backwards, and a test whose fixture agrees with the
    code by construction checks nothing. Every field the full model carries is
    here except the six ``id`` values, which the refs replace.
    """
    label = DEFAULT_DESCRIPTION_LABEL
    return {
        "external_entities": [
            {
                "ref": "cust",
                "name": "Customer",
                "kind": "human",
                "trust_zone": "net",
                "source_excerpt": "customers log in from the browser",
                "source_label": label,
                "assets": ["pii"],
            }
        ],
        "processes": [
            {
                "ref": "app",
                "name": "Web App",
                "technology": "Python/FastAPI on Cloud Run",
                "trust_zone": "corp",
                "exposure": "internet-facing",
                "interface_kind": "web",
                "source_excerpt": "the web app runs on Cloud Run",
                "source_label": label,
            }
        ],
        "data_stores": [
            {
                "ref": "db",
                "name": "Orders DB",
                "technology": "Cloud SQL Postgres",
                "trust_zone": "corp",
                "data_classification": "confidential",
                "encryption_at_rest": "unknown",
                "source_excerpt": "orders are stored in Postgres",
                "source_label": label,
                "assets": ["business-critical-data", "pii"],
            }
        ],
        "data_flows": [
            {
                "ref": "login",
                "name": "Login",
                "source": "cust",
                "destination": "app",
                "protocol": "HTTPS",
                "authentication": "session cookie",
                "data_description": "credentials in, session out",
                "encryption_in_transit": "TLS 1.3",
                "operations": "unknown",
                "source_excerpt": "customers log in from the browser",
                "source_label": label,
            },
            {
                "ref": "store",
                "name": "Store Order",
                "source": "app",
                "destination": "db",
                "protocol": "Postgres wire protocol",
                "authentication": "IAM database auth",
                "data_description": "order rows",
                "encryption_in_transit": "unknown",
                "operations": "write",
                "source_excerpt": "orders are stored in Postgres",
                "source_label": label,
            },
        ],
        "trust_boundaries": [
            {
                "ref": "net",
                "name": "Internet",
                "kind": "network",
                "source_excerpt": "customers log in from the browser",
                "source_label": label,
            },
            {
                "ref": "corp",
                "name": "Internal Network",
                "kind": "network",
                "source_excerpt": "the web app runs on Cloud Run",
                "source_label": label,
            },
        ],
        "assumptions": [
            {
                "assumption": "web app is internet-facing",
                "element": "app",
                "attribute": "exposure",
                "basis": "customers reach it directly from the browser",
            }
        ],
    }


class TestTheTransportCarriesTheSameFacts:
    def test_a_compact_payload_expands_into_the_expected_full_model(self):
        """The whole claim of the transport, against a model written by hand."""
        model, issues = parse_extraction(compact_fixture(), COMPACT_FORMAT)

        assert issues == []
        assert model == normalize_element_ids(valid_model())

    @pytest.mark.parametrize("path", CORPUS_MODELS, ids=lambda p: p.parent.name)
    def test_every_blessed_corpus_model_survives_the_round_trip(self, path):
        """Thirteen real extractions, through the wire form and back unchanged.

        The hand-written fixture above proves the shape; this proves it on the
        only data in the repo that looks like what a model actually emits —
        every element type, parallel flows, assumptions, long descriptions and
        excerpts.
        """
        blessed = json.loads(path.read_text(encoding="utf-8"))
        expected, _ = parse_extraction(blessed, FULL_FORMAT)
        through_the_wire, issues = parse_extraction(as_compact(blessed), COMPACT_FORMAT)

        assert codes(issues) == []
        assert through_the_wire == expected

    def test_expansion_is_deterministic(self):
        """No model call, no clock, no set iteration: the same bytes each time."""
        first, first_issues = expand(compact_fixture())
        second, second_issues = expand(compact_fixture())

        assert first == second
        assert first_issues == second_issues

    def test_parallel_flows_between_one_pair_both_survive(self):
        """Two interactions between the same endpoints stay two interactions.

        The transport's most dangerous compression would be treating a flow as
        a pair of endpoints, which would collapse "reads the rota" and "writes
        the rota" into one arrow and delete a threat with it.
        """
        payload = compact_fixture()
        second = dict(payload["data_flows"][1], ref="read", name="Read Order")
        second["operations"] = "read"
        payload["data_flows"].append(second)

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert codes(issues) == []
        assert [flow.id for flow in model.data_flows] == [
            "flow:customer-to-web-app:login",
            "flow:web-app-to-orders-db:store-order",
            "flow:web-app-to-orders-db:read-order",
        ]


class TestIdentityIsDerivedAndNeverInvented:
    def test_expansion_leaves_no_provisional_id_behind(self):
        """Every ID a consumer sees is the one the ID rule derives.

        The adapter writes a stand-in and the ID rule overwrites it. A
        provisional ID surviving into a validated model would mean the rewrite
        missed a reference.
        """
        model, _ = parse_extraction(compact_fixture(), COMPACT_FORMAT)
        rendered = json.dumps(model.model_dump(mode="json"))

        assert PROVISIONAL_PREFIX not in rendered

    def test_a_provisional_id_cannot_be_spelled_by_a_derived_one(self):
        """The prefix is what makes the rewrite safe, so it is checked.

        If a provisional ID could equal some other element's derived ID, the
        rewrite map would bind a reference to the wrong element and nothing
        downstream would say so.
        """
        prefixes = {element.id_prefix for element in get_args(Element)}

        assert all(not prefix.startswith(PROVISIONAL_PREFIX) for prefix in prefixes)
        assert all(PROVISIONAL_PREFIX not in prefix for prefix in prefixes)

    def test_a_ref_never_reaches_the_model_as_a_name_or_a_description(self):
        """A ref is transport and nothing else: it names no element downstream."""
        model, _ = parse_extraction(compact_fixture(), COMPACT_FORMAT)

        assert [element.name for element in model.elements()] == [
            "Customer",
            "Web App",
            "Orders DB",
            "Login",
            "Store Order",
            "Internet",
            "Internal Network",
        ]


class TestAReferenceResolvesOrTheGateSaysSo:
    def test_a_dangling_ref_survives_as_itself_and_the_gate_reports_it(self):
        """The adapter invents no endpoint; the gate quotes what the model wrote."""
        payload = compact_fixture()
        payload["data_flows"][0]["destination"] = "nowhere"

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert model is not None
        assert codes(issues) == ["invalid-reference"]
        assert "'nowhere'" in issues[0].message

    def test_a_ref_of_the_wrong_type_resolves_and_then_fails_the_gate(self):
        """Reference typing is the gate's rule, so the diagnostic names a real element.

        Resolving first is what makes the message actionable: repair is told
        that a ``trust_zone`` points at ``process:web-app``, not that some token
        it no longer recognises went missing.
        """
        payload = compact_fixture()
        payload["data_stores"][0]["trust_zone"] = "app"

        _, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert codes(issues) == ["invalid-reference"]
        assert "process:web-app" in issues[0].message

    def test_a_ref_two_elements_claim_binds_to_neither(self):
        """The one thing the adapter must never do is pick.

        Both uses of the ref fail, and the ``duplicate-ref`` issue says why —
        without it a reader sees two dangling references and no cause.
        """
        payload = compact_fixture()
        payload["processes"].append(dict(payload["processes"][0], ref="db"))

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert model is not None
        assert "duplicate-ref" in codes(issues)
        # The one flow that pointed at ``db`` now points at nothing.
        assert codes(issues).count("invalid-reference") == 1
        duplicate = next(issue for issue in issues if issue.code == "duplicate-ref")
        assert "'db'" in duplicate.message

    def test_a_duplicate_ref_widens_the_repair_to_the_whole_model(self):
        """An ambiguous reference table has no narrower patch than the whole.

        A scoped repair would have to name one of the two elements the ref
        points at, which is the question nobody can answer.
        """
        payload = compact_fixture()
        payload["processes"].append(dict(payload["processes"][0], ref="db"))

        _, issues = parse_extraction(payload, COMPACT_FORMAT)
        scope, implicated = repair_scope(issues)

        assert (scope, implicated) == ("whole", [])

    def test_an_assumption_subject_resolves_through_the_same_table(self):
        """The fourth reference field, and it points at any element type."""
        payload = compact_fixture()
        payload["assumptions"][0]["element"] = "gone"

        _, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert "invalid-reference" in codes(issues)


class TestMalformedOutputFailsExplicitly:
    def test_an_omitted_required_field_is_a_transport_failure(self):
        """A missing epistemic field is malformed output, not an unknown fact.

        ``operations`` carries a default on a :class:`~analysis_service.system_model.DataFlow` so archived
        artifacts keep loading. Nothing archived is in this format, so the wire
        form asks for it outright.
        """
        payload = compact_fixture()
        del payload["data_flows"][0]["operations"]

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert model is None
        assert codes(issues) == ["schema"]

    def test_a_schema_issue_names_the_row_it_came_from(self):
        """Diagnostics stay actionable: the location is the payload's own path."""
        payload = compact_fixture()
        payload["processes"][0]["exposure"] = "sort of"

        _, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert issues[0].message.startswith("processes.0.exposure")

    def test_a_name_that_slugs_to_nothing_is_refused(self):
        """There is no emitted ID here to fall back on, so the name must slug.

        The full route lets such a name through with its emitted ID standing
        unchecked — a hole ``system_model`` records. This route closes it,
        because an element whose ID cannot be derived has no identity at all.
        """
        payload = compact_fixture()
        payload["processes"][0]["name"] = "!!!"

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert model is None
        assert codes(issues) == ["schema"]

    def test_a_ref_outside_the_slug_vocabulary_is_refused(self):
        """The ref pattern is a fence, not a tidiness rule.

        An unresolved ref is spliced into a flow's element ID, which is rendered
        into a lane agent's prompt inside a table nothing fences. A ref holding
        a newline and a backtick run would open a block there.
        """
        payload = compact_fixture()
        payload["data_flows"][0]["source"] = "a\n```"

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert model is None
        assert codes(issues) == ["schema"]

    def test_an_unknown_field_is_refused(self):
        """``extra="forbid"``: a field nobody reads is a fact silently dropped."""
        payload = compact_fixture()
        payload["processes"][0]["runtime"] = "python"

        model, issues = parse_extraction(payload, COMPACT_FORMAT)

        assert model is None
        assert codes(issues) == ["schema"]

    def test_a_payload_that_is_not_an_object_at_all_fails_closed(self):
        """Truncation arrives as any shape; none of them expand."""
        model, issues = expand("half a jso")

        assert model is None
        assert issues


class TestTheTablesAnswerTheirRegistries:
    def test_every_element_group_has_a_compact_row_type(self):
        """A sixth element type joins both sides of the transport or neither."""
        assert tuple(COMPACT_ELEMENTS) == ELEMENT_GROUPS

    @pytest.mark.parametrize("group", ELEMENT_GROUPS)
    def test_a_compact_row_carries_its_full_row_s_fields(self, group):
        """Field for field, with ``id`` replaced by ``ref``.

        The transport is allowed to rename an identifier and nothing else. A
        field added to an element type and forgotten here would be a fact the
        compact route cannot express, and the only sign of it would be a
        quieter extraction.
        """
        compact_type, element_type = COMPACT_ELEMENTS[group]
        renamed = {"element_id": "element"}

        expected = {renamed.get(name, name) for name in element_type.model_fields}
        expected = expected - {"id"} | {"ref"}
        assert set(compact_type.model_fields) == expected

    def test_a_compact_row_keeps_every_optional_default_the_full_row_has(self):
        """An omission has to mean the same thing on both routes.

        ``operations`` is the one declared exception: required here, defaulted
        on a :class:`~analysis_service.system_model.DataFlow`. Anything else diverging would make a compact run
        and a full run fail different models for the same emission.
        """
        diverging = {}
        for group, (compact_type, element_type) in COMPACT_ELEMENTS.items():
            for name, field in element_type.model_fields.items():
                if name == "id" or name not in compact_type.model_fields:
                    continue
                if field.is_required() != compact_type.model_fields[name].is_required():
                    diverging[f"{group}.{name}"] = field.default

        assert set(diverging) == {"data_flows.operations"}

    def test_the_root_lists_are_the_full_model_s_own(self):
        """One vocabulary for both routes, so expansion is a row-by-row copy."""
        assert set(CompactSystemModel.model_fields) == set(SystemModel.model_fields)

    def test_the_reference_fields_are_every_reference_an_element_carries(self):
        """The resolver walks a table, and the table answers the schema.

        A reference field added to an element type and missed here would be
        expanded as a literal ref: a dangling reference in every model, or worse
        a string that happens to resolve.
        """
        carried = {
            name
            for _, element_type in COMPACT_ELEMENTS.values()
            for name in element_type.model_fields
            if name not in _Element.model_fields
            and name.endswith(("zone", "source", "destination"))
        }

        assert set(REFERENCE_FIELDS) == carried

    def test_the_format_literal_answers_the_registry(self):
        """The report re-spells these strings, so the two have to agree."""
        assert get_args(ExtractionFormat) == EXTRACTION_FORMATS

    def test_both_transport_tables_answer_the_registry(self):
        """A transport is a schema and the text describing it, chosen together.

        Two tables rather than two branches, and both are held to the registry
        here: a transport with a schema and no prompt would ask a model for a
        shape it was never told about.
        """
        assert set(EXTRACTION_SCHEMAS) == set(EXTRACTION_FORMATS)
        assert set(EXTRACTION_DELTAS) == set(EXTRACTION_FORMATS)

    def test_an_unknown_transport_raises_at_both_seams(self):
        """Fail closed: never fall back to a route the caller did not ask for."""
        loader = MarkdownLoader(PROJECT_ROOT / "prompts")

        with pytest.raises(ValueError, match="unknown extraction format"):
            parse_extraction(compact_fixture(), "compact-v2")
        with pytest.raises(ValueError, match="unknown extraction format"):
            compose_extract_prompt(loader, "compact-v2")


class TestTheFullRouteIsUntouched:
    def test_the_full_route_is_the_gate_it_always_was(self):
        """Turning the flag off restores the existing route exactly."""
        model, issues = parse_extraction(
            valid_model().model_dump(mode="json"), FULL_FORMAT
        )

        assert issues == []
        assert model == normalize_element_ids(valid_model())

    def test_the_full_route_reads_the_body_alone(self):
        """The delta is appended, never merged, so ``extract.md`` cannot move.

        Stage 4 of #938 compares the two routes with everything else fixed. A
        delta that reached the shared body would change the full route's
        instruction digest and make every run before it incomparable.
        """
        loader = MarkdownLoader(PROJECT_ROOT / "prompts")

        assert compose_extract_prompt(loader) == (
            loader.load(EXTRACT_PROMPT_NAME).strip() + "\n"
        )
        assert EXTRACT_COMPACT_PROMPT_NAME not in compose_extract_prompt(loader)

    def test_a_full_payload_is_not_silently_read_as_compact(self):
        """The route is a fact of the build, never a guess about what parsed."""
        model, issues = expand(valid_model().model_dump(mode="json"))

        assert model is None
        assert codes(issues) == ["schema"] * len(issues)
