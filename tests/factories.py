"""Shared factories: a small, fully valid System Model, plus claims and a
complete report built against it.

Topology: customer (internet zone) -> web app -> orders db (both internal),
one cross-boundary flow and one intra-zone flow, one recorded assumption.

The claim-shaped factories here are **STRIDE's**, because STRIDE is the package
this repository carries and a fixture has to build something concrete. What they
are built *into* is neutral: :func:`sample_analysis` fills one
:class:`~analysis_service.frameworks.stride.record.StrideAnalysis` block and
:func:`sample_report` wraps it in the envelope beside the job's own selection,
so a test that means to talk about the envelope can hold a second block without
going through here.

Also home to :class:`ScriptedLlm`, the offline stand-in every graph-driving
test binds its nodes to. It lives here rather than in one test module because
the service tests and the eval tests must drive the graph through the *same*
stand-in. A copy that reported no ``model_version`` would let a whole class of
provenance defect stay invisible to the eval lane.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel, Field

from analysis_service import frameworks as framework_registry
from analysis_service.binding import NodeBinding
from analysis_service.frameworks import PACKAGES, FrameworkName, FrameworkPackage
from analysis_service.frameworks.asvs.record import (
    RequirementProposal,
    RequirementRulingProposal,
)
from analysis_service.frameworks.stride.record import (
    STRIDE_VERSION,
    DraftThreat,
    StrideAnalysis,
    Threat,
    ThreatProposal,
    ThreatRuling,
)
from analysis_service.graph import (
    ENTRY_EXTRACT,
    Entry,
    Pipeline,
    build_pipeline,
)
from analysis_service.identity import build_identity, execution_fingerprint
from analysis_service.markdown_loader import MarkdownLoader
from analysis_service.model_tiers import ModelTierConfig, load_model_tiers
from analysis_service.report import (
    FrameworkSelection,
    Ground,
    InputRef,
    Job,
    Mitigation,
    NodeRun,
    Report,
    Severity,
    Verdict,
)
from analysis_service.sampling import SamplingConfig, load_sampling
from analysis_service.sources import DEFAULT_DESCRIPTION_LABEL, Source
from analysis_service.system_model import (
    Assumption,
    DataFlow,
    DataStore,
    ExternalEntity,
    Process,
    SystemModel,
    TrustBoundary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The one framework this repository carries, as a one-name selection. Spelled
#: here rather than inline so a test that means "the default selection" and a
#: test that means "the ``stride`` package specifically" are distinguishable.
DEFAULT_FRAMEWORKS: tuple[FrameworkName, ...] = ("stride",)

BASE_MODEL = "fake-base-001"
STRONG_MODEL = "fake-strong-001"

# The shipped config selects no vendor, so a test that needs a resolvable tier
# config has to choose one. Deliberately **mixed**: the two tiers select
# independently, and a same-vendor pair on both sides would leave that path
# untested everywhere but here — which is exactly what the old Vertex-on-both
# default did. Nothing downstream of this reaches a network; the vendors are
# named so the binding and the gate see a realistic pair, not so a model runs.
TEST_TIER_ENV: dict[str, str] = {
    "ANALYSIS_MODEL_BASE_VENDOR": "openai",
    "ANALYSIS_MODEL_BASE_MODEL": "gpt-4.1-mini",
    "ANALYSIS_MODEL_STRONG_VENDOR": "vertex",
    "ANALYSIS_MODEL_STRONG_MODEL": "gemini-2.5-pro",
    # A third vendor on `review`, so the tier is not accidentally covered by
    # whatever `strong` happens to select. The shipped node map leaves criticism
    # on `strong`, so nothing here runs on this pair until a test moves it —
    # which is the point: an unexercised tier that is also unselectable would
    # hide every defect in selecting it.
    "ANALYSIS_MODEL_REVIEW_VENDOR": "anthropic",
    "ANALYSIS_MODEL_REVIEW_MODEL": "claude-opus-5",
}

# What the selection above implies: two API keys and one ADC triple, because the
# three tiers sit on vendors with different credential modes. Placeholders — the
# loader checks that a variable is *declared*, never that it authenticates, and
# no test here reaches a provider. Kept beside the selection so the two cannot
# drift; a tier moved to another vendor needs its variables added here too.
TEST_CREDENTIAL_ENV: dict[str, str] = {
    "ANALYSIS_ANTHROPIC_API_KEY": "sk-ant-not-a-real-key",
    "ANALYSIS_OPENAI_API_KEY": "sk-not-a-real-key",
    "ANALYSIS_VERTEX_PROJECT": "test-project",
    "ANALYSIS_VERTEX_LOCATION": "us-central1",
    "GOOGLE_APPLICATION_CREDENTIALS": "/nonexistent/adc.json",
}


def repo_tiers() -> ModelTierConfig:
    """The shipped tier config, resolved under the canonical test selection.

    One place, so the selection cannot drift between the nine modules that need
    it. Tests assert about topology, binding and provenance rather than about
    which vendor answered, so *which* pair this is remains a fixture detail.
    """
    return load_model_tiers(
        PROJECT_ROOT / "config" / "model_tiers.toml", env=TEST_TIER_ENV
    )


# Far above any ceiling a test configures, so seeding never refuses.
_SEEDING_CEILING = 1_000_000


# A stand-in instruction digest for a test that is not exercising the digest
# itself. Any 64-hex value works: the identity hashes it opaquely.
SAMPLE_INSTRUCTIONS = "1c" * 32


def sample_fingerprint(
    served: str,
    sampling: Any,
    *,
    requested: str | None = None,
    instructions: str = SAMPLE_INSTRUCTIONS,
    build: Any = None,
) -> str:
    """One execution fingerprint, with the parts a test does not care about filled.

    ``requested`` defaults to ``served``, which is what an offline stand-in
    produces: the scripted model echoes the route it was bound to, so the two
    routes agree. A test about the requested/served split passes both.
    """
    return execution_fingerprint(
        requested_route=served if requested is None else requested,
        served_route=served,
        sampling=sampling.model_dump() if hasattr(sampling, "model_dump") else sampling,
        instruction_sha256=instructions,
        build=build_identity() if build is None else build,
    )


async def admit(store: Any, record: Any) -> Any:
    """Put ``record`` in ``store`` without exercising the ceiling.

    :meth:`~analysis_service.jobs.JobStore.reserve` is the only way in, by
    design — the protocol carries no unconditional create, so a backend cannot
    offer one the API might race on. A test that is seeding rather than
    measuring the ceiling passes one it cannot reach.
    """
    return await store.reserve(record, ceiling=_SEEDING_CEILING)


def sample_selection(
    frameworks: Sequence[FrameworkName] = DEFAULT_FRAMEWORKS,
) -> list[FrameworkSelection]:
    """One job's framework selection, with each package's options left empty.

    ``frameworks`` is required and non-empty everywhere a job exists — on the
    submission, on the record and on the engine — with no default on any path,
    which is what stops a caller's omission meaning two different things on two
    installs. So a test that is about something else still has to state one, and
    states it here.

    STRIDE's options model is empty, so ``{}`` is a *complete* selection for it
    rather than a stub. A package with required options needs its own values
    here.
    """
    return [FrameworkSelection(name=name) for name in frameworks]


def package_whose_precondition(
    precondition: Any, name: FrameworkName = "stride"
) -> FrameworkPackage:
    """This install's package, with its precondition member replaced.

    **The one thing this repository cannot otherwise test.** STRIDE's
    precondition is total, so a gate that never runs and a gate that always
    passes look identical from every seam — which is how the member came to be
    declared, checked on the corpus side and never called in ``src/``. A package
    that can refuse is what tells the two apart, and until a second framework
    lands there is no such package to select.

    Everything else is the shipped package: the same lanes, the same rules and
    the same text on disk. So a graph built over this is the shipped topology,
    and what the precondition answers is the only difference.

    The parameter is ``Any`` because the defect cases are the point. A caller
    hands this a member that is not callable at all, or one that raises, and the
    gate has to refuse both.
    """
    return replace(PACKAGES[name], precondition=precondition)


def package_answering(result: Any, name: FrameworkName = "stride") -> FrameworkPackage:
    """The same, for a precondition that answers ``result`` about any model.

    ``result`` is ``Any`` rather than a ``PreconditionResult``: one caller hands
    it a value outside the three states, because refusing that is the run-time
    gate's own fail-closed rule.
    """
    return package_whose_precondition(lambda model: result, name)


def carrying(monkeypatch: Any, package: FrameworkPackage) -> None:
    """Register one package under its own name for the length of a test.

    Patches the registry rather than the name it is read through:
    :func:`~analysis_service.frameworks.package_for` reads the module global, and
    the graph holds no package of its own.
    """
    monkeypatch.setattr(
        framework_registry,
        "PACKAGES",
        MappingProxyType({**PACKAGES, package.name: package}),
    )


def repo_package_loaders(
    frameworks: Sequence[FrameworkName] = DEFAULT_FRAMEWORKS,
) -> dict[FrameworkName, MarkdownLoader]:
    """One loader per selected package, rooted where this repo ships its text.

    The third of :func:`~analysis_service.graph.build_pipeline`'s three roots, and
    the only one that is per framework. Built here so a test naming a selection
    does not also have to know that a package's text lives at
    ``frameworks/<name>/``.
    """
    return {
        name: MarkdownLoader(PROJECT_ROOT / "frameworks" / name) for name in frameworks
    }


DESCRIPTION_TEXT = (
    "Customers log in to the web app; customers log in from the browser."
    " The web app runs on Cloud Run, and orders are stored in Postgres."
)
"""The submitted text every scripted job carries.

