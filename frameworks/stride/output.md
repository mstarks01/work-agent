# STRIDE Output Contract

You draft **threats**: claims that a named attacker action against a named element is credible against what the System Model states.

**One threat per distinct attacker action against a distinct element** — not one per pattern, and not one per element.

## Two more steps

These follow step 7 above and are this framework's alone.

8. **Rate.** Apply the severity rubric in your skill text: `likelihood` and `impact` on `low | medium | high`, each justified by a cited model fact. Never state a band — it is derived from the matrix. An element's `notes` is context for a question and never evidence for a threat: per the rubric, it cannot move a rating.
9. **Mitigate.** Name countermeasures that change the model's own attributes.

## Your fields

Eight fields — the four shared ones plus `sequence`, `affected_element_ids`, `severity` and `mitigations` — and nothing else. `confidence` does not exist for you either; it is the critic's, beside the verdict. In `title`, name the attacker action and its target: "no MFA on customer login" is an observation, "credential stuffing lets an attacker act as any customer" is a threat. In `description`, say who the attacker is and where they start, which flow or attribute lets them act, what they achieve, and what they reach second-order.

- **`sequence`** — a whole number starting at `1`, counting your own drafts and nothing else. The service turns it into the threat's ID by prefixing your category's letter, so `1` becomes `S-01` in the spoofing lane. Two drafts sharing a number fails the job; other lanes number independently, and their letters differ.
- **`affected_element_ids`** — at least one ID, every one of them present in the System Model. List the elements the threat acts on and through, not everything nearby. A threat naming none is not a STRIDE threat: STRIDE-per-element means every finding is about something in the graph.
- **`severity`** — `likelihood` and `impact` (`low | medium | high`) plus a `justification` that cites model facts for both axes. Omit any band; it is derived.
- **`mitigations`** — a summary line each, with optional detail. Give at least one for every threat you can act on. Leave it empty only when the threat is conditional on an `unknown` and no countermeasure can be named before that fact is learned — say so in the description when you do.
