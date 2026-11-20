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

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Literal, get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from analysis_service import deployment, resilience
from analysis_service.analysis import CONTROL_ATTRIBUTES
from analysis_service.answer_forms import max_length_of
from analysis_service.answer_state import EARLY_RULES, POLICY_VERSION
from analysis_service.api import MAX_EVENT_STREAMS_PER_OWNER, max_body_bytes
from analysis_service.assertions import Basis
from analysis_service.auth import (
    ALLOWED_ALGORITHMS,
    DEFAULT_ALGORITHMS,
    AuthConfigError,
    build_verifier,
)
from analysis_service.claims import (
    GROUND_TEXT_MAX_CHARS,
    GroundKind,
    RuledClaim,
    Spelling,
    UnknownKey,
)
from analysis_service.config_files import MAX_VALUE_CHARS
from analysis_service.conformance import REFERENCE_MODELS
from analysis_service.controls import (
    MAX_CONTROL_ALIASES,
    MAX_CONTROL_EVIDENCE_TOTAL_BYTES,
    MAX_CONTROL_ROWS,
)
from analysis_service.deployment import ConfigPaths
from analysis_service.diagrams import _install_argv
from analysis_service.fact_answers import (
    MAX_DETAIL_CHARS,
    MAX_FACT_ANSWERS,
    FactAnswer,
    FactStatus,
    fact_kind,
)
from analysis_service.fan_in import BOUND_HOPS
from analysis_service.frameworks import SCHEMAS
from analysis_service.jobs import TERMINAL_STATUSES
from analysis_service.ladder import Rung
from analysis_service.links import MAX_LINK_ANSWERS
from analysis_service.model_tiers import (
    LLM_NODES,
    SUPPORTED_VERSION,
    TIER_NAMES,
    tiers_in_use,
)
from analysis_service.resilience import ResilienceConfig, load_resilience
from analysis_service.sampling import (
    OFFERED_PARAMS,
    TierSampling,
    env_var_for,
    load_sampling,
)
from analysis_service.sources import (
    DIAGRAM_TYPES,
    FORMATS,
    MAX_LABEL_CHARS,
    MAX_SYSTEM_NAME_CHARS,
    SourceKind,
)
from analysis_service.sql_store import _ADMISSION_LOCK_KEY
from analysis_service.store_backends import MAX_RETENTION_DAYS
from analysis_service.system_model import EXCERPT_MAX_CHARS, ElementPrefix, _Element
from analysis_service.validation import MAX_ELEMENTS
from analysis_service.vendors import VENDORS, CredentialMode
from evals.harness.early_rounds import paused_state
from evals.harness.instruments import INSTRUMENTS
from tests.factories import valid_model
from tests.source_tree import REPO_ROOT, guide_files
from tests.test_api import make_client
from webapp.main import MAX_RUNS

pytestmark = pytest.mark.lint

DOCS = REPO_ROOT / "docs"
CONFIGURATION = DOCS / "Configuration.md"
HTTP_API = DOCS / "HTTP-API.md"
CONCEPTS = DOCS / "Concepts.md"
WEB_APP = DOCS / "Web-App.md"
FIRST_RUN = DOCS / "First-Run.md"
REPORT_SCHEMA = DOCS / "Report-Schema.md"

#: The guides a reader copies a TOML example from. A record of a past state,
#: such as ``docs/adr/``, is not a guide, so an old version there is correct.
GUIDES = guide_files()

SHIPPED_PATHS = ConfigPaths.from_env({})
SHIPPED_RESILIENCE = load_resilience(SHIPPED_PATHS.resilience, env={})
SHIPPED_SAMPLING = load_sampling(SHIPPED_PATHS.sampling, env={})

_TOML_FENCE = re.compile(r"^```toml\n(.*?)^```", re.DOTALL | re.MULTILINE)
_ASSIGNMENT = re.compile(r"`([a-z_]+ = [^`\n]+)`")
_NUMBER_WORDS = {
    "twice": 2,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "ninth": 9,
    "ten": 10,
}


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
    stated: dict[str, str] = {}
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
    "max_owner_files": SHIPPED_RESILIENCE.max_owner_files,
    "max_controls": SHIPPED_RESILIENCE.max_controls,
    "max_control_evidence": SHIPPED_RESILIENCE.max_control_evidence,
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
    documented = {
        backticked(row[0])[0] for row in table(text, "Variable", "Effect", "Range")
    }
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
        for mode in vendor.credential_modes
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