Every ``source_excerpt`` in :func:`valid_model` and the quote ground in
:func:`sample_threat` are verbatim spans of *this string*, because the gate now
checks an excerpt against the source it cites the same way the fan-in checks a
threat's quote. A fixture citing words its own job never carried is the exact
defect that check exists to catch, and one used by 29 tests would have failed
all of them.
"""


def valid_model(source_label: str = DEFAULT_DESCRIPTION_LABEL) -> SystemModel:
    """The shared gate-passing model.

    ``source_label`` is a parameter because the citation rule checks it against
    the *job's* labels: a model whose excerpts cite a source the job did not
    carry is invalid, so a test submitting its own labels must say so here too.
    Its excerpts are spans of :data:`DESCRIPTION_TEXT`, so it also passes the
    rule that the excerpt is really *in* that source.
    """
    return SystemModel(
        external_entities=[
            ExternalEntity(
                id="entity:customer",
                name="Customer",
                kind="human",
                trust_zone="boundary:internet",
                source_excerpt="customers log in from the browser",
                source_label=source_label,
                assets=["pii"],
            )
        ],
        processes=[
            Process(
                id="process:web-app",
                name="Web App",
                technology="Python/FastAPI on Cloud Run",
                trust_zone="boundary:internal-network",
                exposure="internet-facing",
                interface_kind="web",
                source_excerpt="the web app runs on Cloud Run",
                source_label=source_label,
            )
        ],
        data_stores=[
            DataStore(
                id="store:orders-db",
                name="Orders DB",
                technology="Cloud SQL Postgres",
                trust_zone="boundary:internal-network",
                data_classification="confidential",
                encryption_at_rest="unknown",
                source_excerpt="orders are stored in Postgres",
                source_label=source_label,
                assets=["business-critical-data", "pii"],
            )
        ],
        data_flows=[
            DataFlow(
                id="flow:customer-to-web-app:login",
                name="Login",
                source="entity:customer",
                destination="process:web-app",
                protocol="HTTPS",
                authentication="session cookie",
                data_description="credentials in, session out",
                encryption_in_transit="TLS 1.3",
                source_excerpt="customers log in from the browser",
                source_label=source_label,
            ),
            DataFlow(
                id="flow:web-app-to-orders-db:store-order",
                name="Store Order",
                source="process:web-app",
                destination="store:orders-db",
                protocol="Postgres wire protocol",
                authentication="IAM database auth",
                data_description="order rows",
                encryption_in_transit="unknown",
                source_excerpt="orders are stored in Postgres",
                source_label=source_label,
            ),
        ],
        trust_boundaries=[
            TrustBoundary(
                id="boundary:internet",
                name="Internet",
                kind="network",
                source_excerpt="customers log in from the browser",
                source_label=source_label,
            ),
            TrustBoundary(
                id="boundary:internal-network",
                name="Internal Network",
                kind="network",
                source_excerpt="the web app runs on Cloud Run",
                source_label=source_label,
            ),
        ],
        assumptions=[
            Assumption(
                assumption="web app is internet-facing",
                element_id="process:web-app",
                attribute="exposure",
                basis="customers reach it directly from the browser",
            )
        ],
    )


def sample_draft(
    threat_id: str = "S-01",
    category: str = "spoofing",
    **overrides: Any,
) -> DraftThreat:
    """One lane agent's draft against valid_model(), before the critic rules."""
    threat = sample_threat(threat_id, category, **overrides)
    return DraftThreat.model_validate(
        threat.model_dump(exclude={"confidence", "verdict"})
    )


