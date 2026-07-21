"""The eval judge: claim equivalence, and adjudication of unmatched threats.

Two narrow judgement calls, and nothing else (ticket 009 decisions 7 and 9).
Everything mechanically decidable — the lane prefilter, one-to-one assignment,
severity arithmetic, reference resolution — happens in
:mod:`evals.harness.scorer` without a model, per the standing principle that
deterministic decisions belong in code.

Three properties this module exists to hold:

* **The judge is pinned separately from the tiers** (decision 12). A judge
  upgrade silently re-scores history and turns a comparison into a phantom
  regression, so ``evals/config/judge.toml`` is versioned on its own and there
  is deliberately no env-var override: changing the judge is a reviewable diff
  and an explicit re-baselining event, gated by
  :mod:`evals.harness.calibration`.
* **Position bias is killed by randomizing pair order** (decision 12). Which
  claim is presented first is drawn from a seeded RNG, so the ordering is
  reproducible across runs but uncorrelated with which side is the reference.
* **The judge prompt does not live in ``prompts/``** (decision 14). That tree
  is the shipped service's, governed by ticket 020's lints and baked into the
  production container; a judge prompt satisfies none of those rules and has
  no business in the deployed image.

Security: claims are model-produced text and reach the judge as data, never as
instructions (OWASP LLM01). They ride in a JSON payload the prompt names as
untrusted, and the judge's own output is constrained by a response schema and
re-validated here before any of it reaches a metric (LLM05).
"""

from __future__ import annotations

import json
import random
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stride_service.markdown_loader import MarkdownLoader
from stride_service.model_tiers import validate_model_string
from stride_service.report import StrideCategory
from stride_service.system_model import SystemModel

EVALS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JUDGE_CONFIG_PATH = EVALS_ROOT / "config" / "judge.toml"
DEFAULT_JUDGE_PROMPTS_DIR = EVALS_ROOT / "prompts"

CLAIM_PROMPT_NAME = "judge_claim_equivalence"
ADJUDICATION_PROMPT_NAME = "judge_adjudication"

# Ticket 009 decision 9. ``ungrounded`` is the only gating bucket: a threat
# asserting a fact the blessed model does not support is the failure that
# destroys trust in a security report. ``valid-unlisted`` is explicitly *not*
# a failure — references are non-exhaustive by construction.
Bucket = Literal["ungrounded", "valid-unlisted", "noise"]

RulingT = TypeVar("RulingT", bound=BaseModel)


class JudgeConfigError(ValueError):
    """The judge configuration is invalid or unusable."""


class JudgeError(RuntimeError):
    """The judge did not return a usable ruling."""


class JudgeConfig(BaseModel):
    """The pinned judge: model string and its own sampling.

    Sampling is pinned *with the judge* rather than read from
    ``config/sampling.toml``: the shared file is the configuration under test,
    and a judge whose decoding moved with it would re-score history every time
    the suite measured a new production temperature.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=2.0)
    # Seeds the presentation-order shuffle, so a run is reproducible while
    # order stays uncorrelated with which side is the reference.
    order_seed: int = 0

    def model_post_init(self, _context: object) -> None:
        validate_model_string(self.model, source="judge.model")


def load_judge_config(path: Path | str = DEFAULT_JUDGE_CONFIG_PATH) -> JudgeConfig:
    """Load and validate the separately-pinned judge configuration."""
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise JudgeConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise JudgeConfigError(f"{path}: cannot be read: {exc}") from exc
    try:
        return JudgeConfig(**raw)
    except ValidationError as exc:
        raise JudgeConfigError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class ClaimPair:
    """One candidate pair for the equivalence judge, always within a lane."""

    case: str
    category: StrideCategory
    reference_claim: str
    candidate_claim: str


class ClaimRuling(BaseModel):
    """The judge's answer on one pair: same attacker action, same target?"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    match: bool
    rationale: str = Field(min_length=1, max_length=400)


