"""What a provider said it charged, from its response to the record.

Three layers, matching the three seams in :mod:`analysis_service.charges`: the
shapes a figure can arrive in, the rule that decides whether it may be recorded,
and the two subclasses that carry it. The rule's other half — which arrangement
a deployment declares — is tested beside the loader that reads it, in
``tests/test_model_tiers.py``.
"""

from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from analysis_service.charges import (
    CHARGE_METADATA_KEY,
    UPSTREAM_METADATA_KEY,
    ChargeModeMismatchError,
    charge_capturing_client_class,
    charge_reporting_llm_class,
    recordable_charge,
    records_reported_charge,
    reported_charge_of,
    served_upstream_of,
    stated_arrangement_of,
)
from analysis_service.vendors import VENDOR_NAMES, ChargeMode, vendor_for

_COST_HEADER = "llm_provider-x-litellm-response-cost"


def _response(hidden, byok=None, provider=None):
    """A stand-in for litellm's own response object.

    ``_hidden_params`` is where litellm files the charge; ``usage`` is the block
    it parses from the body and keeps verbatim, which is where the provider's
    own statement of the arrangement arrives; ``provider`` is a top-level extra
    litellm keeps the same way.
    """
    usage = SimpleNamespace() if byok is None else SimpleNamespace(is_byok=byok)
    response = SimpleNamespace(_hidden_params=hidden, usage=usage)
    if provider is not None:
        response.provider = provider
    return response


def _with_charge(value, byok=None, provider=None):
    return _response(
        {"additional_headers": {_COST_HEADER: value}}, byok=byok, provider=provider
    )


class TestTheShapesAFigureArrivesIn:
    """Every shape the path can take, because the producer decides them all.

    litellm's ``_hidden_params`` is a pinned dependency's internal spelling, and
    the value inside it has been a string in that dictionary's other entries. So
    each step is read as absent-able rather than asserted, and this class is the
    list of what "absent" turned out to mean.
    """

    def test_a_response_without_hidden_params_reports_nothing(self):
        assert reported_charge_of(SimpleNamespace()) is None

    def test_hidden_params_that_is_not_a_mapping_reports_nothing(self):
        assert reported_charge_of(_response("charged")) is None

    def test_missing_additional_headers_reports_nothing(self):
        assert reported_charge_of(_response({})) is None

    def test_additional_headers_that_is_not_a_mapping_reports_nothing(self):
        assert reported_charge_of(_response({"additional_headers": []})) is None

    def test_a_provider_that_stated_no_charge_reports_nothing(self):
        assert reported_charge_of(_response({"additional_headers": {}})) is None

    def test_an_explicit_none_reports_nothing(self):
        assert reported_charge_of(_with_charge(None)) is None

    def test_a_float_is_the_charge(self):
        assert reported_charge_of(_with_charge(0.00042)) == pytest.approx(0.00042)

    def test_an_integer_is_the_charge(self):
        assert reported_charge_of(_with_charge(2)) == pytest.approx(2.0)

    def test_a_string_is_read_as_the_number_it_spells(self):
        """Headers arrive as text, and this one has not always been parsed."""
        assert reported_charge_of(_with_charge("0.00042")) == pytest.approx(0.00042)

    def test_a_zero_charge_is_kept(self):
        """A free or fully cached call really can cost nothing.

        Reading it as "unreported" would turn a measurement into a hole.
        """
        assert reported_charge_of(_with_charge(0.0)) == 0.0

    @pytest.mark.parametrize("value", ["free", "", [0.1], {"usd": 1}])
    def test_a_value_that_is_not_a_number_is_refused(self, value):
        assert reported_charge_of(_with_charge(value)) is None

    @pytest.mark.parametrize("value", [-0.01, math.inf, -math.inf, math.nan])
    def test_a_value_outside_the_plausible_range_is_refused(self, value):
        """A cost cannot be negative, and no arithmetic here survives a NaN."""
        assert reported_charge_of(_with_charge(value)) is None


