"""What may cross the seam into the model translator (#502).

**LiteLLM runs in this process, with this process's authority.** It is a
partially trusted dependency by position rather than by design: it holds the
provider credentials, it reaches the network, and a compromise of it is a
compromise of the application. Running it behind a real boundary — a separate
constrained process, an egress allowlist, its own narrowly scoped credentials —
is deployment work this repository does not do, because this repository ships no
deployment packaging. ADR 0015 accepts the substrate; `docs/Architecture.md`
states the residual risk.

What *is* decidable here is the seam itself: the set of values this service
hands the translator, and where each of them comes from. These lints pin that
set, so the thing a boundary would later constrain cannot quietly widen first.

**The property being pinned is that no provider endpoint can originate from
untrusted input.** A `base_url`, `api_base` or `api_version` reaching the
adapter from a prompt, a submitted source, a corpus case or a model's own output
is the SSRF and endpoint-substitution path #502 names. Today none can, because
no code path sets one at all — the vendor registry decides the route and the
credential mode, and both are code. A test is what keeps "nobody wrote one" from
becoming "somebody wrote one and nobody noticed".

A text scan rather than an import-and-introspect, matching
`tests/test_workflow_lints.py`'s reasoning: the question is what the *source*
may express, and importing the module answers what one configuration happened to
produce.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from analysis_service.sampling import OFFERED_PARAMS, TierSampling
from analysis_service.vendors import VENDORS

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "analysis_service"

#: Kwargs that redirect a request to an address of the caller's choosing.
#: LiteLLM and the SDKs beneath it accept all of these. **None is set anywhere
#: in the package**, and that absence is the property, not a coincidence: a
#: request goes where the vendor's own client sends it.
FORBIDDEN_ENDPOINT_KWARGS = (
    "api_base",
    "base_url",
    "api_version",
    "aws_bedrock_runtime_endpoint",
)

#: Kwargs that turn TLS verification off, or point it at a trust store of the
#: caller's choosing. LiteLLM resolves verification from ``ssl_verify``, then
#: ``SSL_VERIFY``, then ``litellm.ssl_verify``, and builds the SSL context from
#: ``ssl_certificate``, ``ssl_security_level`` and ``ssl_ecdh_curve``
#: (``litellm.llms.custom_httpx.http_handler``). **None is set anywhere in
#: the package**, and that absence is the property: a provider's certificate is
#: checked against the system trust store, and no configuration can say
#: otherwise. The process-environment path the same resolver reads is not a
#: kwarg, so :func:`test_litellm_resolves_tls_verification_on` drives it
#: instead.
FORBIDDEN_TLS_KWARGS = (
    "ssl_verify",
    "ssl_certificate",
    "ssl_security_level",
    "ssl_ecdh_curve",
)

#: Kwargs that name *which* provider, rather than which address. These are set,
#: and must be — the build-time capability probe has to tell LiteLLM which
#: provider it is asking about. What is checked is the value's provenance: it
#: has to be read off the vendor registry entry, which is code, and never off
#: anything a request can influence.
REGISTRY_ONLY_KWARGS = ("custom_llm_provider",)

#: Every kwarg the whole seam may carry, endpoint-ish or not. Deploy-time
#: credential material (``vertex_project`` and ``vertex_location`` scope a
#: request; neither names a host)
#: belongs to the vendor's credential table and is checked with it.
CONFIG_FORBIDDEN_KWARGS = (
    *FORBIDDEN_ENDPOINT_KWARGS,
    *FORBIDDEN_TLS_KWARGS,
    *REGISTRY_ONLY_KWARGS,
    "vertex_credentials",
)


def _package_sources() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _assigned_keywords(source: Path) -> set[str]:
    """Every keyword argument name spelled in a call in ``source``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            names.update(kw.arg for kw in node.keywords if kw.arg)
        if isinstance(node, ast.Dict):
            names.update(
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return names


@pytest.mark.parametrize("kwarg", FORBIDDEN_ENDPOINT_KWARGS)
def test_no_module_names_a_provider_endpoint_kwarg(kwarg):
    """The service never sets an endpoint; the vendor registry decides the route.

    An endpoint the application can set is an endpoint some future caller can
    influence. The registry's `route()` builds a router prefix and a model name
    and nothing else, so a request goes where the vendor's own client sends it.
    """
    offenders = [
        source.relative_to(REPO_ROOT)
        for source in _package_sources()
        if kwarg in _assigned_keywords(source)
    ]
    assert not offenders, (
        f"{kwarg!r} is set in {offenders}. A provider endpoint must come from"
        f" the vendor registry, never from a value a request could reach:"
        f" prompts, submitted sources, corpus text and model output all flow"
        f" through this process, and an adapter that accepts an endpoint is the"
        f" SSRF and endpoint-substitution path."
    )


@pytest.mark.parametrize("kwarg", FORBIDDEN_TLS_KWARGS)
def test_no_module_names_a_tls_kwarg(kwarg):
    """Nothing in the service can weaken the check on a provider's certificate.

    Each of these reaches LiteLLM's own SSL resolution, and the two that take a
    path redirect the check rather than remove it — which is the same outcome
    when the path is one an operator did not choose. The property is that the
    package sets none of them, so a provider connection is verified against the
    system trust store and no value this service composes can say otherwise.
    """
    offenders = [
        source.relative_to(REPO_ROOT)
        for source in _package_sources()
        if kwarg in _assigned_keywords(source)
    ]
    assert not offenders, (
        f"{kwarg!r} is set in {offenders}. TLS verification is what makes the"
        f" credential in the Authorization header safe to send, so nothing in"
        f" this package may turn it off or repoint it."
    )


def test_litellm_resolves_tls_verification_on():
    """The env path, which a kwarg lint cannot see, read through its own reader.

    ``get_ssl_verify`` is what LiteLLM calls before it builds a connection, and
    it reads three sources in order: the call's kwarg, ``SSL_VERIFY`` in the
    process environment, and ``litellm.ssl_verify``. The lints above close the
    first. This drives the resolver itself, so the other two are held against
    LiteLLM's own answer rather than against a restatement of it here — and a
    release that changed the default fails here rather than on the wire.

    It also closes the one path the lints cannot reach at all. They read call
    kwargs and dict keys, so a module assignment — ``litellm.ssl_verify =
    False`` somewhere in the package — would pass them. Importing
    ``analysis_service`` is what runs that assignment, and this test runs after
    the import.

    **Every falsy answer is a failure, not only ``False``.** ``SSL_VERIFY=""``
    resolves to ``""``, which is neither a trust store nor a decision anybody
    made. A non-empty path is a pass: ``SSL_CERT_FILE`` names a store to check
    against, which is verification rather than the absence of it.
    """
    from litellm.llms.custom_httpx.http_handler import get_ssl_verify

    resolved = get_ssl_verify()

    assert resolved, (
        f"LiteLLM resolves TLS verification to {resolved!r}, so it would build"
        " provider connections without checking a certificate. Either"
        " SSL_VERIFY is set in this environment, or the library's default"
        " moved."
    )


@pytest.mark.parametrize("kwarg", FORBIDDEN_TLS_KWARGS)
def test_every_forbidden_tls_kwarg_is_one_litellm_reads(kwarg):
    """The table above, checked against the library it describes.

    A lint over names nobody uses any more passes forever and guards nothing.
    Each of these is a module attribute LiteLLM defines and reads when it builds
    a connection, so a release that renames one fails here — which is where the
    list gets corrected — rather than in the lint, which would go on agreeing
    with itself.

    ``litellm.__getattr__`` raises for a name it does not define, so this is a
    real question rather than a default.
    """
    import litellm

    assert hasattr(litellm, kwarg), (
        f"LiteLLM no longer defines {kwarg!r}. Re-read its SSL resolution and"
        " correct FORBIDDEN_TLS_KWARGS: a lint over a name the library dropped"
        " protects nothing."
    )


@pytest.mark.parametrize("kwarg", CONFIG_FORBIDDEN_KWARGS)
def test_no_forbidden_kwarg_is_configurable(kwarg):
    """Not settable from `sampling.toml`, an env override, or a submission.

    Two closed sets, checked together because a value only has to be
    expressible in one of them to reach the adapter: the sampling model forbids
    unknown fields, and the env override surface is an explicit allowlist. The
    third source, the vendor's credential table, is a per-vendor constant in
    code and is checked below.
    """
    assert kwarg not in TierSampling.model_fields
    assert kwarg not in OFFERED_PARAMS


def test_the_sampling_surface_forbids_what_it_does_not_name():
    """The mechanism behind the check above, asserted rather than assumed."""
    assert TierSampling.model_config["extra"] == "forbid"
    with pytest.raises(ValueError):
        TierSampling(api_base="https://attacker.example")


def test_the_override_surface_is_no_wider_than_the_sampling_surface():
    """An env var cannot introduce a param the file could not hold."""
    assert set(OFFERED_PARAMS) <= set(TierSampling.model_fields)


def test_every_adapter_kwarg_comes_from_a_closed_set():
    """What `build_tier_adapters` hands the **translator**, and where each part
    is from.

    The translator is the object on the provider side of
    `analysis_service.provider`'s seam, and it is what holds the tier's
    configuration — so this is still the one constructor a deployment's values
    reach LiteLLM through.

    Six sources, all of them code or deploy-time config: the route from the
    vendor registry, one literal, the per-request timeout from
    `config/resilience.toml`, the tier's sampling constructor kwargs, the
    vendor's credential kwargs, and the client, which is one of two classes
    this package defines, chosen by a registry fact and carrying the
    arrangement this deployment declared. A further spread would be the thing to look at, which is why
    the count is asserted rather than the names alone.
    """
    source = (PACKAGE / "binding.py").read_text(encoding="utf-8")
    call = re.search(r"translator = translating\((.*?)\n        \)", source, re.DOTALL)
    assert call is not None, "build_tier_adapters no longer builds adapters this way"
    body = call.group(1)
    assert "model=selection.route" in body
    assert "**tier_sampling.constructor_kwargs()" in body
    assert "**vendor.credential_kwargs(env, tiers.credential_mode(" in body
    assert "**{_TIMEOUT_KWARG: resilience.request_timeout_seconds()}" in body
    assert "llm_client=(" in body
    assert "capturing_client(vendor, tiers.charge_mode(" in body
    assert "if vendor.reports_charge or not vendor.routes_to_one_provider" in body
    assert body.count("**") == 4, (
        "a new spread reaches the translator constructor. Every value crossing"
        " this seam has to come from the vendor registry or from deploy-time"
        " config, never from anything a request can influence."
    )


@pytest.mark.parametrize("kwarg", REGISTRY_ONLY_KWARGS)
def test_a_provider_naming_kwarg_takes_its_value_from_the_registry(kwarg):
    """`custom_llm_provider` is set, and only ever off a `Vendor`.

    The build-time capability probe has to tell LiteLLM which provider it is
    asking about, so the name is legitimately used. What must not happen is the
    value coming from anywhere but the registry — a provider string a request
    could influence routes the request somewhere the deployment did not choose,
    which is the same defect as an attacker-supplied `api_base` wearing a
    different name.
    """
    literals = []
    for source in _package_sources():
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != kwarg:
                    continue
                value = keyword.value
                registry_sourced = (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "vendor"
                )
                if not registry_sourced:
                    literals.append(f"{source.relative_to(REPO_ROOT)}:{value.lineno}")
    assert not literals, (
        f"{kwarg!r} is set from something other than the vendor registry at"
        f" {literals}. Its value decides which provider a request reaches, so it"
        f" must be read off a `Vendor`, never off config, a prompt, a submitted"
        f" source or a model's own output."
    )


def test_the_credential_table_carries_no_address():
    """A vendor's credential kwargs authenticate and address; none redirects.

    `vertex_project` and `vertex_location` say which Google project and region
    a request is scoped to. Neither names a host, so neither is an endpoint —
    which is why they are excluded from the endpoint list and asserted here
    instead. A table that grew an endpoint entry would move the decision about
    where a request goes into deploy-time config, and the point of the registry
    is that it is code.

    Driven over every mode each vendor allows rather than over the modes a
    deployment selects, so a mode nobody has selected still answers.
    """
    for vendor in VENDORS.values():
        for mode, source in vendor.credentials.items():
            kwargs = {entry.kwarg for entry in source.env}
            assert not kwargs & set(FORBIDDEN_ENDPOINT_KWARGS), (vendor.name, mode)
