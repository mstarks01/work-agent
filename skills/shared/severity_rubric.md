# Severity Rubric

Severity is qualitative **likelihood × impact**. You rate the two inputs on `low | medium | high` and give a one-sentence justification for each threat. The severity band (`low | medium | high | critical`) is **derived mechanically from the matrix below — never assert a band yourself**; a stated band that contradicts the matrix is a validation error.

## The matrix

| likelihood \ impact | low | medium | high |
|---|---|---|---|
| **high** | medium | high | **critical** |
| **medium** | low | medium | high |
| **low** | low | low | medium |

## Rating likelihood

How plausible is it that a capable attacker exploits this within the system's normal life? Anchor on model facts:

- **Attacker population.** `exposure: internet-facing` or a flow from an untrusted zone → anyone can attempt it. Derived boundary crossings from lower-trust zones raise likelihood; purely intra-zone paths require a prior foothold, lowering it one notch — not to zero.
- **Prerequisites.** Count what the attacker must already have: valid credentials, a position on the network path, a compromised neighbor, specialist knowledge. Each hard prerequisite lowers likelihood; "possession of a leaked static key" is a cheap prerequisite, not a hard one.
- **Control state.** A stated, strong control on the relevant attribute (`authentication`, `encryption_in_transit`, `encryption_at_rest`) lowers likelihood. An `unknown` control is unverified: rate likelihood as if the control may be absent, and say the rating is conditional on the unknown in your justification.
- **Effort and reliability.** Point-and-shoot attacks with public tooling rate higher than multi-step chains needing luck or timing.

## Rating impact

What is lost if the threat succeeds? Anchor on model facts:

- **Assets touched.** Asset tags on the affected elements are the primary input: `credentials` and `secrets` compromise usually rates **high** (they unlock further systems); `pii`, `health`, `financial` rate at least **medium**, high at scale or with regulatory exposure; `availability-critical` makes outage impact high; `reputation` colors public-facing failures.
- **Data classification.** A Data Store's `data_classification` sets a floor: restricted/confidential data disclosure or corruption is not low-impact.
- **Blast radius.** Score the full reach, including second-order consequences the threat description names: elements reachable through outbound flows, dependents that fail in a cascade, every consumer of a poisoned store. One record vs. the corpus; one user vs. all tenants.
- **Reversibility.** Recoverable interruptions rate lower than irreversible disclosure, unrecoverable corruption, or safety-relevant harm.

## Calibration rules

- Justifications must cite model facts (element IDs, attributes, tags, crossings) — not vibes. A rating whose justification cites nothing is uncalibrated.
- Rate likelihood assuming no compensating controls beyond those stated in the model; the model's silence is not mitigation.
- Do not inflate likelihood to express high impact, or vice versa — the matrix combines them; each axis is rated on its own evidence.
- Threats conditioned on `unknown` attributes keep their conditional framing: rate as stated above and leave resolution to the needs-info verdict path.
- An element's `notes` **never moves a rating.** It carries what someone said about a fact — a hedge, an admitted gap, two sources contradicting each other — rather than the fact itself, so an `unknown` attribute rates identically whether a speaker was unsure about it, two people disagreed about it, or nobody raised it at all. Read `notes` to sharpen the question you ask or the mitigation you name; never to raise or lower likelihood or impact.
- The critic calibrates ratings across categories for consistency: identical fact patterns must receive identical ratings regardless of which analyst produced them.