def _every_vendor_and_arrangement():
    """Every ``(vendor, mode)`` pair, read off the registry rather than listed.

    Each vendor is asked about every arrangement in the vocabulary and about no
    arrangement at all, so a row added tomorrow is covered here without an edit.
    """
    return [
        pytest.param(name, mode, id=f"{name}-{mode.value if mode else 'undeclared'}")
        for name in sorted(VENDOR_NAMES)
        for mode in (None, *ChargeMode)
    ]


class TestWhichFiguresAreRecorded:
    """The rule, asked for every vendor the registry holds.

    Completeness cannot see a wrong value, so the two OpenRouter answers are
    asserted for what they are rather than for being present.
    """

    @pytest.mark.parametrize(("name", "mode"), _every_vendor_and_arrangement())
    def test_a_vendor_that_reports_no_charge_records_none(self, name, mode):
        vendor = vendor_for(name)
        if vendor.reports_charge:
            pytest.skip(f"{name} reports a charge; the property tests below cover it")
        assert records_reported_charge(vendor, mode) is False

    def test_the_scan_is_not_vacuously_empty(self):
        """A registry where nothing reports a charge would pass the class above."""
        reporting = [name for name in VENDOR_NAMES if vendor_for(name).reports_charge]
        assert reporting, (
            "no vendor in the registry reports a charge, so every test in this"
            " module that means to exercise the recording path is skipping"
        )

    def test_a_whole_charge_is_recorded(self):
        """Under this account's own key, what OpenRouter charged is what it cost."""
        assert records_reported_charge(vendor_for("openrouter"), ChargeMode.DIRECT)

    def test_a_part_of_a_charge_is_not_recorded(self):
        """Under a key of the operator's own, the figure is the routing fee.

        The upstream provider charges the operator elsewhere, and litellm never
        reads the field that names that half — so there is no sum to record, and
        the fee alone would read as the cost of the call.
        """
        assert not records_reported_charge(
            vendor_for("openrouter"), ChargeMode.OWN_UPSTREAM_KEY
        )

    def test_an_undeclared_arrangement_records_nothing(self):
        """The loader refuses this state; a hand-built config can still reach it."""
        assert not records_reported_charge(vendor_for("openrouter"), None)

    @pytest.mark.parametrize("name", sorted(VENDOR_NAMES))
    def test_every_arrangement_a_vendor_allows_answers_the_question(self, name):
        """A row that lists an arrangement says what its figure covers.

        The completeness half: a new entry cannot be a key with nothing behind
        it, because the rule reads the entry and not the key.
        """
        vendor = vendor_for(name)
        for mode in vendor.charge_modes:
            assert isinstance(vendor.charges[mode].covers_whole_call, bool)


