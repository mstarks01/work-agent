"""Every value a guide copies out of code or config, held to its source.

The guides print shipped values, variable names, routes and limits that the
code defines. Nothing else fails when the code changes and the guide does not,
so each check here reads the copy from the guide and compares it with the value
the code's own reader returns. The code is the source of truth.

Each check reads the source through the code: it imports the constant, or it
loads the shipped file through its loader. It never parses a config file with a
second rule. Each check reads the guide narrowly, through one table, one
sentence or one fenced block, and asserts that the scan found something. A
reworded guide then fails the check, and does not pass with nothing read.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from analysis_service import resilience
from analysis_service.answer_round import EARLY_RULES
from analysis_service.auth import (
    ALLOWED_ALGORITHMS,
    DEFAULT_ALGORITHMS,
    AuthConfigError,
    build_verifier,
)
from analysis_service.deployment import ConfigPaths
from analysis_service.links import MAX_LINK_ANSWERS
from analysis_service.model_tiers import LLM_NODES, SUPPORTED_VERSION, TIER_NAMES
from analysis_service.resilience import ResilienceConfig, load_resilience
from analysis_service.sampling import (
    OFFERED_PARAMS,
    TierSampling,
    env_var_for,
    load_sampling,
)
from analysis_service.sources import MAX_LABEL_CHARS, MAX_SYSTEM_NAME_CHARS
from analysis_service.validation import MAX_ELEMENTS
from analysis_service.vendors import VENDORS, CredentialMode
from tests.test_api import make_client

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
CONFIGURATION = DOCS / "Configuration.md"
HTTP_API = DOCS / "HTTP-API.md"

#: The guides a reader copies a TOML example from. ``docs/adr/``,
#: ``docs/research/`` and ``docs/history/`` record a past state, so an old
#: version there is correct.
GUIDES = (REPO_ROOT / "README.md", *sorted(DOCS.glob("*.md")))

SHIPPED_PATHS = ConfigPaths.from_env({})
SHIPPED_RESILIENCE = load_resilience(SHIPPED_PATHS.resilience, env={})
SHIPPED_SAMPLING = load_sampling(SHIPPED_PATHS.sampling, env={})

_TOML_FENCE = re.compile(r"^```toml\n(.*?)^```", re.DOTALL | re.MULTILINE)
_ASSIGNMENT = re.compile(r"`([a-z_]+ = [^`\n]+)`")
_NUMBER_WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "ten": 10}


def section(path: Path, heading: str) -> str:
    """The text under one heading, up to the next heading of its level or above."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.match(rf"#{{1,{level}}} ", lines[index])
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def flat(text: str) -> str:
    """The text with each run of whitespace made one space, so a wrap reads as a space."""
    return " ".join(text.split())


def tables(text: str, *header: str) -> list[list[list[str]]]:
    """The body rows of every Markdown table in the text whose header row is
    ``header``, each row as a list of stripped cells."""
    found = []
    rows: list[list[str]] = []
    for line in [*text.splitlines(), ""]:
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
            continue
        if rows and tuple(rows[0]) == header:
            found.append(rows[2:])
        rows = []
    return found


def table(text: str, *header: str) -> list[list[str]]:
    """The body rows of the one table in the text whose header row is ``header``."""
    (rows,) = tables(text, *header)
    assert rows, f"the table headed {header} has no rows"
    return rows


def backticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def pinned_values(cell: str) -> dict[str, str]:
    """Each tier's value in a pinned cell, such as
    ``pinned `16384` base / `64000` strong and review``. A segment that names
    no tier states the value for every tier."""
    stated = {}
    for segment in cell.removeprefix("pinned ").split(" / "):
        value, _, words = segment.partition("` ")
        named = tuple(tier for tier in TIER_NAMES if re.search(rf"\b{tier}\b", words))
        stated.update(dict.fromkeys(named or TIER_NAMES, value.strip("`")))
    return stated


# Resilience: config/resilience.toml as analysis_service.resilience loads it.


def test_the_resilience_section_states_every_shipped_value():
    """Each ``key = value`` span the section prints is the loaded value, and
    the section prints every scalar key the file carries."""
    shown = {}
    for span in _ASSIGNMENT.findall(section(CONFIGURATION, "### Resilience")):
        shown.update(
            (key, value)
            for key, value in tomllib.loads(span).items()
            if key in ResilienceConfig.model_fields
        )

    assert set(shown) == set(ResilienceConfig.model_fields) - {"bounds_by_upstream"}
    for key, value in shown.items():
        assert value == getattr(SHIPPED_RESILIENCE, key), key


