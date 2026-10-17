"""The rule readers of ADR 0091, and the page module that walks them.

The loaders stay the only source of each type, closed set and bound. These
tests hold the joined variables against the page module, the secret rule
against the loaders that read a secret, and the storage rows against the
settings that their stores read.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections import Counter
from dataclasses import fields
from typing import get_args

import pytest

from analysis_service import sampling
from analysis_service.deployment import (
    BLESSED_FINGERPRINTS_VAR,
    CONFIG_PATHS,
    ConfigPathName,
    ConfigPaths,
)
from analysis_service.model_tiers import TIER_NAMES, load_model_tiers
from analysis_service.objects import S3_KEY_FILES
from analysis_service.resilience import load_resilience
from analysis_service.sampling import SamplingConfigError
from analysis_service.settings_variables import deployment_variables, secret_vars
from analysis_service.sql_store import DATABASE_CREDENTIALS, DATABASES
from analysis_service.store_backends import build_store, retention_days
from analysis_service.store_settings import (
    DATABASE_PASSWORD_FILE_VAR,
    DATABASE_VARIABLES,
)
from analysis_service.vendors import REGION, VENDORS, CredentialMode, vendor_for
from tests.factories import PROJECT_ROOT, TEST_TIER_ENV, declared_env, secret_env
from webapp import settings_page
from webapp.settings_page import EXAMPLES, IAM_SETUP, SETTINGS
from webapp.settings_view import _CONTROLS


class TestSecretVars:
    def test_it_names_each_secret_that_a_loader_reads(self):
        vendor_secrets = {
            var
            for vendor in VENDORS.values()
            for var in vendor.all_env_vars(secret_only=True)
        }
        assert vendor_secrets
        assert secret_vars() == {
            *vendor_secrets,
            *S3_KEY_FILES.values(),
            DATABASE_PASSWORD_FILE_VAR,
        }

    @pytest.mark.parametrize(
        "var",
        [
            "ANALYSIS_TIERS_FILE",
            "ANALYSIS_FRAMEWORKS_FILE",
            "ANALYSIS_DATABASE_CA_FILE",
            "ANALYSIS_VERTEX_PROJECT",
            "ANALYSIS_BEDROCK_REGION",
        ],
    )
    def test_a_file_or_an_address_is_not_a_secret(self, var):
        assert var in deployment_variables()
        assert var not in secret_vars()


class TestDeploymentVariables:
    def test_each_name_is_listed_once(self):
        names = deployment_variables()
        assert len(names) == len(set(names))

    def test_each_secret_is_a_deployment_variable(self):
        assert secret_vars() <= set(deployment_variables())

    @pytest.mark.parametrize("var", sampling.env_variables())
    def test_each_sampling_name_is_one_the_loader_folds(self, var):
        """The loader parses each name it reads; the export spells each one.
        No offered param takes ``?``, so the loader refuses it for each name,
        and the refusal is about the value, not the name."""
        with pytest.raises(SamplingConfigError) as refused:
            sampling.load_sampling(
                PROJECT_ROOT / "config" / "sampling.toml", {var: "?"}
            )
        assert "unknown tier" not in str(refused.value)
        assert "is not overridable" not in str(refused.value)


class TestStorageRows:
    def test_the_database_rows_read_every_database_setting(self):
        """The rows need the ``sql`` extra; the list does not. Each must hold
        the other."""
        read = {var for database in DATABASES.values() for var in database.reads}
        read |= {var for mode in DATABASE_CREDENTIALS.values() for var in mode.reads}
        assert read == set(DATABASE_VARIABLES)


class TestThePageModule:
    def test_one_entry_for_each_variable_and_one_variable_for_each_entry(self):
        named = Counter(
            setting.variable for setting in SETTINGS.values() if setting.variable
        )
        assert set(named) == set(deployment_variables())
        assert max(named.values()) == 1

    def test_an_entry_with_no_variable_is_shown_only(self):
        """A TOML key that no variable overrides cannot change on the page."""
        unnamed = [key for key, setting in SETTINGS.items() if not setting.variable]
        assert unnamed == ["tiers.review_independence"]
        assert SETTINGS["tiers.review_independence"].mark == "shows"

    def test_sampling_and_review_independence_are_shown_only(self):
        sampling_keys = {key for key in SETTINGS if key.startswith("sampling.")}
        assert len(sampling_keys) == len(sampling.env_variables())
        for key in {*sampling_keys, "tiers.review_independence"}:
            assert SETTINGS[key].mark == "shows", key

    def test_the_config_paths_and_the_blessed_fingerprints_are_excluded(self):
        excluded = {
            setting.variable: setting
            for setting in SETTINGS.values()
            if setting.mark == "excluded"
        }
        assert set(excluded) == {var for var, _ in CONFIG_PATHS.values()}
        assert "Promotion" in excluded[BLESSED_FINGERPRINTS_VAR].reason

    def test_each_file_key_names_a_config_path(self):
        """``_Files.lookup`` reads ``CONFIG_PATHS`` by the field a file key
        names, and the Literal of the names spans the same fields."""
        names = {field.name for field in fields(ConfigPaths)}
        assert set(get_args(ConfigPathName)) == set(CONFIG_PATHS) == names
        for key, setting in SETTINGS.items():
            if setting.file_key is not None:
                assert setting.file_key[0] in CONFIG_PATHS, key

    def test_a_reason_comes_with_each_mark_but_changes(self):
        for key, setting in SETTINGS.items():
            assert bool(setting.reason) is (setting.mark != "changes"), key

    def test_each_tier_has_its_model_and_its_vendor(self):
        for tier in TIER_NAMES:
            assert SETTINGS[f"tiers.{tier}.model"].mark == "changes"
            assert SETTINGS[f"tiers.{tier}.vendor"].mark == "changes"

    def test_each_vendor_with_the_iam_mode_has_its_setup(self):
        assert set(IAM_SETUP) == set(VENDORS)
        for name, vendor in VENDORS.items():
            has_iam = CredentialMode.IAM in vendor.credential_modes
            assert (IAM_SETUP[name] is not None) is has_iam, name

    def test_the_module_holds_no_rule_of_a_loader(self):
        """No regex, and no Literal but the mark: a type, a closed set or a
        bound stays in its loader."""
        tree = ast.parse(inspect.getsource(settings_page))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }
        assert "re" not in imported
        literals = [
            node.targets[0].id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Subscript)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "Literal"
        ]
        assert literals == ["Mark"]


def _closed_settings() -> list[tuple[str, list[str]]]:
    """Each Deployment Setting variable that a loader reads with
    :func:`~analysis_service.store_settings.closed_setting`, and the set that the loader passes it.

    The scan reads each call in the source of the package, and evaluates its
    setting and its table in the namespace of the module of the call. A call
    whose setting is not a variable, such as the driver of the database URL,
    names a part of a free-text value, so the scan leaves it out.
    """
    package = PROJECT_ROOT / "src" / "analysis_service"
    found = {}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "closed_setting"
        ]
        if not calls:
            continue
        namespace = vars(importlib.import_module(f"analysis_service.{path.stem}"))
        for call in calls:
            variable = eval(ast.unparse(call.args[1]), namespace)
            if variable in deployment_variables():
                table = eval(ast.unparse(call.args[2]), namespace)
                found[variable] = sorted(str(name) for name in table)
    return sorted(found.items())


class TestTheChoices:
    def test_the_scan_finds_each_closed_store_setting(self):
        assert {variable for variable, _ in _closed_settings()} >= {
            "ANALYSIS_JOB_STORE",
            "ANALYSIS_OBJECT_STORE",
            "ANALYSIS_DATABASE_CREDENTIALS",
        }

    @pytest.mark.parametrize(
        ("variable", "names"),
        _closed_settings(),
        ids=[variable for variable, _ in _closed_settings()],
    )
    def test_the_page_offers_the_closed_set_of_its_loader(self, variable, names):
        [key] = [key for key, row in SETTINGS.items() if row.variable == variable]
        assert sorted(_CONTROLS.get(key, {}).get("options", [])) == names


#: The settings of a PostgreSQL job store with a filesystem object store, from
#: the examples. Each store example is checked over this base.
STORE_BASE = {
    "ANALYSIS_JOB_STORE": "sql",
    "ANALYSIS_DATABASE_URL": EXAMPLES["store.database_url"],
    "ANALYSIS_DATABASE_CREDENTIALS": "password",
    **secret_env(DATABASE_PASSWORD_FILE_VAR, "not-a-real-password"),
    "ANALYSIS_OBJECT_STORE": "filesystem",
    "ANALYSIS_OBJECT_ROOT": EXAMPLES["store.object_root"],
}

#: The settings over :data:`STORE_BASE` of an ``s3`` object store with the key
#: pair, which reads the other object settings.
S3_STORE = {
    "ANALYSIS_OBJECT_STORE": "s3",
    "ANALYSIS_OBJECT_ROOT": "",
    "ANALYSIS_OBJECT_BUCKET": EXAMPLES["store.object_bucket"],
    "ANALYSIS_OBJECT_REGION": EXAMPLES["store.object_region"],
    "ANALYSIS_OBJECT_CREDENTIALS": "api_key",
    **{
        var: secret_env(var, f"not-a-real-{var.lower()}")[var]
        for var in S3_KEY_FILES.values()
    },
}


def _passes_its_loader(key: str, example: str) -> None:
    """Run ``example`` through the loader that reads ``key``; raise where the
    loader refuses it.

    The region of the database needs the identity of a platform to build a
    store, so it runs through the shape that the loader checks first.
    """
    var = SETTINGS[key].variable
    assert var is not None, key
    if key.startswith("tiers."):
        for vendor in VENDORS.values():
            vendor.validate_model(example, key)
    elif key.startswith("upstreams."):
        tiers = PROJECT_ROOT / "config" / "model_tiers.toml"
        load_model_tiers(tiers, TEST_TIER_ENV | {var: example})
    elif key.startswith("credentials."):
        vendor = vendor_for(key.split(".")[1])
        for mode in vendor.credential_modes:
            if var in vendor.required_env_vars(mode):
                env = declared_env(vendor.name, mode) | {var: example}
                vendor.credential_kwargs(env, mode)
    elif key.startswith("resilience."):
        load_resilience(PROJECT_ROOT / "config" / "resilience.toml", {var: example})
    elif key == "store.database_region":
        assert REGION.fullmatch(example)
    else:
        s3 = (
            S3_STORE
            if var.startswith("ANALYSIS_OBJECT_") and key != "store.object_root"
            else {}
        )
        env = STORE_BASE | s3 | {var: example}
        build_store({name: value for name, value in env.items() if value})
        retention_days(env)


class TestTheExamples:
    @pytest.mark.parametrize("key", sorted(EXAMPLES))
    def test_each_example_passes_the_loader_of_its_setting(self, key):
        _passes_its_loader(key, EXAMPLES[key])

    def test_each_free_text_field_has_one(self):
        """A key field and a choice have none. A setting that no value passes
        has none: the upstreams of a vendor that routes to one provider, the
        charges of a vendor with one charge mode, and the CA file, whose loader
        opens the file."""
        no_example = {
            f"upstreams.{name}"
            for name, vendor in VENDORS.items()
            if vendor.upstream_pin is None
        }
        no_example |= {
            f"charges.{name}"
            for name, vendor in VENDORS.items()
            if len(vendor.charge_modes) < 2
        }
        no_example.add("store.database_ca_file")
        free_text = {
            key
            for key, setting in SETTINGS.items()
            if setting.mark == "changes"
            and setting.variable not in secret_vars()
            and "options" not in _CONTROLS.get(key, {})
        }
        assert set(EXAMPLES) == free_text - no_example