def sample_proposal(
    threat_id: str = "S-01",
    category: str = "spoofing",
    **overrides: Any,
) -> ThreatProposal:
    """What a lane agent emits for sample_draft(), before resolution.

    The same two facts the draft is grounded on, *named* rather than
    serialized: the quote as a candidate, the crossing as a catalog ID. So
    resolving this against ``valid_model()``'s catalog, in the lane the second
    argument names, reproduces ``sample_draft()`` exactly — the property the
    two fixtures exist together to let a test assert.

    ``framework`` and ``framework_version`` are excluded alongside ``id``: all
    three are the *service's* to stamp, composed by
    :func:`~analysis_service.evidence.resolve_proposals` from the package it is
    resolving for, so a proposal carrying them would be an agent asserting its
    own provenance.
    """
    threat = sample_threat(threat_id, category)
    fields: dict[str, Any] = threat.model_dump(
        mode="json",
        exclude={
            "confidence",
            "verdict",
            "grounds",
            "id",
            "category",
            "framework",
            "framework_version",
        },
    )
    # Called with the threat ID the resolved draft will carry, because that is
    # what a test is talking about — the proposal itself names only the number,
    # and the lane supplies the letter.
    fields["sequence"] = int(threat_id.split("-")[1])
    fields["quotes"] = [
        {
            "text": "Customers log in to the web app",
            "source_label": DEFAULT_DESCRIPTION_LABEL,
        }
    ]
    fields["evidence_refs"] = ["crossing:flow:customer-to-web-app:login"]
    fields.update(overrides)
    return ThreatProposal.model_validate(fields)


