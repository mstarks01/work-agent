"""The variables of a deployment's settings, joined from each loader family.

Each loader stays the only source of the variables it reads, and exports them
as ``env_variables()`` (ADR 0091). This module joins the exports, names the
variables that hold a secret file, and merges ``settings.env`` into the process
environment for the first-run app (ADR 0092). It imports no module that needs
an optional extra, so a process without the ``sql`` or the ``aws`` extra can
call it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from analysis_service import (
    deployment,
    framework_config,
    model_tiers,
    resilience,
    sampling,
    store_backends,
    vendors,
)
from analysis_service.config_files import (
    SETTINGS_FILE,
    SettingsFileError,
    SettingsLine,
    read_settings_env,
    settings_dir,
    value_refusal,
)
from analysis_service.objects import S3_KEY_FILES
from analysis_service.store_settings import DATABASE_PASSWORD_FILE_VAR


def deployment_variables() -> tuple[str, ...]:
    """Every variable that a loader of a Deployment Setting reads, once each.

    The Settings page holds one entry for each. A loader that reads a new
    variable exports it, or the test of the page module fails. The ``/v1``
    auth settings and ``ANALYSIS_OFFLINE`` are not here: no Deployment reads
    them.
    """
    return tuple(
        dict.fromkeys(
            (
                *deployment.env_variables(),
                *framework_config.env_variables(),
                *model_tiers.env_variables(),
                *vendors.env_variables(),
                *sampling.env_variables(),
                *resilience.env_variables(),
                *store_backends.env_variables(),
            )
        )
    )


def secret_vars() -> frozenset[str]:
    """Every variable that names a secret file.

    **The one reader of that fact.** The Settings page and its write-only key
    field call it. Each loader reads the file of one of these variables
    through :func:`~analysis_service.vendors.read_secret`, which applies the
    permission rule of a secret file. It collects the secret
    credential variables of each vendor and mode, the key files of the ``s3``
    object store, and the database password file. A ``_FILE`` suffix does not
    make a secret: ``ANALYSIS_TIERS_FILE`` names a config file, and
    ``ANALYSIS_DATABASE_CA_FILE`` names a certificate.
    """
    vendor_secrets = {
        var
        for vendor in vendors.VENDORS.values()
        for var in vendor.all_env_vars(secret_only=True)
    }
    return frozenset(
        {*vendor_secrets, *S3_KEY_FILES.values(), DATABASE_PASSWORD_FILE_VAR}
    )


def deployment_env(process_env: Mapping[str, str]) -> dict[str, str]:
    """The process environment with the lines of ``settings.env`` added.

    The first-run app builds its store and its Deployment from this one
    mapping. It refuses, and names the variable and never the value:

    - a value from either source that :func:`value_refusal` refuses, for each
      variable of :func:`deployment_variables`;
    - a key of ``settings.env`` that is not one of those variables, with its
      line number;
    - a variable that both sources set.

    A missing settings directory or ``settings.env`` adds nothing.
    """
    path = settings_dir(process_env) / SETTINGS_FILE
    return merged_env(process_env, read_settings_env(path), path)


def merged_env(
    process_env: Mapping[str, str], lines: Mapping[str, SettingsLine], path: Path
) -> dict[str, str]:
    """The process environment with ``lines``, the lines of the
    ``settings.env`` at ``path``, added, under the refusals of
    :func:`deployment_env`. A save checks the lines it will write with it."""
    known = set(deployment_variables())
    for var in sorted(known & process_env.keys()):
        if refusal := value_refusal(process_env[var]):
            raise SettingsFileError(f"{var} in the process environment {refusal}")
    for var, (number, value) in lines.items():
        where = f"{path} line {number}"
        if var not in known:
            raise SettingsFileError(
                f"{where}: {var} is not a setting that this version reads"
            )
        if var in process_env:
            raise SettingsFileError(
                f"{where}: {var} is also set in the process environment; unset one"
            )
        if refusal := value_refusal(value):
            raise SettingsFileError(f"{where}: {var} {refusal}")
    return {**process_env, **{var: line.value for var, line in lines.items()}}