class TestTheProviderContradictsTheDeclaration:
    """What a response says about the arrangement, against what was declared.

    OpenRouter states ``is_byok`` in the same ``usage`` block it states the
    charge in, and litellm keeps it. The declaration is still the mechanism —
    the flag can **contradict** one and never supply one — because a figure
    worth recording only where a provider describes its own charge rests on
    that description.
    """

    def test_a_direct_account_that_answers_as_byok_stops_the_run(self):
        """The figure would be the routing fee recorded as a whole cost."""
        with pytest.raises(ChargeModeMismatchError) as caught:
            recordable_charge(
                REPORTING, ChargeMode.DIRECT, _with_charge(0.5, byok=True)
            )
        message = str(caught.value)
        assert "charges.openrouter" in message
        assert "'direct'" in message and "'own_upstream_key'" in message

    def test_a_byok_account_that_answers_as_direct_stops_the_run(self):
        """The other direction, which records nothing while a charge arrives.

        It moves no number wrongly, and it is still a wrong declaration: the
        deployment discards a whole charge every call and reports a cost of
        nothing.
        """
        with pytest.raises(ChargeModeMismatchError):
            recordable_charge(
                REPORTING, ChargeMode.OWN_UPSTREAM_KEY, _with_charge(0.5, byok=False)
            )

    def test_the_arrangement_is_checked_even_where_no_charge_arrived(self):
        """The contradiction is about the arrangement, not about the figure."""
        response = _response({"additional_headers": {}}, byok=True)
        with pytest.raises(ChargeModeMismatchError):
            recordable_charge(REPORTING, ChargeMode.DIRECT, response)

    def test_an_agreeing_flag_records_the_charge(self):
        assert recordable_charge(
            REPORTING, ChargeMode.DIRECT, _with_charge(0.5, byok=False)
        ) == pytest.approx(0.5)

    def test_an_agreeing_byok_flag_still_records_nothing(self):
        """Agreement is not the question the recording rule asks.

        Under this arrangement the reported figure covers part of the call,
        which is true whether or not the provider confirms the arrangement.
        """
        assert (
            recordable_charge(
                REPORTING, ChargeMode.OWN_UPSTREAM_KEY, _with_charge(0.5, byok=True)
            )
            is None
        )

    def test_a_provider_that_states_no_arrangement_leaves_the_declaration(self):
        """Absence is the ordinary case and never a contradiction."""
        assert stated_arrangement_of(_with_charge(0.5)) is None
        assert recordable_charge(
            REPORTING, ChargeMode.DIRECT, _with_charge(0.5)
        ) == pytest.approx(0.5)

    @pytest.mark.parametrize("value", [1, 0, "true", "", None, [True]])
    def test_a_flag_that_is_not_a_boolean_states_nothing(self, value):
        """``1`` and ``0`` are equal to ``True`` and ``False`` as dict keys.

        A lookup that accepted them would read an integer flag as an
        arrangement the provider never stated, and stop a run over it.
        """
        response = _response({"additional_headers": {}}, byok=value)
        assert stated_arrangement_of(response) is None
        assert recordable_charge(REPORTING, ChargeMode.DIRECT, response) is None

    def test_a_response_with_no_usage_block_states_nothing(self):
        assert stated_arrangement_of(SimpleNamespace()) is None

    def test_a_vendor_that_reports_no_charge_is_never_contradicted(self):
        """It declares no arrangement, so a flag has nothing to disagree with.

        Such a vendor never gets the capturing client either. This is the rule
        answering for a caller that has one anyway, rather than a second rule
        about who calls it.
        """
        assert recordable_charge(SILENT, None, _with_charge(0.5, byok=True)) is None


class TestWhichUpstreamAnswered:
    """The evidence a gateway volunteers and litellm keeps.

    Measured 2026-09-11: ``response.provider`` reads ``'DeepInfra'`` on a Llama
    call and ``'Claude Platform on AWS'`` on a Claude one. It names a serving
    organisation, so it is recorded and never hashed.
    """

    def test_a_named_upstream_is_read(self):
        assert served_upstream_of(_with_charge(0.1, provider="DeepInfra")) == (
            "DeepInfra"
        )

    def test_a_direct_vendor_names_none(self):
        """``None`` rather than the vendor's own name.

        Attribution derived from the route is exactly what this field replaces,
        so a response that says nothing leaves it empty.
        """
        assert served_upstream_of(_with_charge(0.1)) is None

    @pytest.mark.parametrize("value", ["", "   ", 7, True, None, ["DeepInfra"]])
    def test_a_value_that_is_not_a_name_states_nothing(self, value):
        assert served_upstream_of(_with_charge(0.1, provider=value)) is None

    def test_a_name_is_trimmed_and_bounded(self):
        """A third party's free text on its way into a report and an artifact."""
        assert served_upstream_of(_with_charge(0.1, provider="  AWS  ")) == "AWS"
        long = served_upstream_of(_with_charge(0.1, provider="x" * 400))
        assert long is not None and len(long) == 100

    def test_it_is_stamped_beside_the_charge(self):
        (response,) = asyncio.run(
            _drive(_Reporting(_client(charge=0.25, provider="DeepInfra")))
        )
        assert response.custom_metadata == {
            CHARGE_METADATA_KEY: pytest.approx(0.25),
            UPSTREAM_METADATA_KEY: "DeepInfra",
        }

    def test_an_upstream_is_stamped_where_no_charge_is_recorded(self):
        """The two facts are independent, and a BYOK route records only one."""
        client = _client(
            charge=0.25, provider="DeepInfra", mode=ChargeMode.OWN_UPSTREAM_KEY
        )
        (response,) = asyncio.run(_drive(_Reporting(client)))
        assert response.custom_metadata == {UPSTREAM_METADATA_KEY: "DeepInfra"}

    def test_a_response_that_says_neither_is_stamped_with_neither(self):
        (response,) = asyncio.run(_drive(_Reporting(_client())))
        assert response.custom_metadata is None