def sample_ruling(threat_id: str = "S-01", **overrides: Any) -> ThreatRuling:
    """The critic's ruling on one sample_draft(), overridable per test.

    Carries no ``severity``, which is the common case: the agent's rating
    stands unless the calibration step replaced it.
    """
    fields: dict[str, Any] = {
        "id": threat_id,
        "confidence": "high",
        "verdict": Verdict(status="confirmed"),
    }
    fields.update(overrides)
    return ThreatRuling(**fields)


def sample_threat(
    threat_id: str = "S-01",
    category: str = "spoofing",
    **overrides: Any,
) -> Threat:
    """One valid confirmed threat against valid_model(), overridable per test."""
    fields: dict[str, Any] = {
        "id": threat_id,
        "framework": "stride",
        "framework_version": STRIDE_VERSION,
        "category": category,
        "title": "Session cookie theft enables account takeover",
        "description": "Stolen session cookies let an attacker impersonate"
        " the customer against the web app.",
        "affected_element_ids": ["flow:customer-to-web-app:login"],
        # The default draft's action, matching its title: a stolen session used
        # against the web app. Tests that care about identity override it.
        "verb": "use-credential",
        # One quote and one derived fact, so a default draft exercises two
        # branches rather than the cheapest one. The quote is a verbatim span of
        # the source ``sample_report`` submits, so it verifies through the
        # ladder; tests that need a failing quote override this field.
        "grounds": [
            Ground(
                kind="quote",
                text="Customers log in to the web app",
                source_label=DEFAULT_DESCRIPTION_LABEL,
            ),
            Ground(kind="derived-fact", flow_id="flow:customer-to-web-app:login"),
        ],
        "severity": Severity(
            likelihood="medium",
            impact="high",
            justification="Commodity attack against exposed sessions.",
        ),
        "confidence": "high",
        "mitigations": [Mitigation(summary="Set HttpOnly and Secure on cookies")],
        "verdict": Verdict(status="confirmed"),
    }
    fields.update(overrides)
    return Threat(**fields)


