# 101. A lane may run on its own tier

- **Status**: accepted
- **Date**: 2026-10-25
- **Amends**: the rule in `config/model_tiers.toml` that tier keys are per
  framework and never per lane.

## Context

The tier config maps each framework's lanes to one key, `analyze/<framework>`.
The reason was that every lane runs the same judgement, so a key per lane would
hold one value that nothing reads.

A provider can refuse one lane and not the others. On the anthropic vendor,
Opus 5.5 stopped STRIDE jobs of cases 02, 14 and 15 at its content filter
(QA-2026-10-24-01). After #1947 named the node, a run of case 02 showed the
denial-of-service lane as the lane that refused (QA-2026-10-25-01). Its
fragment is defensive threat modelling. Sonnet 5 ran all 13 cases with no
refusal. With one key per framework, the only remedy was to move all six lanes
off Opus.

## Decision

**A lane may have its own optional key, `analyze/<framework>/<lane>`.** A lane
with no key runs on `analyze/<framework>`. A lane key names one of the three
tiers, so an operator who wants Sonnet 5 for one lane sets `review` to Sonnet 5
and maps the lane to `review`.

- `lane_key` is the one spelling of the key, and `framework_of_lane_key` its one
  reader.
- `ModelTierConfig.resolve_tier` resolves a lane key, and falls back to the
  framework key when the map does not set it.
- The loader refuses a lane key under a framework this build does not carry.
  It cannot list a package's lanes, because the packages import it, so
  `validate_packages` refuses a lane that the package does not declare.
- `independence_breaches` checks each lane key against its framework's critic,
  as it checks `analyze/<framework>`.

## Consequences

- `review` can hold a lane, which does not match its name. A fourth tier keeps
  the names true, and it costs environment variables, a vendor-parity row and a
  version of the tiers file. The maintainer chose the existing tier.
- A lane on `review` with the critic also on `review` breaches
  `distinct_model`, and the loader reports it.
- The tiers file keeps version 9: a file with no lane key loads as before.