class _FakeResponse:
    """The shape ADK hands back: something with ``custom_metadata``."""

    def __init__(self):
        self.custom_metadata = None


class _FakeClient:
    """ADK's client seam, answering with whatever the test scripts.

    The capturing subclass owns the constructor — it takes the vendor and the
    declared arrangement — so what a response says is scripted onto the instance
    afterwards by :func:`_client`.
    """

    charge = None
    byok = None
    provider = None

    async def acompletion(self, **kwargs):
        headers = {} if self.charge is None else {_COST_HEADER: self.charge}
        return _response(
            {"additional_headers": headers}, byok=self.byok, provider=self.provider
        )


class _FakeLlm:
    """ADK's adapter seam: it calls the client, then yields its own response."""

    #: How many responses one call yields. One is what a schema-bound,
    #: non-streaming call produces; more is what the seam has to survive.
    responses = 1

    def __init__(self, llm_client):
        self.llm_client = llm_client

    async def generate_content_async(self, llm_request, stream: bool = False):
        await self.llm_client.acompletion(model="m", messages=[], tools=[])
        for _ in range(self.responses):
            yield _FakeResponse()


class _TwoResponseLlm(_FakeLlm):
    """One call, two responses — the shape that could double a charge."""

    responses = 2


class _StreamingLlm(_FakeLlm):
    """A call whose client answers with a stream wrapper rather than a response.

    What ADK hands back when ``stream=True``: an object carrying neither
    ``_hidden_params`` nor ``usage``. This service binds an ``output_schema`` on
    every node and so never streams, which is what makes the branch untravelled
    rather than absent — and an untravelled branch that raised would be found
    by whoever first turned streaming on.
    """

    async def generate_content_async(self, llm_request, stream: bool = False):
        await self.llm_client.acompletion(model="m", messages=[], tools=[])
        yield _FakeResponse()


class _StreamClient(_FakeClient):
    """A client that answers with a wrapper, as litellm does for a stream."""

    async def acompletion(self, **kwargs):
        return SimpleNamespace()


_Capturing = charge_capturing_client_class(_FakeClient)
_Reporting = charge_reporting_llm_class(_FakeLlm)

#: The registry's two answers: a vendor that states what it charged, and one
#: that states token counts alone. Read from the registry rather than built, so
#: these tests exercise the rows a deployment can actually select.
REPORTING = vendor_for("openrouter")
SILENT = vendor_for("anthropic")


def _client(
    charge=None,
    byok=None,
    provider=None,
    vendor=REPORTING,
    mode=ChargeMode.DIRECT,
):
    """One capturing client, scripted to answer as a provider would."""
    client = _Capturing(vendor, mode)
    client.charge = charge
    client.byok = byok
    client.provider = provider
    return client


async def _drive(adapter):
    return [response async for response in adapter.generate_content_async(None)]


def _stamp_of(adapter):
    (response,) = asyncio.run(_drive(adapter))
    return (response.custom_metadata or {}).get(CHARGE_METADATA_KEY)