def test_the_resilience_section_states_the_shipped_upstream_row():
    sentence = re.search(
        r"whose one row gives `([^`]+)` a `timeout_ms` and a `job_deadline_ms` of"
        r" `(\d+)` each",
        flat(section(CONFIGURATION, "### Resilience")),
    )

    assert sentence, "the sentence that states the shipped upstream row is gone"
    bound = int(sentence[2])
    assert {
        slug: (row.timeout_ms, row.job_deadline_ms)
        for slug, row in SHIPPED_RESILIENCE.bounds_by_upstream.items()
    } == {sentence[1]: (bound, bound)}


def test_the_per_window_table_names_resilience_keys():
    text = section(CONFIGURATION, "### Resilience")
    knobs = {backticked(row[0])[0] for row in table(text, "Knob", "What it bounds")}

    assert knobs <= set(ResilienceConfig.model_fields)


def test_the_deadline_arithmetic_uses_the_shipped_values():
    """The argument for a job deadline multiplies three shipped values, so
    each factor and the product are held to the file."""
    text = flat(section(CONFIGURATION, "### Resilience"))
    timeout = re.search(r"`timeout_ms` bounds one request at (\d+) s", text)
    attempts = re.search(r"`attempts` allows (\d+) of them per node", text)
    product = re.search(
        r"the graph runs (\w+) LLM stages in series on its longest path — (\d+)"
        r" minutes",
        text,
    )
    deadline = re.search(r"(\d+) s is a \*\*backstop", text)
    rate = re.search(r"`max_jobs_per_window = \d+` is (\w+) times the ceiling", text)

    assert timeout and attempts and product and deadline and rate
    shipped = SHIPPED_RESILIENCE
    assert int(timeout[1]) * 1000 == shipped.timeout_ms
    assert int(attempts[1]) == shipped.attempts
    stages = _NUMBER_WORDS[product[1]]
    assert int(product[2]) * 60_000 == shipped.timeout_ms * shipped.attempts * stages
    assert int(deadline[1]) * 1000 == shipped.job_deadline_ms
    assert (
        _NUMBER_WORDS[rate[1]] * shipped.max_active_jobs == shipped.max_jobs_per_window
    )


#: What each row of the Input limits table holds, read from its source.
INPUT_LIMITS = {
    "max_source_bytes": SHIPPED_RESILIENCE.max_source_bytes,
    "max_sources": SHIPPED_RESILIENCE.max_sources,
    "job_deadline_ms": SHIPPED_RESILIENCE.job_deadline_ms,
    "max_active_jobs": SHIPPED_RESILIENCE.max_active_jobs,
    "label": MAX_LABEL_CHARS,
    "MAX_SYSTEM_NAME_CHARS": MAX_SYSTEM_NAME_CHARS,
    "MAX_ELEMENTS": MAX_ELEMENTS,
}

#: A value cell's unit, as the factor that turns it into the source's unit.
_UNITS = {"KiB": 1024, "s": 1000, "": 1}


def test_the_input_limits_table_is_the_shipped_limits():
    """``docs/HTTP-API.md`` and ``docs/Integration-Guide.md`` point here for
    the shipped limits, so this table is the one copy."""
    documented = {}
    text = section(CONFIGURATION, "## Input limits")
    for name, value, _ in table(text, "Limit", "Value", "Where"):
        number = re.match(r"(\d+)(?: (KiB|s)\b)?", value)
        assert number, f"the Input limits value {value!r} starts with no number"
        documented[backticked(name)[0]] = int(number[1]) * _UNITS[number[2] or ""]

    assert documented == INPUT_LIMITS


def test_the_resilience_override_table_is_every_variable_the_loader_reads():
    """Both directions: each ``*_VAR`` the module defines is listed, and each
    listed variable changes what the loader returns."""
    text = section(CONFIGURATION, "### Resilience overrides")
    documented = {backticked(row[0])[0] for row in table(text, "Variable", "Effect")}
    defined = {
        getattr(resilience, name) for name in dir(resilience) if name.endswith("_VAR")
    }

    assert documented == defined
    for var in documented:
        # Every shipped value is above 1, and 1 is legal for every knob.
        overridden = load_resilience(SHIPPED_PATHS.resilience, env={var: "1"})
        assert overridden != SHIPPED_RESILIENCE, f"{var} changes nothing"


# Sampling: config/sampling.toml as analysis_service.sampling loads it.


