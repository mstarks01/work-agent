"""Each TOML loader takes its version as an integer, never a float or a bool.

TOML reads ``version = 9.0`` as a float and ``version = true`` as a bool, and
both compare equal to an integer. So an equality check alone accepts a file
whose version line nobody wrote as a version.
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
from evals.harness.roster import DEFAULT_ROSTER_PATH
from evals.harness.roster import load as load_roster
from tests.factories import TEST_TIER_ENV

CONFIG = Path(__file__).resolve().parents[1] / "config"

#: Each shipped file, and its loader.
LOADERS: dict[str, tuple[Path, Callable[[Path], object]]] = {
    "frameworks": (CONFIG / "frameworks.toml", lambda path: load_frameworks(path, {})),
    "model_tiers": (
        CONFIG / "model_tiers.toml",
        lambda path: load_model_tiers(path, TEST_TIER_ENV),
    ),
    "resilience": (CONFIG / "resilience.toml", lambda path: load_resilience(path, {})),
    "sampling": (CONFIG / "sampling.toml", lambda path: load_sampling(path, {})),
    "certification": (CONFIG / "blessed-fingerprints.toml", load_manifest),
    "roster": (DEFAULT_ROSTER_PATH, load_roster),
}


def _with_version(source: Path, target: Path, spell: Callable[[int], str]) -> Path:
    text = source.read_text(encoding="utf-8")
    found = re.search(r"^version = (\d+)$", text, re.MULTILINE)
    assert found is not None, source
    version = int(found.group(1))
    target.write_text(
        re.sub(
            r"^version = \d+$", f"version = {spell(version)}", text, flags=re.MULTILINE
        ),
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize("name", LOADERS)
def test_the_shipped_file_loads(name):
    path, load = LOADERS[name]
    load(path)


@pytest.mark.parametrize("name", LOADERS)
@pytest.mark.parametrize(
    "spell", [lambda n: f"{n}.0", lambda n: "true"], ids=["float", "bool"]
)
def test_a_version_that_is_not_an_integer_is_refused(tmp_path, name, spell):
    path, load = LOADERS[name]
    edited = _with_version(path, tmp_path / path.name, spell)

    with pytest.raises(ValueError, match="version"):
        load(edited)
