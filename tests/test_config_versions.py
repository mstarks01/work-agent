"""Each versioned file loader takes its version as an integer, never a float or a bool.

TOML and JSON read ``9.0`` as a float and ``true`` as a bool, and both compare
equal to an integer. So an equality check alone accepts a file whose version
line nobody wrote as a version.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from analysis_service.certification import load_manifest
from analysis_service.framework_config import load_frameworks
from analysis_service.model_tiers import load_model_tiers
from analysis_service.resilience import load_resilience
from analysis_service.sampling import load_sampling
from evals.harness.artifact import load_artifact
from evals.harness.provenance import REPO_ROOT
from evals.harness.roster import DEFAULT_ROSTER_PATH
from evals.harness.roster import load as load_roster
from tests.factories import TEST_TIER_ENV

CONFIG = REPO_ROOT / "config"

#: A sealed sweep artifact, which holds its version as ``artifact_version``.
ARTIFACT = (
    REPO_ROOT / "evals/baselines/6bff717-gpt-5.6-terra-24dda4db/mstarks01-e32ad9c5.json"
)

#: The version line of a TOML file: its prefix, then the number.
TOML_LINE = r"^(version = )(\d+)$"

#: Each shipped file, its loader, and the pattern of its version line.
LOADERS: dict[str, tuple[Path, Callable[[Path], object], str]] = {
    "frameworks": (
        CONFIG / "frameworks.toml",
        lambda path: load_frameworks(path, {}),
        TOML_LINE,
    ),
    "model_tiers": (
        CONFIG / "model_tiers.toml",
        lambda path: load_model_tiers(path, TEST_TIER_ENV),
        TOML_LINE,
    ),
    "resilience": (
        CONFIG / "resilience.toml",
        lambda path: load_resilience(path, {}),
        TOML_LINE,
    ),
    "sampling": (
        CONFIG / "sampling.toml",
        lambda path: load_sampling(path, {}),
        TOML_LINE,
    ),
    "certification": (CONFIG / "blessed-fingerprints.toml", load_manifest, TOML_LINE),
    "roster": (DEFAULT_ROSTER_PATH, load_roster, TOML_LINE),
    "artifact": (ARTIFACT, load_artifact, r'^(  "artifact_version": )(\d+)(?=,$)'),
}


def _with_version(
    source: Path, target: Path, line: str, spell: Callable[[int], str]
) -> Path:
    text = source.read_text(encoding="utf-8")
    found = re.search(line, text, re.MULTILINE)
    assert found is not None, source
    version = int(found.group(2))
    target.write_text(
        re.sub(
            line,
            lambda match: match.group(1) + spell(version),
            text,
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize("name", LOADERS)
def test_the_shipped_file_loads(name):
    path, load, _ = LOADERS[name]
    load(path)


@pytest.mark.parametrize("name", LOADERS)
@pytest.mark.parametrize(
    "spell", [lambda n: f"{n}.0", lambda n: "true"], ids=["float", "bool"]
)
def test_a_version_that_is_not_an_integer_is_refused(tmp_path, name, spell):
    path, load, line = LOADERS[name]
    edited = _with_version(path, tmp_path / path.name, line, spell)

    with pytest.raises(ValueError, match="version"):
        load(edited)
