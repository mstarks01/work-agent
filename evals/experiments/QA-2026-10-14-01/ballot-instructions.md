# Capability reader ballot: instructions

## What this ballot is for

A model read 15 descriptions and stated the capabilities each one says the
application has. Each row is one statement: a description, a capability, and
the words the model quoted. Your verdicts measure how often the model is
right, and for which capabilities. They decide whether the service may state
a capability from a description without a question, and which capabilities
it may state.

Code already checked each quote: every quote is in its description. Your job
is the meaning: do the quoted words show that the application has this
capability?

## The files

- `capability-reader-ballot.csv`: 254 rows. Fill in the last three columns.
- `descriptions.md`: the full text of each description, if a quote needs its
  context.

## The columns

| Column | What it holds |
| --- | --- |
| `n` | the row number. Do not change it. |
| `case` | `example` is `examples/orders.md`; the other names are corpus cases |
| `capability` | the key the model stated |
| `meaning` | what the capability means, from the service's table |
| `part of` | the parent capability, if this one is a part |
| `quote the reader gave` | the words the model quoted |
| `runs that stated it` | how many of the runs stated it: 5 runs on the example, 1 on each case |
| `verdict` | **you fill this in** |
| `reason` | **you fill this in** for a `wrong` row |
| `note` | **optional**, any comment |

Do not change the text in the other columns. I check that every row comes
back with the same text.

## The verdicts

Write one of these three words in `verdict`:

- `right`: the description shows that the application has the capability,
  as the meaning defines it. The quote need not be the best one, if the
  description says it somewhere.
- `wrong`: the description does not show it.
- `unsure`: you cannot tell from the description. An `unsure` row counts as
  neither right nor wrong.

For each `wrong` row, write one of these reasons in `reason`:

- `not-stated`: the description does not say it. The model guessed what an
  application like this usually has.
- `third-party`: the capability belongs to a third party, such as an
  identity provider or a payment provider, and not to the application.
- `too-specific`: the description shows the parent, or a near capability,
  but not this one.
- `other`: anything else. Say what in `note`.

Leave `reason` blank on a `right` or an `unsure` row.

## Examples

These two are not ballot rows.

- "Users upload profile pictures, which we resize" for `file-upload`:
  `right`.
- "The service runs on Kubernetes" for `os-command` ("the application runs
  operating system commands or programs"): `wrong`, reason `not-stated`.
  Running on Kubernetes does not show that the application itself runs
  commands.

## When you are done

Send the CSV back in this conversation. Every row needs a verdict.
