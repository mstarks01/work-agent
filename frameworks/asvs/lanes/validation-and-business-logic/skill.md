# Validation and Business Logic (V2)

## Scope

Chapter V2 of ASVS 5.0: input validation, business-logic integrity and anti-automation. Your lane covers whether the application decides what it will accept, whether that decision is made where an attacker cannot reach it, and whether a business flow can be run out of order, replayed, or driven faster than a human could drive it.

Chapter boundaries: what happens to accepted data at an interpreter is chapter V1. Who is allowed to run a flow is chapter V8. How a session is held across a flow's steps is chapter V7. Your subject is the rule that says what a valid request is.

## Applicability

This chapter applies to every application. Two of its requirements need more: a validation rule enforced on the client alone needs code that runs on an untrusted side, and the sequencing requirements need a flow with more than one step.

Read the model for both. A `technology` naming a browser framework or a mobile app answers the first. A `description` naming a checkout, an onboarding sequence or an approval workflow answers the second. Where neither appears, say so and rule the requirement out on that fact.

### The requirements of this chapter

13 requirements across 4 sections: 4 at level 1, 7 at level 2, 2 at level 3. Rule on every one at or below the level the scope line names, and on no other. The `(L…)` tag is the requirement's own level, and the pair after the chapter number is what you put in `requirement`.

#### V2.1 Validation and Business Logic Documentation

- **V2.1.1** (L1) — Verify that the application's documentation defines input validation rules for how to check the validity of data items against an expected structure. This could be common data formats such as credit card numbers, email addresses, telephone numbers, or it could be an internal data format.
- **V2.1.2** (L2) — Verify that the application's documentation defines how to validate the logical and contextual consistency of combined data items, such as checking that suburb and ZIP code match.
- **V2.1.3** (L2) — Verify that expectations for business logic limits and validations are documented, including both per-user and globally across the application.

#### V2.2 Input Validation

- **V2.2.1** (L1) — Verify that input is validated to enforce business or functional expectations for that input. This should either use positive validation against an allow list of values, patterns, and ranges, or be based on comparing the input to an expected structure and logical limits according to predefined rules. For L1, this can focus on input which is used to make specific business or security decisions. For L2 and up, this should apply to all input.
- **V2.2.2** (L1) — Verify that the application is designed to enforce input validation at a trusted service layer. While client-side validation improves usability and should be encouraged, it must not be relied upon as a security control.
- **V2.2.3** (L2) — Verify that the application ensures that combinations of related data items are reasonable according to the pre-defined rules.

#### V2.3 Business Logic Security

- **V2.3.1** (L1) — Verify that the application will only process business logic flows for the same user in the expected sequential step order and without skipping steps.
- **V2.3.2** (L2) — Verify that business logic limits are implemented per the application's documentation to avoid business logic flaws being exploited.
- **V2.3.3** (L2) — Verify that transactions are being used at the business logic level such that either a business logic operation succeeds in its entirety or it is rolled back to the previous correct state.
- **V2.3.4** (L2) — Verify that business logic level locking mechanisms are used to ensure that limited quantity resources (such as theater seats or delivery slots) cannot be double-booked by manipulating the application's logic.
- **V2.3.5** (L3) — Verify that high-value business logic flows require multi-user approval to prevent unauthorized or accidental actions. This could include but is not limited to large monetary transfers, contract approvals, access to classified information, or safety overrides in manufacturing.

#### V2.4 Anti-automation

- **V2.4.1** (L2) — Verify that anti-automation controls are in place to protect against excessive calls to application functions that could lead to data exfiltration, garbage-data creation, quota exhaustion, rate-limit breaches, denial-of-service, or overuse of costly resources.
- **V2.4.2** (L3) — Verify that business logic flows require realistic human timing, preventing excessively rapid transaction submissions.

## Threat Patterns

The recurring ways this chapter's requirements go unanswered in a system description. Each names what to look for; none is a finding on its own.

**Validation is described but not located.** The input says requests are validated and never says which side enforces it. A rule an untrusted client applies is not a control, and the requirement stays open until the input names the enforcing side.
**Documented rules are assumed rather than stated.** Several requirements here verify that a validation policy exists as a document. No representation of a running system holds that answer, so these are needs-info by construction — say that plainly rather than treating the silence as a failure.
**A multi-step flow has no stated ordering control.** The input describes stages and says nothing that prevents a later stage being called first. Name the stages the model does record.
**Anti-automation is unmentioned.** Rate limits and bot controls are rarely in prose. Where the model shows an internet-facing process, the requirement applies and the input does not settle it.

## Guardrails

- **Rule the requirement, do not restate it.** A claim whose description repeats the published text has said nothing about this system. Name the fact of *this* model that makes the requirement apply, and what the input does or does not show about it.
- **Unknown is not absent.** When an attribute reads `unknown`, the control is unverified. Write the ruling conditionally, cite the element and the attribute, and let the critic mark it needs-info. An attribute reading `none` is the opposite: the submitter answered, so write that ruling plainly.
- **Never report a pass.** The input carries prose, not source code or configuration, so "this requirement is satisfied" is not a conclusion available to you. Where the input describes a control that looks sufficient, say what it describes and what remains unverified.
- **Never use the word compliance.** This run rules on applicability, and a level-filtered run covers a subset of the standard. Neither is a compliance result.
- **Stay in the model.** Reference only element IDs the System Model carries. A requirement about a coding practice has no position in the graph — leave `affected_element_ids` empty rather than reaching for the nearest element.
- **One ruling per requirement.** Do not merge two requirements whose subjects are close: the standard separated them, and a reader cites them separately.

## Mitigations

This record carries no mitigations, and that is a decision rather than an omission: **the requirement text is the remedy**. A reader who wants to know what to do reads the requirement your claim cites, in the published standard, at the version your claim's ID names.

So do not write a countermeasure into the description. What belongs there is what the requirement's subject looks like *in this system* — which element, which attribute, which stated fact — because that is what the standard's text cannot supply and what makes the citation actionable.

Where a ruling is needs-info, write **the question**: the one fact the submitter could supply that would settle it.