class TestTheChargeReachesTheRecord:
    """The two subclasses, composed as ``binding`` composes them.

    The client is the only code that holds litellm's response and the adapter is
    the only code that holds ADK's, so the figure crosses between them in a
    context variable. What these tests are really about is that it crosses to
    the right response and to no other.
    """

    def test_a_captured_charge_is_stamped_on_the_response(self):
        assert _stamp_of(_Reporting(_client(charge=0.0031))) == pytest.approx(0.0031)

    def test_a_client_that_captures_nothing_stamps_nothing(self):
        """An adapter on a vendor that reports no charge keeps ADK's own client."""
        assert _stamp_of(_Reporting(_FakeClient())) is None

    def test_a_provider_that_reported_no_charge_stamps_nothing(self):
        assert _stamp_of(_Reporting(_client(charge=None))) is None

    def test_one_adapter_never_stamps_another_adapter_s_figure(self):
        """Two tiers can run in one task, and the second must not inherit.

        This is what the variable is cleared for. A deployment with OpenRouter
        on ``strong`` and a direct vendor on ``base`` would otherwise record the
        gateway's charge against a call the gateway never saw.
        """

        async def both():
            first = await _drive(_Reporting(_client(charge=0.5)))
            second = await _drive(_Reporting(_FakeClient()))
            return first, second

        (first,), (second,) = asyncio.run(both())
        assert first.custom_metadata[CHARGE_METADATA_KEY] == pytest.approx(0.5)
        assert second.custom_metadata is None

    def test_one_call_stamps_its_charge_once(self):
        """A charge on two responses of one call reads as twice the money."""
        adapter = charge_reporting_llm_class(_TwoResponseLlm)(_client(charge=0.25))
        stamped = [
            (response.custom_metadata or {}).get(CHARGE_METADATA_KEY)
            for response in asyncio.run(_drive(adapter))
        ]
        assert stamped == [pytest.approx(0.25), None]

    def test_a_contradicted_declaration_stops_the_drive(self):
        """The refusal travels the path, not only the rule.

        A run whose provider disagrees with the declaration stops at the node
        that found out, which costs one node's tokens and names the key to
        change.
        """
        adapter = _Reporting(_client(charge=0.5, byok=True))
        with pytest.raises(ChargeModeMismatchError):
            asyncio.run(_drive(adapter))

    def test_a_streamed_call_records_nothing_and_raises_nothing(self):
        """The shape the reader was written for but never met.

        A stream wrapper carries no usage block and no hidden params, so every
        read answers "nothing said" rather than failing. Nothing is recorded,
        which is honest: what a streamed call cost is stated in a place this
        path does not see.
        """
        capturing = charge_capturing_client_class(_StreamClient)
        adapter = charge_reporting_llm_class(_StreamingLlm)(
            capturing(REPORTING, ChargeMode.DIRECT)
        )

        (response,) = asyncio.run(_drive(adapter))

        assert response.custom_metadata is None

    def test_concurrent_nodes_each_keep_their_own_figure(self):
        """Every node on a tier shares one adapter and runs in its own task."""
        charges = [0.1, 0.2, 0.3]

        async def concurrently():
            adapters = [_Reporting(_client(charge=charge)) for charge in charges]
            return await asyncio.gather(*(_drive(adapter) for adapter in adapters))

        stamped = [
            response.custom_metadata[CHARGE_METADATA_KEY]
            for (response,) in asyncio.run(concurrently())
        ]
        assert stamped == pytest.approx(charges)


class TestTheStoredFigureIsHeldToTheRuleThatWroteIt:
    """The bounds this module states, held on the models that store its values.

    Each was spelled twice: the finite rule here and ``ge=0`` alone on the
    record, the length here and a literal in two models. A stored value is only
    as trustworthy as whatever wrote it, and a hand-edited artifact is exactly
    the case both readers exist for.
    """

    def test_a_non_finite_charge_cannot_be_read_back(self):
        """The regression, in the shape that proved it.

        ``inf`` validated, summed to ``inf`` through ``charges_by_node``, and
        re-serialised as ``null`` — so a report read back and re-dumped moved
        the bytes an attestation seals. Both read-back paths are driven,
        because ``json.loads`` accepts ``Infinity`` and pydantic's own parser
        does too.
        """
        from analysis_service.report import NodeRun

        raw = '{"node":"x","duration_ms":1,"reported_charge_usd":Infinity}'
        with pytest.raises(ValidationError):
            NodeRun.model_validate_json(raw)
        with pytest.raises(ValidationError):
            NodeRun.model_validate(json.loads(raw))

    def test_the_length_bound_has_one_reader(self):
        """The producer's bound and the two records that store its output.

        Compared against the bound rather than against 100, so widening it
        moves all three together instead of making two readers refuse what the
        third writes.
        """
        from analysis_service.charges import UPSTREAM_MAX_CHARS
        from analysis_service.report import NodeRun
        from evals.harness.provenance import NodeExecution

        longest = "u" * UPSTREAM_MAX_CHARS
        assert (
            NodeRun(node="x", duration_ms=1, served_upstream=longest).served_upstream
            == longest
        )
        for model in (NodeRun, NodeExecution):
            with pytest.raises(ValidationError):
                model.model_validate({"served_upstream": longest + "u"})
