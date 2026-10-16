# 91. The settings page reads the loaders' own rules

- **Status**: accepted
- **Date**: 2026-10-16
- **Relates to**: map [#1760](https://github.com/mstarks01/work-agent/issues/1760),
  ticket [#1761](https://github.com/mstarks01/work-agent/issues/1761)

## Context

The **Settings** page of the first-run app changes and shows **Deployment
Settings**. For each setting, the page must know the type, the allowed values,
the group, whether the setting is a secret, and whether a change needs a
restart.

The loaders hold these rules. Pydantic models and Literals give the types and
the bounds. The `VENDORS` rows give each vendor's credential variables and
mark the secret ones. The `closed_setting` tables name each store backend.
The cross-field checks, such as `_check_complete`, `validate_model` and the
database URL checks, are code and not data.

A save builds a test Deployment from the new values with the same loaders and
refuses the save if the load fails. So the page uses the rules only to draw
the form. It never validates a value.

## Decision

**The loaders stay the only source of each type, closed set and bound.**

- The page reads a type through `model_json_schema()` on `ModelTierConfig`
  and `ResilienceConfig`. For the other settings, it reads the `VENDORS`
  rows, the `closed_setting` tables, and `typing.get_args` on a bare Literal.
- One page module in `webapp` holds only the facts that no loader holds: the
  group, the label, and one mark for each setting. The mark is "changes",
  "shows", or "excluded" with a reason. The key of an entry is the Deployment
  Setting, such as `tiers.strong.model`, and not the env var or the TOML key
  that sets it.
- `secret_vars()` in `analysis_service` is the one reader of "this variable
  names a secret file". It collects the flags from `VENDORS`, from the S3 key
  files and from the database password file. The page, the write-only key
  field and the file permission check call it. A variable is not a secret
  because its name ends in `_FILE`: `ANALYSIS_TIERS_FILE` and
  `ANALYSIS_DATABASE_CA_FILE` are not secrets.
- The settings that the store loaders read need a restart, as one group. The
  prefixes come from the store loaders' own tuples, not from a new list.
- A `PASS_FLAGS` table in `deployment.py` maps each optional-pass variable to
  its default. `Deployment.from_env`, the page and a doc test read it.
- Each row of a storage table (`JOB_STORES`, `OBJECT_STORES`, `DATABASES`,
  `DATABASE_CREDENTIALS`) declares a `reads` tuple. `refuse_unread` and the
  page both read it, so the page shows only the fields of the selected
  backend.
- `deployment_variables()` in `analysis_service` collects the variable names
  that each loader family exports. A test requires one page-module entry for
  each name. A loader that reads a new variable must export it, or the test
  fails.

## Considered options

**A central settings registry**, which each loader reads. It would put every
fact about a variable on one line, and it would give one list of all the
variables. The registry lost for three reasons:

1. The cross-field checks cannot move into a registry, so the loaders keep
   rules either way.
2. The pydantic models already define the TOML settings and their env
   overrides. A registry that also states those types is a second reader,
   unless the code generates the models from the registry, which makes them
   dynamic and harder to read.
3. The change touches about 60 variables across six loaders that work and
   have tests.

The `deployment_variables()` test holds the scattered facts together without
a registry.

**A copy of the rules in the page**, held to the loaders by a test. This adds
a second reader of every closed set.

## Consequences

The facts about one variable sit in up to four places: the loader type, the
`VENDORS` row or the storage table, the page module, and the family export.
Read the page module first; each entry points to its loader.
