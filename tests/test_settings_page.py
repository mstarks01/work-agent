"""The rule readers of ADR 0091, and the page module that walks them.

The loaders stay the only source of each type, closed set and bound. These
tests hold the joined variables against the page module, the secret rule
against the loaders that read a secret, and the storage rows against the
settings that their stores read.
"""

from __future__ import annotations

import ast
import inspect
from collections import Counter

import pytest

from analysis_service import sampling
from analysis_service.deployment import BLESSED_FINGERPRINTS_VAR, CONFIG_PATHS
from analysis_service.model_tiers import TIER_NAMES
from analysis_service.objects import S3_KEY_FILES
from analysis_service.sampling import SamplingConfigError
from analysis_service.settings_variables import deployment_variables, secret_vars
from analysis_service.sql_store import DATABASE_CREDENTIALS, DATABASES
from analysis_service.store_settings import (
    DATABASE_PASSWORD_FILE_VAR,
    DATABASE_VARIABLES,
)
from analysis_service.vendors import VENDORS, CredentialMode
from webapp import settings_page
from webapp.settings_page import IAM_SETUP, SETTINGS


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

    def test_each_sampling_name_is_one_the_loader_folds(self):
        """The loader parses each name it reads; the export spells each one.
        A name that the fold cannot place raises an error about the name, and
        any other error is about the value."""
        for var in sampling.env_variables():
            try:
                sampling._apply_env_overrides({}, {var: "?"})
            except SamplingConfigError as error:
                assert "unknown tier" not in str(error), var
                assert "is not overridable" not in str(error), var


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
