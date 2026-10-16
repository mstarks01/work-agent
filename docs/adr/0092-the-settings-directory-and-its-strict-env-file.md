# 92. The settings directory and its strict env file

- **Status**: accepted
- **Date**: 2026-10-16
- **Relates to**: map [#1760](https://github.com/mstarks01/work-agent/issues/1760),
  tickets [#1763](https://github.com/mstarks01/work-agent/issues/1763) and
  [#1764](https://github.com/mstarks01/work-agent/issues/1764)

## Context

The **Settings** page of the first-run app writes the **Deployment Settings**
to one directory outside the checkout. The app makes one mapping from the
process environment and `settings.env` in that directory, and gives it to
`Deployment.from_env`. A file on the user's disk is hard to change after a
release, so its location, its contents and its format are recorded here.

## Decision

**The directory.** The default is `$XDG_CONFIG_HOME/work-agent/`. When
`XDG_CONFIG_HOME` is unset or is not an absolute path, the default is
`~/.config/work-agent/`. `ANALYSIS_SETTINGS_DIR` replaces the default. The
app reads `ANALYSIS_SETTINGS_DIR` only from the process environment, because
`settings.env` cannot name its own location.

**The contents.** The directory is flat and has mode `0700`. Each file has
mode `0600`:

- `settings.env`;
- one secret file for each secret variable that `secret_vars()` names. The
  file name is the variable without `ANALYSIS_` and `_FILE`, in lower case:
  `ANALYSIS_ANTHROPIC_API_KEY_FILE` names `anthropic_api_key`.

**No TOML file.** The page writes each changed value as its `ANALYSIS_*`
override in `settings.env`, for example `ANALYSIS_MODEL_BASE_MODEL` or
`ANALYSIS_TIMEOUT_MS`. The shipped TOML files stay the base, and the directory
holds no copy of them. Each loader stays the only reader of its setting.

**One new override.** `frameworks.toml` has one value that the page changes
and that has no override: `carried`. `ANALYSIS_FRAMEWORKS_CARRIED` is that
override. Its value is a list of framework names separated by commas, with no
spaces, for example `asvs,stride`. Each name must be in `FrameworkName`. The
framework loader refuses a duplicate name and an empty list. The variable
replaces the `carried` list of the file and does not add to it. The framework
loader is the only reader of the variable.

**The format of `settings.env`.** Each line is `KEY=value`, or a blank line.
`KEY` matches `ANALYSIS_[A-Z0-9_]+`. The value is all of the text after the
first `=`, with no change. The format has no quotes, no `export`, no `$`
expansion and no comments. One function in `config_files.py` reads the file.
It refuses any other line and gives the line number. It also refuses a key
that occurs two times. The same file loads in systemd `EnvironmentFile=` and
in `docker --env-file`.

**The value rule.** A value is printable (`str.isprintable()`), has 4,096
characters or fewer, has no `\`, has no whitespace at either end, and does not
start with `'` or `"`. The parser splits the file on `\n` only. One function in
`config_files.py` holds the rule. The writer calls it, and the merge calls it
for each value from the process environment and from `settings.env`. systemd
removes whitespace at the ends of a value, reads `\` as an escape and as a line
continuation, and reads a value that starts with a quote as quoted text that
can span lines. Each refused form would give systemd a different value or a
second entry.

**The write.** The app makes the directory at the first save, not at
startup. It makes the directory with mode `0700`. It
writes each file to a new temporary file in the same directory with mode
`0600` and `O_EXCL`, then calls `fsync` and `os.replace`. It does not change
the mode of a directory or file that already exists. The permission check
refuses a bad mode and names it. In one save, the app writes the secret files
first, then `settings.env`, then deletes each file that the
new `settings.env` does not name. A save that leaves no tier with a vendor
removes that vendor's key line and its key file, and the page says so before
the save.

## Considered options

**A permissive `.env` format** with quotes, `export` and comments. The app
writes the full file again on each save, so it would drop a comment without a
message. Each extra form also needs a rule in the parser. A strict format
refuses the line and loses nothing.

**A copy of each TOML file that the page changes**, named in `settings.env`
by its path variable. The runtime has no TOML writer. A copy that the app
writes again from a model of its values loses the comments that explain each
rule. A copy that the app edits in place needs a new dependency, and a line
editor written for this purpose is a second reader of the TOML format. Each
form of copy also keeps the shipped values of the day the app made it, so an
upgrade does not reach it. An override changes one value and leaves the rest
of the shipped file in force.

**Keep a key file that no tier uses**, so that a switch back needs no new
key. The page never shows a key again, and a secret on disk that no code
reads gives only exposure.

## Consequences

A move of the directory breaks the absolute path in each `_FILE` line of
`settings.env`.

An upgrade changes the shipped base under the overrides. An override keeps its
value, so the page shows the shipped value beside it and offers a reset, which
removes the line. When an upgrade renames or removes a variable, the startup
refuses the line and names its key and line number. The page shows that line,
and a save can remove it.

`settings.env` is the main store of the changes of a person, and the TOML files
hold the defaults. A person who reads `config/model_tiers.toml` sees no tier
model. The Source column of the page shows where each value comes from.
