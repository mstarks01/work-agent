# Capability reader contrast cases

Each folder is one short source that tests where the capability reader
(`prompts/capabilities.md`) must draw a line. The cases here contrast what
states `multi-factor-authentication` with what does not.

Each folder holds three files:

- `source.md` is the submitted text. A job carries it as its system
  description.
- `model.json` is an empty System Model. The promotion gate of ADR 0086 reads
  it as the reference model, which states no capability.
- `case.json` states the `actor` who presents the factors, the
  `capability_scope` of the sign-in, the `reading` that a reader which follows
  the prompt emits, the `rule_break_quote` that a reader which breaks the MFA
  rule would quote, and the `expected` ASVS level 3 decisions.

`tests/test_capability_contrast.py` sends each `reading` through the real
graph as a scripted reply. A scripted reply proves only what the code does with
it. It does not show what a model emits.

A live measurement runs the cases through the gate script of QA-2026-10-14-02.
That script reads a case under `evals/corpus/`, so give each case as a path
from there:

```sh
uv run python evals/experiments/QA-2026-10-14-02/gate.py <out.json> <model route> \
  ../capability_contrast/01-one-time-code-only \
  ../capability_contrast/02-independent-factors \
  ../capability_contrast/03-support-impersonation \
  ../capability_contrast/04-third-party-own-users \
  ../capability_contrast/05-account-and-code
```

The run costs money, and `ANALYSIS_OFFLINE` refuses it.