def sample_analysis(
    threats: list[Threat] | None = None,
    rejected_threats: list[Threat] | None = None,
    **marks: Any,
) -> StrideAnalysis:
    """One STRIDE analysis block over valid_model(), summary already computed.

    ``marks`` passes this block's own mark lists straight through, so a test
    about mark placement states only the mark it is about. The summary is not
    among them: the block recounts its own contents, so a fixture that let a
    caller pass one would let a test assert against a number the schema is about
    to reject.
    """
    if threats is None:
        threats = [sample_threat()]
    if rejected_threats is None:
        rejected_threats = []
    return StrideAnalysis(
        framework="stride",
        framework_version=STRIDE_VERSION,
        disclaimer=STRIDE_DISCLAIMER,
        claims=threats,
        rejected_claims=rejected_threats,
        summary=StrideAnalysis.summarize(threats, rejected_threats),
        **marks,
    )


def sample_report(
    threats: list[Threat] | None = None,
    rejected_threats: list[Threat] | None = None,
    *,
    analyses: list[Any] | None = None,
    **marks: Any,
) -> Report:
    """A complete, internally consistent report over valid_model().

    One STRIDE block by default, built from ``threats`` and ``rejected_threats``.
    ``analyses`` replaces that block outright, for the tests that are about the
    envelope rather than about STRIDE — a second framework's block, or two, or
    none. The job's own ``frameworks`` list follows whatever the blocks are,
    since the envelope checks the two agree and a fixture that let them drift
    would fail every test for one reason.

    ``marks`` are routed by **which model declares the field**: the envelope
    carries exactly one mark list (``shared_element_names``, which annotates the
    shared model) and every other mark belongs to a block. So a test still names
    a mark once, and the schema decides where it lands. Passing block marks
    alongside an explicit ``analyses`` is refused rather than silently dropped.
    """
    model = valid_model()
    block_marks = {key: value for key, value in marks.items() if key not in _ENVELOPE}
    envelope_marks = {key: value for key, value in marks.items() if key in _ENVELOPE}
    if analyses is None:
        analyses = [sample_analysis(threats, rejected_threats, **block_marks)]
    elif block_marks or threats is not None or rejected_threats is not None:
        raise TypeError(
            "sample_report(analyses=...) builds the blocks itself;"
            f" pass {sorted({*block_marks, 'threats', 'rejected_threats'})} to"
            " sample_analysis() instead"
        )
    created = datetime(2026, 7, 18, 14, 3, 21, tzinfo=UTC)
    return Report(
        job=Job(
            id="job-8f3a2c91",
            created_at=created,
            completed_at=datetime(2026, 7, 18, 14, 4, 9, tzinfo=UTC),
            frameworks=[FrameworkSelection(name=block.framework) for block in analyses],
        ),
        input=InputRef.of(
            system_name="Order Service",
            sources=[Source.description(DESCRIPTION_TEXT)],
        ),
        nodes=[
            NodeRun(node="extract", model="gemini-2.5-flash", duration_ms=3200),
            NodeRun(node="critic_stride", model="gemini-2.5-pro", duration_ms=14500),
            NodeRun(node="assemble", duration_ms=20),
        ],
        system_model=model,
        boundary_crossings=model.boundary_crossings(),
        elements_analyzed=len(model.elements()),
        analyses=analyses,
        **envelope_marks,
    )


#: The mark lists the *envelope* declares. Everything else a caller names is a
#: block's, which is what :func:`sample_report` routes on. Derived from the
#: schema rather than listed, so a mark that moves between the two moves here
#: with it.
_ENVELOPE = frozenset(Report.model_fields)

#: What ``frameworks/stride/disclaimer.md`` says, read once. The block's
#: ``disclaimer`` is the package's own text, so a fixture spelling its own would
#: be asserting against a sentence the service never serves.
STRIDE_DISCLAIMER = (
    MarkdownLoader(PROJECT_ROOT / "frameworks" / "stride").load("disclaimer").strip()
)

EMPTY_CLAIMS = '{"claims": []}'


def claims_json(*claims: BaseModel) -> str:
    """A lane-agent or critic node's emission: the list inside its ``claims`` key.

    The wrapper is the node's output-schema shape, not the domain's, so tests
    build it here rather than each spelling it out — a bare array is what these
    nodes emitted before the schema had to be convertible, and is now invalid.

    The key is ``claims`` rather than ``threats`` because one shared
    ``prompts/analyze.md`` serves every registered framework's lane agents; see
    :class:`~analysis_service.report.ProposalBatch`.
    """
    return json.dumps({"claims": [claim.model_dump(mode="json") for claim in claims]})


