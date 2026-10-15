# Name the shapes before you read the value

The third defect class in `docs/agents/code-review.md`, and where a model-produced set belongs.

New code that reads a value fails on the shape its author never listed. A
split on `"\n"` misses U+2028, U+2029 and U+0085, so a payload that carries one
round-trips corrupted. A `.get` on a TOML entry that need not be a table raises
`AttributeError` on `ada = "contributor"`, the line a first-timer writes.

**Write down every shape the value can take, then handle each one.** The
question is what the *producer* can emit, not what it usually emits: `str` has
more line terminators than `"\n"`, `tomllib` returns a scalar where you expect a
table, and a model emits a name that slugs to empty. Ask the parser's
documentation rather than the sample input.

**A shape the producer is a model has a second half: the set.** Where a gate
checks a field against a computed set, the provider-facing schema has to state
that set, because a model asked for a string of 1 to 100 characters will
eventually write prose into it. A field with a length bound and no enum admits
a sentence; the `Ground` comment records this failure from the critic, 132
times in six runs. Grep `validation.py` for `not in`: each one is a
gate check whose schema may say only `max_length`.

**Prefer the enum in `json_schema_extra` to a validator.** A value outside the
set then stays an `invalid-reference` that names its element, so `repair_scope`
keeps the repair to that element; a validator makes it a schema fault with no
element to name, and the scope widens to the whole object. Validating harder
enlarges the blast radius.

**`assets` is a list of tags from a closed set.** Its set is
`CORE_ASSET_TAGS`. The schema states that set as an enum on each item through
`assets_field`, `extract.md` rule 6 states it in the prompt, and the gate
refuses a tag outside it as `illegal-asset-tag`.