# Storage and auth: Configuration.md repeats two HTTP-API.md tables.


def _from_http_api(rows: list[list[str]]) -> list[list[str]]:
    """The rows with each link into HTTP-API.md written as HTTP-API.md writes it."""
    return [[cell.replace("](HTTP-API.md#", "](#") for cell in row] for row in rows]


def test_the_storage_table_is_the_http_api_storage_table():
    header = ("Variable", "Values", "Rule")
    copy = table(section(CONFIGURATION, "### Job storage"), *header)

    assert _from_http_api(copy) == table(section(HTTP_API, "## Job storage"), *header)


def test_the_auth_table_is_the_http_api_auth_tables():
    provider, oidc = tables(
        section(HTTP_API, "### How the provider is chosen"), "Variable", "Purpose"
    )
    copy = table(section(CONFIGURATION, "### Bearer auth"), "Variable", "Purpose")

    assert copy == provider + oidc


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
    policy = re.search(r"names the policy that built it \(`([a-z0-9-]+)`\)", text)
    floors = re.search(
        r"A field question needs a `score` of at least (\d+), and a capability"
        r" question at least (\d+)\.",
        text,
    )
    limit = re.search(
        r"One pause asks at most (\d+) capability questions for each selected"
        r" framework",
        text,
    )
    unlimited = "Field questions have no limit" in text
    links = re.search(r"or there are more than (\d+) entries", text)

    assert policy and floors and limit and unlimited and links
    capability, field = EARLY_RULES["capability"], EARLY_RULES["field"]
    assert policy[1] == POLICY_VERSION
    assert (float(floors[1]), float(floors[2])) == (field.floor, capability.floor)
    assert int(limit[1]) == capability.limit
    assert field.limit is None
    assert int(links[1]) == MAX_LINK_ANSWERS


# The early entry: the keys a waiting job's early question carries.


def test_the_early_entry_names_every_key_the_service_sends():
    """The documented keys of an ``early_questions`` entry are the keys of a
    served one: :meth:`~analysis_service.early_questions.EarlyQuestion.to_json`
    and what the question set adds beside it."""
    text = flat(HTTP_API.read_text(encoding="utf-8"))
    entry = re.search(
        r"A waiting job also lists `early_questions`:.*?Each entry is `\{([^}]*)\}`",
        text,
    )
    assert entry is not None
    documented = {key.strip() for key in entry[1].split(",")}
    state = paused_state(valid_model(), {"stride": {}}, (), ())
    served = state.questions.to_json()["early_questions"]
    assert served
    assert documented == set(served[0])


def test_the_detected_type_list_is_every_element_prefix():
    """``detected_facts`` sends an element's ``id_prefix`` as its ``type``."""
    text = flat(HTTP_API.read_text(encoding="utf-8"))
    listed = re.search(
        r"`type` is the prefix of the element's ID: one of ([^.]*)\.", text
    )
    assert listed is not None
    assert set(re.findall(r"`([a-z]+)`", listed[1])) == set(get_args(ElementPrefix))


# Fact questions and answers: the key and the kinds that FactAnswer reads.

_JSON_FENCE = re.compile(r"^```json\n(.*?)^```", re.DOTALL | re.MULTILINE)


def _keyed(value: object) -> list[dict]:
    """Every JSON object in ``value``, at any depth, that holds a ``key``."""
    if isinstance(value, list):
        return [found for item in value for found in _keyed(item)]
    if not isinstance(value, dict):
        return []
    own = [value] if "key" in value else []
    return own + [found for item in value.values() for found in _keyed(item)]


