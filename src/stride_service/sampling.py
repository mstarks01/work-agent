"""Sampling configuration for the graph's LLM nodes.

Implements decision 15 of wayfinder ticket 009: sampling is pinned in a
versioned config file that **eval and production read from the same place**.
An eval-only temperature is the failure mode where the suite goes green while
production drifts, so there is deliberately no env-var override here — unlike
:mod:`stride_service.model_tiers`, where ops retune tier strings on Cloud Run.
Changing sampling is a reviewable diff to ``config/sampling.toml``.

The default is ``temperature = 0``, and it is a **knob, not a constant**:
greedy decoding most closely reproduces the anchored exemplar pattern and so
plausibly amplifies the exemplar-domain bias the corpus exists to measure, and
it forecloses the Self-MoA recall lever. Which value to ship is the first
thing the eval suite measures (wayfinder ticket 025), which is only possible
if the value is configurable.

Loading fails closed: a malformed file, an out-of-range value or an unknown
key raises :class:`SamplingConfigError` rather than falling back to library
defaults, since a node quietly running on different sampling than the config
records invalidates every eval result taken against it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SamplingConfigError(ValueError):
    """The sampling configuration is invalid or unusable."""


class SamplingConfig(BaseModel):
    """Validated decoding parameters applied to every LLM node.

    Only the parameters the design has an opinion about live here. Anything
    absent is left to the model's own default on purpose: pinning a value
    this project has never measured would claim a decision nobody made.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)

    def to_generate_content_config(self) -> types.GenerateContentConfig:
        """The ADK/GenAI object a node is configured with."""
        return types.GenerateContentConfig(
            temperature=self.temperature, top_p=self.top_p
        )


def load_sampling(path: Path | str) -> SamplingConfig:
    """Load and validate the sampling config from its TOML file."""
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SamplingConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise SamplingConfigError(f"{path}: cannot be read: {exc}") from exc

    try:
        return SamplingConfig(**raw)
    except ValidationError as exc:
        raise SamplingConfigError(f"{path}: {exc}") from exc
