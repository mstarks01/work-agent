# Name the shapes before you read the value

The third defect class in `docs/agents/code-review.md`, and where a model-produced set belongs.

New code that reads a value fails on the shape its author never listed.
`unfence` split on `"\n"` and missed U+2028, U+2029 and U+0085, so a payload
carrying one round-tripped corrupted. The roster note called `.get` on an entry
TOML does not require to be a table, and `ada = "contributor"` — the line a
first-timer writes — raised `AttributeError` through a whole preflight.

**Write down every shape the value can take, then handle each one.** The
question is what the *producer* can emit, not what it usually emits: `str` has
more line terminators than `"\n"`, `tomllib` returns a scalar where you expect a
table, and a model emits a name that slugs to empty. Ask the parser's
documentation rather than the sample input.

Two audits, three defects, and each one a shape that was legal all along.

**A shape the producer is a model has a second half: the set.** Where a gate
checks a field against a computed set, the provider-facing schema has to state
that set, because a model asked for a string of 1 to 100 characters will
eventually write prose into it. `Assumption.attribute` carried a length bound
and no enum, and a live sweep lost a case to a 100-character sentence in it —
the same failure the `Ground` comment beside it already recorded from the
critic, 132 times in six runs. Grep `validation.py` for `not in`: each one is a
gate check whose schema may say only `max_length`.

**Prefer the enum in `json_schema_extra` to a validator.** A value outside the
set then stays an `invalid-reference` that names its element, so `repair_scope`
keeps the repair to that element; a validator makes it a schema fault with no
element to name, and the scope widens to the whole object. Validating harder
enlarges the blast radius.

**The rule stops where the set is configurable.** `assets` has the identical
shape and takes no enum, because `extra_asset_tags` lets a deployment extend
the vocabulary and the output schema is static. A configurable set is stated in
the prompt, as `extract.md` rule 6 states it. Which of the two a field is
decides where its set belongs.