def test_each_fact_key_example_is_a_key_the_service_reads():
    """A fact question's ``key`` and a fact answer read as the service reads them."""
    keyed = [
        found
        for block in _JSON_FENCE.findall(HTTP_API.read_text(encoding="utf-8"))
        if '"key"' in block
        for found in _keyed(json.loads(block))
    ]
    questions = [row for row in keyed if "kind" in row]
    answers = [row for row in keyed if "kind" not in row]

    assert questions and len(answers) >= 2
    for row in questions:
        key = TypeAdapter(UnknownKey).validate_python(row["key"])
        assert fact_kind(key) == row["kind"]
    for row in answers:
        FactAnswer.model_validate(row)


def test_the_ground_block_and_table_name_every_ground_kind():
    text = (DOCS / "Report-Schema.md").read_text(encoding="utf-8")
    block = re.search(
        r"^class Ground:\n    kind: \((.*?)\n    \)", text, re.DOTALL | re.MULTILINE
    )
    rows = table(text, "`kind`", "Carries", "Reads as")

    assert block
    assert set(re.findall(r'"([a-z-]+)"', block[1])) == set(get_args(GroundKind))
    assert {backticked(row[0])[0] for row in rows} == set(get_args(GroundKind))


def test_the_fact_kind_list_is_every_spelling():
    text = HTTP_API.read_text(encoding="utf-8")
    bullet = re.search(r"^- `kind` is (.*?)^- ", text, re.DOTALL | re.MULTILINE)

    assert bullet
    assert set(backticked(bullet[1])) == set(get_args(Spelling))


def test_the_history_list_is_every_fact_status():
    text = flat(HTTP_API.read_text(encoding="utf-8"))
    sentence = re.search(
        r"carries `history`, what became of it at the pause: (.*?)\. ", text
    )

    assert sentence
    assert set(backticked(sentence[1])) == set(get_args(FactStatus))


def test_the_source_kind_vocabulary_is_every_source_kind():
    text = flat((REPO_ROOT / "GLOSSARY.md").read_text(encoding="utf-8"))
    sentence = re.search(r"`kind` is a closed vocabulary of (.*?)\. ", text)

    assert sentence
    assert set(backticked(sentence[1])) == set(get_args(SourceKind))


def _paragraphs(path: Path) -> list[str]:
    """Each paragraph, list item and table row of one guide, flattened."""
    parts = re.split(r"\n\s*\n|\n(?=[-*|] )", path.read_text(encoding="utf-8"))
    return [flat(part) for part in parts if part.strip()]


def test_each_list_of_diagram_types_is_every_diagram_type():
    """A paragraph that names `architecture-beta` lists the diagram types that
    the reader reads, so it names each one of
    :data:`~analysis_service.sources.DIAGRAM_TYPES`."""
    lists = [
        (path, paragraph)
        for path in GUIDES
        for paragraph in _paragraphs(path)
        if "architecture-beta" in backticked(paragraph)
    ]
    short = [
        f"{path.relative_to(REPO_ROOT)}: {paragraph[:80]}"
        for path, paragraph in lists
        if not set(DIAGRAM_TYPES) <= set(backticked(paragraph))
    ]

    assert lists
    assert not short, f"these lists of diagram types are short: {short}"


def test_each_list_of_upload_extensions_is_its_whole_format_row():
    """A paragraph that names two extensions of one
    :data:`~analysis_service.sources.FORMATS` row copies the row, so it names
    every extension of that row."""
    copies = [
        (path, paragraph, row.extensions)
        for path in GUIDES
        for paragraph in _paragraphs(path)
        for row in FORMATS.values()
        if len(set(row.extensions) & set(backticked(paragraph))) > 1
    ]
    short = [
        f"{path.relative_to(REPO_ROOT)}: {paragraph[:80]}"
        for path, paragraph, extensions in copies
        if not set(extensions) <= set(backticked(paragraph))
    ]

    assert len({extensions for _, _, extensions in copies}) == len(FORMATS)
    assert not short, f"these lists of extensions are short: {short}"


def test_the_concepts_ground_list_is_every_ground_kind():
    bullets = re.findall(
        r"^- `([a-z-]+)`: ", section(CONCEPTS, "## Ground"), re.MULTILINE
    )

    assert sorted(bullets) == sorted(get_args(GroundKind))