def test_the_sampling_table_is_the_param_surface_and_the_shipped_values():
    """The table lists every param a tier admits. An **unset** row is unset on
    every tier, and a pinned row states each tier's value."""
    text = section(CONFIGURATION, "### Sampling")
    shown = {
        param: state
        for names, state, _ in table(text, "Param", "Shipped state", "Notes")
        for param in backticked(names)
    }

    assert set(shown) == set(TierSampling.model_fields)
    for param, state in shown.items():
        loaded = {
            tier: getattr(SHIPPED_SAMPLING.for_tier(tier), param) for tier in TIER_NAMES
        }
        if state == "**unset**":
            assert set(loaded.values()) == {None}, param
            continue
        assert state.startswith("pinned "), f"{param}: unread state {state!r}"
        assert pinned_values(state) == {
            tier: str(value).lower() for tier, value in loaded.items()
        }, param


def test_the_sampling_section_states_the_shipped_version():
    span = re.search(r"`version = (\d+)`", section(CONFIGURATION, "### Sampling"))

    assert span, "the Sampling section states no version"
    assert int(span[1]) == SHIPPED_SAMPLING.version


def test_the_sampling_override_table_is_the_offered_params():
    text = section(CONFIGURATION, "### Sampling overrides")
    documented = {
        backticked(row[0])[0].removeprefix("ANALYSIS_SAMPLING_{TIER}_").lower()
        for row in table(text, "Variable", "Effect")
    }
    tiers = re.search(r"`\{TIER\}` is ([^.]+)\.", flat(text))

    assert documented == set(OFFERED_PARAMS)
    assert tiers, "the sentence that names the tiers is gone"
    assert backticked(tiers[1]) == [tier.upper() for tier in TIER_NAMES]
    assert env_var_for("base", "seed") == "ANALYSIS_SAMPLING_BASE_SEED"


# Vendors: the credential modes and variables in analysis_service.vendors.


def test_the_credential_table_is_the_vendor_registry():
    text = section(CONFIGURATION, "### Models and vendors")
    rows = table(text, "Vendor", "Credential mode", "Required environment")
    documented = {
        (backticked(vendor)[0], backticked(mode)[0], tuple(backticked(variables)))
        for vendor, mode, variables in rows
    }
    registered = {
        (name, mode.value, vendor.required_env_vars(mode))
        for name, vendor in VENDORS.items()
        for mode in vendor.credentials
    }

    assert documented == registered
    assert {mode for _, mode, _ in documented} == {
        mode.value for mode in CredentialMode
    }


# Config paths: ConfigPaths.from_env.


def test_the_config_path_table_is_every_config_path():
    """Each listed variable moves exactly one path, the one whose default the
    row names, and every path has a row."""
    text = section(
        CONFIGURATION, "### Config paths (override where files are read from)"
    )
    moved = {}
    for variable, default in table(text, "Variable", "Overrides"):
        var, shown = backticked(variable)[0], backticked(default)[0]
        target = Path("/elsewhere") / var
        paths = ConfigPaths.from_env({var: str(target)})
        fields = [name for name, path in vars(paths).items() if path == target]
        assert len(fields) == 1, f"{var} moves {fields}"
        default_path = getattr(SHIPPED_PATHS, fields[0]).as_posix()
        assert default_path.endswith(shown.rstrip("/")), (
            f"{var} moves {fields[0]}, whose default is not {shown}"
        )
        moved[fields[0]] = var

    assert set(moved) == set(vars(SHIPPED_PATHS))


# Auth: analysis_service.auth.


_OIDC_REQUIRED = {
    "ANALYSIS_AUTH_PROVIDER": "oidc",
    "ANALYSIS_OIDC_ISSUER": "https://issuer.example",
    "ANALYSIS_OIDC_AUDIENCE": "api",
    "ANALYSIS_OIDC_JWKS_URL": "https://issuer.example/jwks",
}


def test_the_oidc_table_is_the_settings_the_verifier_reads():
    """The required variables are the ones a bare ``oidc`` verifier names as
    missing. The optional one is read: an algorithm outside the allowlist in
    it stops the build, and its stated default is the code's."""
    text = section(HTTP_API, "### How the provider is chosen")
    provider, oidc_rows = tables(text, "Variable", "Purpose")
    oidc = {backticked(row[0])[0]: row[1] for row in oidc_rows}
    optional = [var for var, purpose in oidc.items() if "*Optional.*" in purpose]

    with pytest.raises(AuthConfigError) as missing:
        build_verifier({"ANALYSIS_AUTH_PROVIDER": "oidc"})
    named = set(re.findall(r"ANALYSIS_\w+", str(missing.value)))
    assert set(oidc) - set(optional) == named
    assert [backticked(row[0])[0] for row in provider] == ["ANALYSIS_AUTH_PROVIDER"]

    (algorithms,) = optional
    build_verifier(_OIDC_REQUIRED | {algorithms: "ES256"})
    with pytest.raises(AuthConfigError):
        build_verifier(_OIDC_REQUIRED | {algorithms: "HS256"})
    default = re.findall(r"Defaults to `([^`]+)`", oidc[algorithms])
    assert tuple(default) == DEFAULT_ALGORITHMS