def served_build(requested: str) -> str:
    """The build a scripted model claims answered: the request plus a suffix."""
    return f"{requested}-served"


def scripted_usage() -> types.GenerateContentResponseUsageMetadata:
    """The usage block a scripted model reports, in the provider's own spelling.

    Five distinct values, none of them a sum of the others, so a test can tell
    which neutral field each provider counter landed in — a block of equal
    numbers would pass a mapping that transposed two of them. ``thoughts`` sits
    outside ``candidates`` here, which is the Gemini-family reading; the
    recorded fields are deliberately not cross-checked, so the OpenAI-family
    reading is equally recordable.
    """
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=1100,
        cached_content_token_count=700,
        candidates_token_count=300,
        thoughts_token_count=9000,
        total_token_count=10400,
    )


class ScriptedLlm(BaseLlm):
    """One node's model: replays a fixed emission and records the request.

    It reports a ``model_version`` distinct from the configured ``model``, the
    way a real provider does — the pinned string names a family, the served
    build names what answered. Keeping them different is what lets a test tell
    "recorded what was asked for" apart from "recorded what ran", and it is
    what makes the generation-identity path reachable at all without a live
    provider.

    It reports ``usage_metadata`` for the same reason: a stand-in that
    answered for free would leave the token-accounting path unreachable
    offline, and the one number the accounting exists to surface — reasoning
    tokens — is the one no visible output would reveal.
    """

    reply: str
    seen: list[str] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            model_version=served_build(self.model),
            usage_metadata=scripted_usage(),
        )


class SlowLlm(ScriptedLlm):
    """A stand-in that takes measurable time to answer.

    Every other stand-in replies instantly, which makes a whole class of timing
    defect invisible: a model's latency can be charged to the wrong node and
    every offline duration still reads as ~0 ms either way. This one waits long
    enough to land on one side of a millisecond-resolution assertion.
    """

    delay_s: float = 0.05

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        await asyncio.sleep(self.delay_s)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


class SilentLlm(ScriptedLlm):
    """A stand-in whose response carries no ``model_version``.

    Stands for a provider that reported no served build. Such a node must end
    up with no fingerprint rather than one keyed on what was merely requested.

    It still reports usage, which is the point of keeping the two apart: a
    token count is a fact about the call whether or not the provider also named
    the build that served it, so the missing build must cost the fingerprint
    and nothing else.
    """

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            usage_metadata=scripted_usage(),
        )


class UnmeteredLlm(ScriptedLlm):
    """A stand-in whose response carries no usage block at all.

    The other half of :class:`SilentLlm`: a provider that named its build and
    metered nothing. Such a node must carry no ``usage`` rather than a zeroed
    one, so a roll-up cannot read an unmeasured call as a free call.
    """

    async def generate_content_async(
        self, llm_request, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.config.system_instruction or "")
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.reply)]),
            model_version=served_build(self.model),
        )


#: The tier keys the *base* tier serves. Spelled as tier node names rather than
#: graph node names because that is what the resolver is handed, and because the
#: two are no longer the same thing: six lane agents share one ``analyze/<F>``.
_BASE_TIER_NODES = frozenset({"extract", "repair"})