class BucketRuling(BaseModel):
    """Where an unmatched produced threat lands (decision 9)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bucket: Bucket
    rationale: str = Field(min_length=1, max_length=400)


@dataclass(frozen=True)
class UnmatchedThreat:
    """A produced threat no reference claimed, on its way to adjudication."""

    threat_id: str
    category: StrideCategory
    claim: str
    description: str
    affected_element_ids: tuple[str, ...]


class Judge(Protocol):
    """The two judgement calls the scorer is allowed to make.

    A protocol so the scorer is unit-testable offline against a scripted
    stand-in with zero Vertex calls, which is exactly what the credential-free
    PR job runs (decision 17).
    """

    def equivalent(self, pair: ClaimPair) -> ClaimRuling: ...

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling: ...


class MemoJudge:
    """A judge that answers a repeated question with its first answer.

    Exists for critic yield (ticket 028), which scores the pre-critic drafts
    and the post-critic threats against the *same* references. The post-critic
    set is a subset of the pre-critic one, so without memoization the second
    pass re-asks — and re-pays for — a question already answered, and a judge
    at non-zero temperature could answer it differently the second time. That
    would show up as critic yield rather than as judge variance, which is the
    one thing this instrument must not manufacture.

    **Scope it to one case.** ``adjudicate`` takes a system model that is not
    part of the key, so a memo shared across cases could hand one case's ruling
    to another; per-case instances make that impossible by construction rather
    than by a digest nobody would maintain.
    """

    def __init__(self, inner: Judge) -> None:
        self._inner = inner
        self._claims: dict[ClaimPair, ClaimRuling] = {}
        self._buckets: dict[tuple[UnmatchedThreat, tuple[str, ...]], BucketRuling] = {}
        self.hits = 0

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        if pair in self._claims:
            self.hits += 1
        else:
            self._claims[pair] = self._inner.equivalent(pair)
        return self._claims[pair]

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling:
        # ``sibling_claims`` is part of the key on purpose: the pre-critic set
        # carries the drafts the critic went on to kill, so the same threat is
        # genuinely being adjudicated against a different field of siblings on
        # each side, and reusing one ruling for both would be wrong.
        key = (threat, sibling_claims)
        if key in self._buckets:
            self.hits += 1
        else:
            self._buckets[key] = self._inner.adjudicate(
                threat, system_model, sibling_claims
            )
        return self._buckets[key]


def claim_payload(pair: ClaimPair, *, rng: random.Random) -> dict[str, object]:
    """The pair as the judge sees it, in randomized presentation order.

    Returns the two claims as ``claim_a``/``claim_b`` with the reference on a
    coin-flip side. Equivalence is symmetric, so nothing needs unmapping
    afterwards — the shuffle exists purely so position cannot stand in for
    "this one is the reference".
    """
    claims = [pair.reference_claim, pair.candidate_claim]
    if rng.random() < 0.5:
        claims.reverse()
    return {
        "stride_category": pair.category,
        "claim_a": claims[0],
        "claim_b": claims[1],
    }


def adjudication_payload(
    threat: UnmatchedThreat,
    system_model: SystemModel,
    sibling_claims: tuple[str, ...],
) -> dict[str, object]:
    """An unmatched threat plus the facts it must be grounded in."""
    return {
        "stride_category": threat.category,
        "claim": threat.claim,
        "description": threat.description,
        "affected_element_ids": list(threat.affected_element_ids),
        "system_model": system_model.model_dump(mode="json"),
        "boundary_crossings": [
            crossing.model_dump(mode="json")
            for crossing in system_model.boundary_crossings()
        ],
        "other_reported_claims": list(sibling_claims),
    }


class VertexJudge:
    """The pinned judge, over Vertex through the GenAI SDK.

    Authentication is ADC (decision 17: IAM, never API keys), so nothing here
    reads a credential — the CI job's Workload Identity Federation short-lived
    credentials are picked up implicitly, and the harness needs no code change
    to authenticate.
    """

    def __init__(
        self,
        config: JudgeConfig,
        *,
        client: object | None = None,
        prompts: MarkdownLoader | None = None,
    ) -> None:
        self._config = config
        self._rng = random.Random(config.order_seed)
        self._prompts = prompts or MarkdownLoader(DEFAULT_JUDGE_PROMPTS_DIR)
        self._claim_prompt = self._prompts.load(CLAIM_PROMPT_NAME)
        self._adjudication_prompt = self._prompts.load(ADJUDICATION_PROMPT_NAME)
        self._client = client or self._default_client()
        self._served_versions: set[str] = set()

    @property
    def served_model_versions(self) -> tuple[str, ...]:
        """The model versions Vertex reported actually serving these calls.

        The configured string is only a request (ticket 026): for generations
        that ship no numbered builds, the stable identifier resolves to
        whichever build is current, so reproducibility rests on recording what
        answered rather than on the string alone. More than one entry in a
        single run means the build moved mid-run — which is exactly the fact a
        phantom regression would otherwise be blamed on the prompt.
        """
        return tuple(sorted(self._served_versions))

    @staticmethod
    def _default_client() -> object:
        from google import genai

        return genai.Client()

    def equivalent(self, pair: ClaimPair) -> ClaimRuling:
        return self._ask(
            self._claim_prompt,
            claim_payload(pair, rng=self._rng),
            ClaimRuling,
        )

    def adjudicate(
        self,
        threat: UnmatchedThreat,
        system_model: SystemModel,
        sibling_claims: tuple[str, ...],
    ) -> BucketRuling:
        return self._ask(
            self._adjudication_prompt,
            adjudication_payload(threat, system_model, sibling_claims),
            BucketRuling,
        )

    def _ask(
        self, instruction: str, payload: dict[str, object], schema: type[RulingT]
    ) -> RulingT:
        """One judge call, constrained to ``schema`` and re-validated here."""
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._config.model,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=instruction,
                temperature=self._config.temperature,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        served = getattr(response, "model_version", None)
        if served:
            self._served_versions.add(served)
        try:
            return schema.model_validate_json(response.text or "")
        except ValidationError as exc:
            raise JudgeError(
                f"judge returned output that is not a valid {schema.__name__}"
            ) from exc