def test_the_oidc_allowlist_is_the_allowed_algorithms():
    """The guide writes ``RS256``, ``RS384`` and ``RS512`` as ``RS256/384/512``."""
    sentence = re.search(
        r"The accepted set is an \*\*allowlist\*\*.*?: (.*?)\.\n",
        section(HTTP_API, "#### Signing algorithms"),
        re.DOTALL,
    )

    assert sentence, "the sentence that lists the allowlist is gone"
    documented = set()
    for name in backticked(sentence[1]):
        family = re.fullmatch(r"([A-Z]+)(\d+(?:/\d+)*)", name)
        if family is None:
            documented.add(name)
            continue
        documented.update(f"{family[1]}{size}" for size in family[2].split("/"))
    assert documented == ALLOWED_ALGORITHMS


# Routes: the FastAPI app.


def test_the_route_table_is_the_apps_routes():
    """Every route the app serves is listed, and every listed route is served.
    A path parameter's name is not compared: the guide writes ``{id}``."""

    def shape(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", path)

    client, _ = make_client()
    served = {
        (method, shape(route.path))
        for route in client.app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    rows = table(section(HTTP_API, "## Routes"), "Method", "Path", "Purpose")
    documented = {
        (backticked(method)[0], shape(backticked(path)[0])) for method, path, _ in rows
    }

    assert documented == served


# Question limits: EARLY_RULES and the link answer limit.


def test_the_question_limits_are_the_early_rules():
    text = flat(HTTP_API.read_text(encoding="utf-8"))
    per_round = re.search(
        r"up to (\d+) capability questions and up to (\d+) questions about the"
        r" model's elements",
        text,
    )
    floors = re.search(
        r"A field question needs a `score` of at least (\d+), and a capability"
        r" question at least (\d+)\.",
        text,
    )
    limit = re.search(r"One pause asks at most (\d+) of each kind in all", text)
    limit_again = re.search(r"takes no place under the limit of (\d+)\.", text)
    links = re.search(r"or there are more than (\d+) entries", text)

    assert per_round and floors and limit and limit_again and links
    capability, field = EARLY_RULES["capability"], EARLY_RULES["field"]
    assert (int(per_round[1]), int(per_round[2])) == (
        capability.per_round,
        field.per_round,
    )
    assert (float(floors[1]), float(floors[2])) == (field.floor, capability.floor)
    assert int(limit[1]) == int(limit_again[1]) == capability.limit == field.limit
    assert int(links[1]) == MAX_LINK_ANSWERS


# Model tiers: the loader's version and node keys.


def test_every_tier_example_states_the_supported_version():
    shown = [
        (path.name, example["version"])
        for path in GUIDES
        for block in _TOML_FENCE.findall(path.read_text(encoding="utf-8"))
        if "tiers" in (example := tomllib.loads(block)) and "version" in example
    ]

    assert shown, "no guide shows a tier example with a version"
    for name, version in shown:
        assert version == SUPPORTED_VERSION, f"{name} shows version {version}"


def test_the_nodes_example_is_every_node_key():
    """The guide says each framework this build can spell needs its three
    keys, so the example shows exactly the keys the loader requires."""
    text = section(
        CONFIGURATION, "### Node keys, and the frameworks this deployment carries"
    )
    (block,) = _TOML_FENCE.findall(text)

    assert set(tomllib.loads(block)["nodes"]) == set(LLM_NODES)


# The readers' own teeth.


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("pinned `1`", {"base": "1", "strong": "1", "review": "1"}),
        (
            "pinned `16384` base / `64000` strong and review",
            {"base": "16384", "strong": "64000", "review": "64000"},
        ),
        ("pinned `16384` base / `32768` strong", {"base": "16384", "strong": "32768"}),
    ],
)
def test_a_pinned_cell_reads_each_tier(cell, expected):
    assert pinned_values(cell) == expected


def test_the_table_reader_reads_only_the_named_table():
    text = (
        "| A | B |\n| --- | --- |\n| `x` | 1 |\n\nprose\n\n"
        "| C | D |\n| --- | --- |\n| `y` | 2 |\n| `z` | 3 |"
    )

    assert tables(text, "C", "D") == [[["`y`", "2"], ["`z`", "3"]]]
    assert tables(text, "A", "D") == []