def test_the_concepts_basis_bullet_is_every_basis():
    bullet = re.search(
        r"^- \*\*Each row states its basis\.\*\* (.*?)\n(?:\n|- )",
        section(CONCEPTS, "## Assertion"),
        re.DOTALL | re.MULTILINE,
    )

    assert bullet
    assert set(backticked(bullet[1])) == set(get_args(Basis))


@pytest.mark.parametrize(
    ("path", "bullet"),
    [
        (CONCEPTS, r"^- \*\*([A-Za-z ]+):\*\*"),
        (DOCS / "Architecture.md", r"^- \*\*([a-z-]+)\*\*"),
    ],
    ids=["concepts", "architecture"],
)
def test_the_outcome_list_is_every_terminal_status(path, bullet):
    names = re.findall(bullet, section(path, "## Outcomes"), re.MULTILINE)

    assert sorted(name.lower().replace(" ", "-") for name in names) == sorted(
        TERMINAL_STATUSES
    )


#: The count words a record section may use for the fields its package adds.
_FIELD_COUNTS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6}


@pytest.mark.parametrize("framework", sorted(SCHEMAS))
def test_each_record_block_is_every_field_its_package_adds(framework):
    """The block names each field the package's record adds, the section
    counts them in one "N fields, and nothing else" sentence, and each
    ``Literal[...]`` the block copies holds the members of the model's own."""
    record = SCHEMAS[framework].ruled_record
    text = section(DOCS / "Report-Schema.md", f"### {framework.upper()}'s own record")
    block = re.search(
        rf"^class {record.__name__}\(RuledClaim\):\n(.*?)^```",
        text,
        re.DOTALL | re.MULTILINE,
    )
    added = set(record.model_fields) - set(RuledClaim.model_fields)
    counts = re.findall(r"\b(\w+) fields?, and nothing else", text)

    assert block
    assert set(re.findall(r"^    ([a-z_]+):", block[1], re.MULTILINE)) == added
    assert len(counts) == 1, "the section states its field count once"
    assert counts[0] in _FIELD_COUNTS, f"no count word {counts[0]!r} in the lint"
    assert _FIELD_COUNTS[counts[0]] == len(added)
    for name, members in re.findall(
        r"^    ([a-z_]+): Literal\[(.*?)\]", block[1], re.MULTILINE
    ):
        annotation = record.model_fields[name].annotation
        assert get_origin(annotation) is Literal, f"{name} is not a Literal"
        assert ast.literal_eval(f"({members},)") == get_args(annotation), name


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


def test_every_tier_example_in_a_guide_selects_a_profiled_model():
    """A reader copies a ``[tiers.<name>]`` block out of a guide into
    ``config/model_tiers.toml``, so its model must be one the matrix
    profiles for its vendor. Every tier, ``review`` included, since each
    one is a selection a reader can copy."""
    shown = [
        (path.name, name, tier["vendor"], tier["model"])
        for path in GUIDES
        for block in _TOML_FENCE.findall(path.read_text(encoding="utf-8"))
        for name, tier in tomllib.loads(block).get("tiers", {}).items()
        if "model" in tier
    ]

    assert shown, "no guide shows a tier example to check"
    for doc, name, vendor, model in shown:
        assert model in REFERENCE_MODELS[vendor], (
            f"{doc} shows tiers.{name} as {vendor}/{model}, which is not"
            f" a model the matrix profiles for {vendor}"
        )


# Eval instruments: the registry the evals guides describe.

#: The evals guides, which together describe what a sweep reports.
EVALS_GUIDES = [path for path in GUIDES if path.parent == REPO_ROOT / "evals"]


