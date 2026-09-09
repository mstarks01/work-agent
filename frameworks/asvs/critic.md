# What an ASVS Verdict Asserts

This framework rules on **requirement applicability**: claims that a named ASVS 5.0.0 requirement applies to this system and that the submitted text does or does not settle it.

## The three states, for this framework

- **confirmed** — the requirement applies to this system, and a stated fact or a quoted sentence shows the gap: the input says what is missing or wrong, rather than leaving it unsaid. The fact that makes it apply is stated in the model or quoted from the sources. A confirmed claim needs no reason: the ruling is the argument. Silence about a control is never a gap; that draft is `needs-info` or a `reasoning` rejection, below. A draft that called itself a gap and rested on absent elements alone was dropped before you read it, with the reason on the report.
- **needs-info** — the requirement applies, and the input does not settle it. Say what has to be answered. An `unknown` is not a missing control and never becomes one here; it is a question the submitter can answer. Most requirements about a document — an authorization policy, a set of validation rules — land here by construction, because no representation of a running system holds the answer. **Those take a `subject`: the question itself, in one plain sentence.** Use `element_id` and `attribute` only where the requirement really does turn on a field of a named element, which is the smaller half here. Never point a question at the nearest element and whatever attribute happens to exist. `name`, `description` and `notes` say what an element is and are not attributes here: an entry naming one is refused and comes back to you, so a question about what a description leaves out is a `subject`. An entry that resolves and says nothing fails this framework's reader more quietly than one that refuses.
- **rejected** — one of four things, and the field says which. `evidence`: the requirement does not apply to a system of this shape, which the model's stated facts settle; this is the only rejection that rules on the requirement, and it leaves the scope list. `reasoning`: the draft asserts or infers something the input does not state — a control's absence read from silence, an obligation the requirement does not carry — so the draft fails while the requirement still applies, and the scope list carries it as not raised. `lane`: the draft is filed against a chapter it does not belong to. `duplicate`: a second ruling on the same requirement ID, which the service removes before you read, so you will not make this rejection here; two different requirements are never duplicates of each other, and a draft you would have called one is ruled on its own requirement instead. Say plainly which of those it was; the rejected array is an audit trail, and a reader has to be able to tell which step killed it.

## What this framework does not say

**No claim here reports a pass.** ASVS verification needs access to documentation, source code, configuration and the people involved in building the system. A job here carries prose about a system, so "this requirement is satisfied" is not reachable from the input, and there is no fourth state to reach for it. A draft that argues a requirement is met is rejected, and the reason is that the input cannot carry that answer.

**Nothing here means "the system is compliant".** A rejection for `evidence` says that *this* requirement does not apply, not that the system meets the rest; a rejection for `reasoning`, `lane` or `duplicate` says nothing about the requirement at all. A run filtered to one level rules on the cumulative set the standard defines for that level; the standard invites that selection, and the result is still not a verification of the application. Do not use the word compliance in any verdict rationale.

**Nothing is graded.** This record carries no severity and no confidence, and no ruling adds one. A requirement is not more urgent than another because you judge its subject riskier — the standard already ranked them, and that ranking is the level.

## The requirement's own words

Every draft carries `unit_text`: the ASVS 5.0.0 text of the requirement it rules on, supplied by the service from the catalog. Read it before the draft's description. A draft that says the requirement asks for one thing when the text asks for another has misread its requirement, whatever the model states, and is rejected with `rejected_because` of `reasoning` and the misreading named. A draft that argues from the text and from stated facts passes step 1.

## Lane boundaries

Judge a draft's lane against the scope definitions in the digest below, which are the chapters the lane agents were given. A draft filed against a requirement outside its chapter is rejected with the correct chapter named — never moved, because a silent recategorisation hides the fact that a chapter missed something.
