# 92. The settings directory and its strict env file

- **Status**: accepted
- **Date**: 2026-10-16
- **Relates to**: map [#1760](https://github.com/mstarks01/work-agent/issues/1760),
  ticket [#1763](https://github.com/mstarks01/work-agent/issues/1763)

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
- a copy of `model_tiers.toml`, `frameworks.toml` or `resilience.toml`, only
  after the first save that changes a value in that file;
- one secret file for each secret variable that `secret_vars()` names. The
  file name is the variable without `ANALYSIS_` and `_FILE`, in lower case:
  `ANALYSIS_ANTHROPIC_API_KEY_FILE` names `anthropic_api_key`.

The directory holds no copy of `sampling.toml` or `blessed-fingerprints.toml`,
because the page does not change them. A copy is a byte copy of the shipped
file, with the changes of the save. `settings.env` names each copy through its
path variable and an absolute path, for example `ANALYSIS_TIERS_FILE`. So
`_path` stays the only reader of the location of a TOML file. The app does
not merge a later shipped file into a copy.

**The format of `settings.env`.** Each line is `KEY=value`, or a blank line.
`KEY` matches `ANALYSIS_[A-Z0-9_]+`. The value is all of the text after the
first `=`, with no change. The format has no quotes, no `export`, no `$`
expansion and no comments. One function in `config_files.py` reads the file.
It refuses any other line and gives the line number. It also refuses a key
that occurs two times. The same file loads in systemd `EnvironmentFile=` and
in `docker --env-file`.

**The write.** The app makes the directory and each copy at the first save
that needs them, not at startup. It makes the directory with mode `0700`. It
writes each file to a new temporary file in the same directory with mode
`0600` and `O_EXCL`, then calls `fsync` and `os.replace`. It does not change
the mode of a directory or file that already exists. The permission check
refuses a bad mode and names it. In one save, the app writes the secret files
and the copies first, then `settings.env`, then deletes each file that the
new `settings.env` does not name. A save that leaves no tier with a vendor
removes that vendor's key line and its key file, and the page says so before
the save.

## Considered options

**A permissive `.env` format** with quotes, `export` and comments. The app
writes the full file again on each save, so it would drop a comment without a
message. Each extra form also needs a rule in the parser. A strict format
refuses the line and loses nothing.

**Code adds the path of a copy to the mapping** when the copy exists. That
rule has no line in a file, so a person cannot see why the loader reads the
copy. A line in `settings.env` shows it, and the conflict rule for a process
variable covers it.

**Keep a key file that no tier uses**, so that a switch back needs no new
key. The page never shows a key again, and a secret on disk that no code
reads gives only exposure.

## Consequences

A move of the directory breaks the absolute paths in `settings.env`. The fix
is one line for each copy. A copy keeps the shipped values of the day the app
made it. When a stale value fails a loader, the loader's own message shows.
