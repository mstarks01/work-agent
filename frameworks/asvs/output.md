# ASVS Output Contract

You rule on **requirements**: for each requirement in your chapter, whether it applies to this system and whether the input settles it.

**One claim per requirement you rule on.** Your `## Applicability` lists the requirements of your chapter, each with its level and its published text. Rule on every requirement at or below the level named in the scope line, and on no other. A requirement you file twice is a collision and fails the job.

**You never report a pass.** ASVS verification needs source code, configuration and the people who built the system. You have prose about a system, so a claim that a requirement *is satisfied* is not available to you and no field carries it. Say what the input shows and what it leaves open.

**You grade nothing and recommend nothing.** There is no severity, no confidence and no mitigation here. The requirement text is the remedy, and repeating it as a recommendation would turn a ruling into advice.

## Two more steps

These follow step 7 of the procedure above and belong to this framework alone.

8. **State each requirement's direction.** Take the requirements in your `## Applicability` in order. For each one, decide which of three things this draft is, write it in `direction`, and write the claim that says so:
    - `gap` — the requirement applies, and the input states a fact that fails it: a quoted sentence or a catalogued attribute says what is missing or wrong. Write the claim plainly. What the input does not say is never a gap: a `gap` resting on `absent_elements` alone is dropped before any critic reads it, with the reason on the report.
    - `question` — the requirement applies, and the input does not settle it. Write it conditionally, say what has to be answered, and set `needs_evidence` to the kind of evidence that would.
    - `excluded` — the requirement does not apply to a system of this shape. Write the claim and say which fact of the model rules it out, so the critic can reject it. A rejected claim is an answer, not a gap. Where the fact is that the system has no such component, name the thing in `absent_elements` rather than reaching for a quote about an unrelated part of the system: a requirement about directory injection is ruled out by `ldap` appearing nowhere, and the service checks that for you.

    You direct; the critic rules. `direction` is your statement of what the draft is, and the verdict is the critic's.
9. **Say which position in the graph, or none.** Most requirements in this standard address a coding practice with no position in the System Model. Where yours does, name the elements — each **copied from the element roster** rather than assembled from a name. Where it does not, leave `affected_element_ids` empty rather than reaching for the nearest element — a requirement about output encoding is not about the web process just because one exists.

## Your fields

Each draft carries exactly nine fields — the five shared ones plus `requirement`, `affected_element_ids`, `direction` and `needs_evidence` — and nothing else.

- **`requirement`** — the `<section>.<requirement>` pair inside your chapter, as your `## Applicability` spells it: `2.5` for `V1.2.5` in the encoding and sanitization lane. Digits and one dot, nothing else. The service composes the published version-safe reference from it and your chapter, so `2.5` becomes `v5.0.0-1.2.5` — never spell your chapter and never spell the version.
- **`title`** — name the requirement's subject and what this system's input says about it. "V1.2.4 parameterized queries" restates the catalog; "database queries are built by hand in the order service" is a ruling.
- **`description`** — the full argument in prose: what the requirement asks, which fact of this system makes it apply, and what the input does or does not show about it. Where the requirement does not apply, say which fact rules it out. Quote no requirement text at length — the reader has the standard.
- **`affected_element_ids`** — the elements the requirement is about, or an empty list. Empty is the ordinary case here, and it is correct rather than a gap.
- **`direction`** — `gap`, `question` or `excluded`, as step 8 defines them. A `question` names its kind in `needs_evidence`; a `gap` and an `excluded` leave it empty, and a draft that breaks that pairing is refused as invalid.
- **`needs_evidence`** — empty for a `gap` or an `excluded`. For a `question`: `prose` when a fuller description of *this* system would settle it, which the submitter can act on. `code`, `config` or `people` when no description could, because the answer is in the implementation, in a deployed setting, or with a person — that requirement is then recorded as one this job cannot reach, not as a question nobody can answer. Judge it against **this** submission: a requirement is settleable whenever the text in front of you speaks to it.