def test_every_instrument_is_described_in_the_guides():
    """An instrument nobody documents is a number nobody can interpret.

    The registry grew an entry per measurement, and the prose beside it is a
    hand-kept enumeration of the same set — the shape that left
    ``evals/README.md``'s harness table six modules short. So the enumeration
    is keyed to the registry rather than remembered.

    **Either spelling counts.** Code names a reading with an identifier and
    prose names it in words: ``applicability_yield`` is written "applicability
    yield" in ``TUNING.md``, and both are the same instrument. Holding the
    guides to the identifier would be a style rule wearing a lint's clothes.

    This decides that an instrument is *mentioned*, never that the sentence
    around it is right. Nothing mechanical can decide the second — see
    ``tests/test_doc_reference_lints.py``.
    """
    prose = "\n".join(guide.read_text(encoding="utf-8") for guide in EVALS_GUIDES)
    undocumented = sorted(
        name
        for name in INSTRUMENTS
        if name not in prose and name.replace("_", " ") not in prose
    )

    assert not undocumented, (
        f"these instruments are described in no evals guide: {undocumented}."
        " A sweep reports every entry in INSTRUMENTS, so a reader meets a"
        " number the documentation never names. Describe it in README.md's"
        " metrics section, or in TUNING.md if it is a tuning lever."
    )


def test_the_evals_guides_are_actually_read():
    """Guards the guard: an empty set would pass the test above vacuously."""
    assert len(EVALS_GUIDES) >= 4, f"only found {[g.name for g in EVALS_GUIDES]}"
    assert INSTRUMENTS, "the registry is empty"


def test_the_nodes_example_is_every_node_key():
    """The guide says each framework this build can spell needs its three
    keys, so the example shows exactly the keys the loader requires."""
    text = section(
        CONFIGURATION, "### Node keys, and the frameworks this deployment carries"
    )
    (block,) = _TOML_FENCE.findall(text)

    assert set(tomllib.loads(block)["nodes"]) == set(LLM_NODES)


def test_the_first_run_tiers_are_the_tier_names_and_the_shipped_tiers():
    """The guide names every tier, and its table names the tiers the shipped
    node map binds."""
    text = section(DOCS / "First-Run.md", "## 2. Select models and provide credentials")
    named = re.search(r"model tiers: (.*?)\. ", flat(text))
    nodes = tomllib.loads(SHIPPED_PATHS.model_tiers.read_text(encoding="utf-8"))
    rows = table(text, "Tier", "Used for")

    assert named
    assert backticked(named[1]) == list(TIER_NAMES)
    assert {backticked(row[0])[0] for row in rows} == tiers_in_use(nodes["nodes"])


def test_the_rung_table_is_every_rung():
    rows = table(
        CONFIGURATION.read_text(encoding="utf-8"), "Rung", "How the schema travels"
    )

    assert [backticked(row[0])[0] for row in rows] == list(get_args(Rung))


# The diagram reader step: the install argv and the directory a wheel bundles.


def test_the_diagram_reader_step_is_the_install_the_app_runs():
    """The ``/v1`` deploy step runs :func:`_install_argv` from a checkout, and
    names the directory that ``force-include`` bundles the reader in."""
    text = section(HTTP_API, "#### The diagram reader step")
    fences = re.findall(r"^```bash\n(.*?)\n```", text, re.DOTALL | re.MULTILINE)
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    included = manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    *install, prefix = _install_argv()
    directory = Path(prefix).name

    assert len(fences) == 2
    assert fences[0].split() == [*install, directory]
    assert f"`{included[directory]}`" in flat(text)
    assert "import DEFAULT_DIAGRAM_READER_DIR as d" in fences[1]
    assert Path(prefix) == deployment.DEFAULT_DIAGRAM_READER_DIR


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


# Bounds: each number a guide copies from a constant.


def _numbers(path: Path, pattern: str) -> list[int]:
    """Each number the pattern's first group reads in the guide, with the
    guide's wraps read as spaces and its thousands separators dropped. A
    number word reads as its value."""
    found = re.findall(pattern, flat(path.read_text(encoding="utf-8")))
    assert found, f"{path.name} has no sentence that matches {pattern!r}"
    return [
        _NUMBER_WORDS[number]
        if number in _NUMBER_WORDS
        else int(number.replace(",", ""))
        for number in found
    ]