def scripted_pipeline(
    replies: dict[str, str],
    *,
    llm_class: type[ScriptedLlm] = ScriptedLlm,
    entry: Entry = ENTRY_EXTRACT,
    sampling: SamplingConfig | None = None,
    frameworks: Sequence[FrameworkName] = DEFAULT_FRAMEWORKS,
) -> tuple[Pipeline, dict[str, ScriptedLlm]]:
    """The real graph, with every LLM node bound to its own scripted stand-in.

    Repo package text, repo prompts, repo tier and sampling config — only the
    model's text is faked, so the topology, the per-tier binding and the
    provenance stamping under test are the shipped ones. The tier adapters are
    short-circuited by passing ``resolve_model`` directly: building them would
    run the credential check, which an offline test has no credentials to pass.

    ``replies`` and the returned map are both keyed by **graph node name** —
    ``analyze_stride_spoofing``, ``critic_stride`` — which is what a test talks
    about. A node with no scripted reply emits an empty claim list.

    ``frameworks`` is the selection the graph is built for, since a graph is now
    built for one (:func:`~analysis_service.graph.build_pipeline`). The returned
    map covers whatever LLM nodes that selection produced.

    ``sampling`` substitutes another *legal* deployment's decoding params for
    the shipped ones. The shipped file leaves several offered params unset, so
    without this the only values any test ever stamps are the ones this
    repository happens to ship — and a param no test sets is a param whose
    journey to the report nothing checks.

    **The stand-ins are named by walking the built graph, not by inverting the
    tier map.** That map stopped being invertible when the lanes moved onto one
    ``analyze/<framework>`` key: six nodes now resolve on it, so a tier name no
    longer identifies a node. The resolver therefore mints a fresh stand-in per
    call — one per node, where production shares one adapter per tier — and the
    walk afterwards is what learns which node got which. Per-node rather than
    per-tier is also what keeps ``seen`` a record of one node's prompts.
    """
    minted: list[ScriptedLlm] = []

    def resolve(tier_node: str) -> BaseLlm:
        model = llm_class(
            model=BASE_MODEL if tier_node in _BASE_TIER_NODES else STRONG_MODEL,
            reply=EMPTY_CLAIMS,
            seen=[],
        )
        minted.append(model)
        return model

    tiers = repo_tiers()
    sampling = sampling or load_sampling(
        PROJECT_ROOT / "config" / "sampling.toml", env={}
    )
    pipeline = build_pipeline(
        prompt_loader=MarkdownLoader(PROJECT_ROOT / "prompts"),
        domain_loader=MarkdownLoader(PROJECT_ROOT / "domains"),
        package_loaders=repo_package_loaders(frameworks),
        binding=NodeBinding.from_configs(tiers, sampling, resolve),
        frameworks=frameworks,
        entry=entry,
    )

    graph = pipeline.workflow.graph
    models = {
        node.name: node.model
        for node in (graph.nodes if graph else ())
        if isinstance(node, LlmAgent) and isinstance(node.model, ScriptedLlm)
    }
    if len(models) != len(minted):
        raise AssertionError(
            f"scripted {len(minted)} stand-ins but found {len(models)} on the"
            " built graph: a node was bound to something else"
        )
    unknown = sorted(set(replies) - set(models))
    if unknown:
        raise AssertionError(
            f"scripted replies for {unknown}, which the graph built for"
            f" {list(frameworks)} has no LLM node called. It has {sorted(models)}"
        )
    for name, model in models.items():
        model.reply = replies.get(name, EMPTY_CLAIMS)
    return pipeline, models


@dataclass(frozen=True)
class ScriptedFramework:
    """One package's scripted answers for a run of the real graph.

    ``lane`` names the one lane agent that drafts; ``proposal`` is that agent's
    whole emission and ``ruling`` is the critic's, both in the package's own
    node schema. ``claim_id`` is the ID the graph composes for that draft, so a
    test can find it on the finished block. ``options`` is a complete selection
    for the package.
    """

    lane: str
    options: Mapping[str, Any]
    claim_id: str
    proposal: str
    ruling: str


#: One scripted fixture per package, keyed by framework and checked against
#: :data:`~analysis_service.frameworks.PACKAGES` in ``test_framework_neutrality``.
#: A test that runs the real graph over every pair reads this rather than
#: naming a package, so a new package joins the run by adding an entry.
SCRIPTED_FRAMEWORKS: Mapping[FrameworkName, ScriptedFramework] = MappingProxyType(
    {
        "stride": ScriptedFramework(
            lane="spoofing",
            options={},
            claim_id="S-01",
            proposal=claims_json(sample_proposal("S-01", "spoofing")),
            ruling=claims_json(sample_ruling("S-01")),
        ),
        "asvs": ScriptedFramework(
            lane="authentication",
            options={"level": 1},
            claim_id="v5.0.0-6.2.1",
            proposal=claims_json(
                RequirementProposal(
                    requirement="2.1",
                    title="No password length policy is stated",
                    description="The requirement applies and the input does not settle it.",
                    # The agent ruled, which is the ordinary answer and the one
                    # that keeps this scripted claim a claim rather than a
                    # scope entry.
                    needs_evidence="",
                    evidence_refs=["crossing:flow:customer-to-web-app:login"],
                )
            ),
            ruling=claims_json(
                RequirementRulingProposal.model_validate(
                    {"id": "v5.0.0-6.2.1", "verdict": {"status": "confirmed"}}
                )
            ),
        ),
    }
)
