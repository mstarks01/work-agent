"""The facts of the Settings page that no loader holds (ADR 0091).

Each entry of :data:`SETTINGS` is one **Deployment Setting**, keyed by the
setting, such as ``tiers.strong.model``. An entry holds the variable that sets
it, its group, its label, its mark, and the key of the shipped TOML file that
holds its base value. The mark says what the page does with
the setting: it ``changes`` it, it ``shows`` it with a reason, or it leaves it
out (``excluded``) with a reason.

The loaders stay the only source of each type, closed set and bound, so no
entry holds one. Each family of entries walks the loader's own export: the
tiers and the vendors of :mod:`~analysis_service.model_tiers`, the credential
variables of each ``VENDORS`` row, the offered params of
:mod:`~analysis_service.sampling`, the overrides of
:mod:`~analysis_service.resilience`, the optional passes and the config paths
of :mod:`~analysis_service.deployment`, the carried frameworks of
:mod:`~analysis_service.framework_config`, and the store settings of
:mod:`~analysis_service.store_backends`. The test of this module holds the
variables of :data:`SETTINGS` against
:func:`~analysis_service.settings_variables.deployment_variables`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from analysis_service import (
    deployment,
    framework_config,
    resilience,
    sampling,
    store_backends,
)
from analysis_service.model_tiers import (
    TIER_NAMES,
    charges_env_var_for,
    credentials_env_var_for,
    env_vars_for,
    upstreams_env_var_for,
)
from analysis_service.vendors import VENDOR_NAMES, VENDORS, VendorName, refused_var

#: What the page does with a setting.
Mark = Literal["changes", "shows", "excluded"]


@dataclass(frozen=True)
class Setting:
    """One Deployment Setting, with the facts about it that no loader holds."""

    #: The variable that sets it. ``None`` for a TOML key that no variable
    #: overrides, which the page can only show.
    variable: str | None
    group: str
    label: str
    mark: Mark
    #: Why the page does not change it. Empty where the mark is ``changes``.
    reason: str
    #: Where the shipped TOML file holds its base value: the field of
    #: :data:`~analysis_service.deployment.CONFIG_PATHS` that names the file,
    #: and the path of keys in it. ``None`` where no file holds one.
    file_key: tuple[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class IamSetup:
    """What the page tells the integrator for a vendor in the ``iam`` mode,
    which reads no key."""

    note: str
    command: str


#: The ``iam`` setup of each vendor whose row allows the ``iam`` mode, and
#: ``None`` for each other vendor. ``docs/First-Run.md`` gives the commands.
IAM_SETUP: dict[VendorName, IamSetup | None] = {
    "vertex": IamSetup(
        note=(
            "Uses your Google login, so this page stores no key. Run this once"
            " on this machine:"
        ),
        command="gcloud auth application-default login",
    ),
    "anthropic": None,
    "openai": None,
    "bedrock": IamSetup(
        note=(
            "Uses your AWS identity, so this page stores no key: an attached"
            " role, AWS_PROFILE or SSO. For SSO, run:"
        ),
        command="aws sso login",
    ),
    "gemini": None,
    "openrouter": None,
}

_UNCERTIFIED = "A change makes each run uncertified until a sweep measures it."
_CONFIG_PATH = (
    "A config path chooses the base file, and the page writes each change as an"
    " override on that base."
)
_PROMOTION = "Only Promotion writes the blessed fingerprints."


def _words(name: str) -> str:
    """A label from a variable stem or a field name: ``max_active_jobs`` gives
    ``Max active jobs``."""
    return name.replace("_", " ").lower().capitalize()


def _changes(
    key: str,
    variable: str,
    group: str,
    label: str,
    file_key: tuple[str, tuple[str, ...]] | None = None,
) -> tuple[str, Setting]:
    return key, Setting(variable, group, label, "changes", "", file_key)


def _tiers_file(*keys: str) -> tuple[str, tuple[str, ...]]:
    return "model_tiers", keys


def _model_settings() -> list[tuple[str, Setting]]:
    entries = []
    for tier in TIER_NAMES:
        vendor_var, model_var = env_vars_for(tier)
        name = tier.capitalize()
        entries += [
            _changes(
                f"tiers.{tier}.{field}",
                var,
                "Models",
                f"{name} {field}",
                _tiers_file("tiers", tier, field),
            )
            for field, var in (("vendor", vendor_var), ("model", model_var))
        ]
    entries.append(
        (
            "tiers.review_independence",
            Setting(
                None,
                "Models",
                "Review independence",
                "shows",
                _UNCERTIFIED,
                _tiers_file("review_independence"),
            ),
        )
    )
    for vendor in VENDOR_NAMES:
        entries += [
            _changes(
                f"credentials.{vendor}.mode",
                credentials_env_var_for(vendor),
                "Credentials",
                f"{vendor} credential mode",
                _tiers_file("credentials", vendor),
            ),
            _changes(
                f"charges.{vendor}",
                charges_env_var_for(vendor),
                "Models",
                f"{vendor} charges",
                _tiers_file("charges", vendor),
            ),
            _changes(
                f"upstreams.{vendor}",
                upstreams_env_var_for(vendor),
                "Models",
                f"{vendor} upstream providers",
                _tiers_file("upstreams", vendor),
            ),
        ]
    return entries


def _credential_settings() -> list[tuple[str, Setting]]:
    """Each credential variable of each ``VENDORS`` row, once, under the stem
    that its vendor's prefix leaves: ``ANALYSIS_BEDROCK_REGION`` is
    ``credentials.bedrock.region``."""
    entries = []
    for vendor, row in VENDORS.items():
        variables = dict.fromkeys(
            var for mode in row.credentials for var in row.required_env_vars(mode)
        )
        for var in variables:
            stem = refused_var(var).removeprefix(f"ANALYSIS_{vendor.upper()}_")
            entries.append(
                _changes(
                    f"credentials.{vendor}.{stem.lower()}",
                    var,
                    "Credentials",
                    f"{vendor} {stem.replace('_', ' ').lower()}",
                )
            )
    return entries


def _sampling_settings() -> list[tuple[str, Setting]]:
    return [
        (
            f"sampling.{tier}.{param}",
            Setting(
                sampling.env_var_for(tier, param),
                "Sampling",
                f"{tier.capitalize()} {param.replace('_', ' ')}",
                "shows",
                _UNCERTIFIED,
                ("sampling", ("tiers", tier, param)),
            ),
        )
        for tier in TIER_NAMES
        for param in sampling.OFFERED_PARAMS
    ]


def _deployment_settings() -> list[tuple[str, Setting]]:
    entries = [
        _changes(
            f"resilience.{field}",
            var,
            "Resilience and limits",
            _words(field),
            ("resilience", (field,)),
        )
        for field, (var, _) in resilience.ENV_OVERRIDES.items()
    ]
    entries += [
        _changes(
            f"passes.{var.removeprefix('ANALYSIS_').lower()}",
            var,
            "Optional passes",
            _words(var.removeprefix("ANALYSIS_")),
        )
        for var in deployment.PASS_FLAGS
    ]
    entries.append(
        _changes(
            "frameworks.carried",
            framework_config.CARRIED_VAR,
            "Frameworks",
            "Frameworks carried",
            ("frameworks_file", ("carried",)),
        )
    )
    entries.append(
        _changes(
            "certification.require_certified",
            deployment.REQUIRE_CERTIFIED_VAR,
            "Certification",
            "Require a certified configuration",
        )
    )
    entries += [
        (
            f"paths.{field}",
            Setting(
                var,
                "Config paths",
                _words(field),
                "excluded",
                _PROMOTION
                if var == deployment.BLESSED_FINGERPRINTS_VAR
                else _CONFIG_PATH,
            ),
        )
        for field, (var, _) in deployment.CONFIG_PATHS.items()
    ]
    entries += [
        _changes(
            f"store.{var.removeprefix('ANALYSIS_').lower()}",
            var,
            "Job storage",
            _words(var.removeprefix("ANALYSIS_").removesuffix("_FILE")),
        )
        for var in store_backends.env_variables()
    ]
    return entries


def _table(entries: list[tuple[str, Setting]]) -> dict[str, Setting]:
    """The entries as a table; a key that two families make raises."""
    table: dict[str, Setting] = {}
    for key, setting in entries:
        if key in table:
            raise ValueError(f"two settings have the key {key!r}")
        table[key] = setting
    return table


#: Every Deployment Setting the page knows, keyed by the setting.
SETTINGS = _table(
    [
        *_model_settings(),
        *_credential_settings(),
        *_sampling_settings(),
        *_deployment_settings(),
    ]
)