#: Each guide sentence that copies a bound, as its guide, the pattern that
#: reads the number, and the bound.
BOUND_COPIES: list[tuple[Path, str, int]] = [
    (
        WEB_APP,
        r"the limit is (\w+) the deployment's `max_source_bytes`",
        max_body_bytes(SHIPPED_RESILIENCE.source_limits())
        // SHIPPED_RESILIENCE.max_source_bytes,
    ),
    (HTTP_API, r"A token may hold at most (\d+) streams", MAX_EVENT_STREAMS_PER_OWNER),
    (HTTP_API, r"so a (\w+) stream gets `429`", MAX_EVENT_STREAMS_PER_OWNER + 1),
    (
        HTTP_API,
        r"must be unique within the job, at most (\d+) characters",
        MAX_LABEL_CHARS,
    ),
    (
        HTTP_API,
        r"one line, at most (\d+) characters, in your own words",
        MAX_DETAIL_CHARS,
    ),
    (HTTP_API, r"that line may hold at most ([\d,]+) characters", EXCERPT_MAX_CHARS),
    (HTTP_API, r"or a line longer than ([\d,]+) characters", EXCERPT_MAX_CHARS),
    (HTTP_API, r"more than its limit: (\d+) links", MAX_LINK_ANSWERS),
    (HTTP_API, r"more than its limit: \d+ links, (\d+) facts", MAX_FACT_ANSWERS),
    (HTTP_API, r"`pg_advisory_xact_lock\((\d+)\)`", _ADMISSION_LOCK_KEY),
    (HTTP_API, r"a whole number of days from 1 to (\d+)", MAX_RETENTION_DAYS),
    (HTTP_API, r"must be a whole number from 1 to (\d+)", MAX_RETENTION_DAYS),
    (CONFIGURATION, r"a whole number of days from 1 to (\d+)", MAX_RETENTION_DAYS),
    (CONFIGURATION, r"has ([\d,]+) characters or fewer", MAX_VALUE_CHARS),
    (CONFIGURATION, r"must have ([\d,]+) characters or fewer", MAX_VALUE_CHARS),
    (
        CONFIGURATION,
        r"`max_source_bytes` must be ([\d,]+) bytes",
        MAX_CONTROL_EVIDENCE_TOTAL_BYTES,
    ),
    (FIRST_RUN, r"keeps at most (\d+) recent runs", MAX_RUNS),
    (WEB_APP, r"At most (\d+) runs\.", MAX_RUNS),
    (WEB_APP, r"A Control holds at most (\d+) aliases", MAX_CONTROL_ALIASES),
    (WEB_APP, r"A Control holds at most \d+ aliases, (\d+) rows", MAX_CONTROL_ROWS),
    (WEB_APP, r"The app holds at most (\d+) jobs", MAX_RUNS),
    (WEB_APP, r"The bound of (\d+) runs", MAX_RUNS),
    (REPORT_SCHEMA, r"the verbatim span, ≤(\d+) chars", GROUND_TEXT_MAX_CHARS),
    (REPORT_SCHEMA, r"more than (\w+) hops from every place", BOUND_HOPS),
    (REPORT_SCHEMA, r"the bound is (\w+) such hops", BOUND_HOPS),
]


@pytest.mark.parametrize(
    ("path", "pattern", "bound"),
    BOUND_COPIES,
    ids=[f"{path.name}:{pattern[:32]}" for path, pattern, _ in BOUND_COPIES],
)
def test_each_copied_bound_is_its_constant(path, pattern, bound):
    assert set(_numbers(path, pattern)) == {bound}


def test_the_attribute_bound_is_each_attribute_field():
    """An answer to an attribute other than ``data_description`` is bounded by
    its field on the element, and every such free-text field takes one bound.
    A closed attribute, such as ``exposure``, takes one of its choices."""
    (stated,) = _numbers(
        HTTP_API, r"An attribute other than `data_description` takes at most (\d+)"
    )
    bounds = {
        max_length_of(element, attribute)
        for element in _Element.__subclasses__()
        for attribute in (*CONTROL_ATTRIBUTES, "technology", "protocol")
        if attribute in element.model_fields
        and element.model_fields[attribute].annotation is str
    }

    assert bounds == {stated}
