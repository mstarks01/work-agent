# Vendor parity

What a change to one **Vendor** row owes every other row, and how the suite checks it.

A **Vendor** row has the same failure mode as a **Framework Package**: a
constant, a branch, or a table entry that is absent or short, and none of them
raises.

**A one-vendor assumption is vacuously correct when it is written and silently
wrong afterwards.** A single constant such as `SERVED_TRUST =
"provider_reported"` is true for one vendor and only looks true for two.

`tests/test_vendor_neutrality.py` is the mechanism, in three layers, and each
catches what the others cannot:

- **Completeness.** Every module-level table keyed by a vendor vocabulary is
  found by reading the modules, not by listing the tables, and must answer for
  every vendor — *including a table added tomorrow*. A `Vendor` field may carry
  no default, because a default is how a new row stays silent about a fact.
- **Declaration.** A vendor named outside the registry must say why, as a
  property of the vendor rather than as its name.
- **Property.** Completeness cannot see a wrong value: a table such as
  `_FORM_RULES` can hold every key and still hold a wrong entry. Those tests sit
  beside the rules they check.

**Keep the guards runnable on a half-built registry.** A collection-time
`CREDENTIAL_MODES[name]` makes a partly-added row an import error, so the suite
cannot reach the module whose message names the missing entry. Use `.get`. A guard that cannot run when the tree is half-built helps nobody.

**A vendor row makes claims about a third party, which a framework never does.**
`served_trust` is a claim about what litellm reads; whether `gpt-4o` is an alias
is a claim about OpenAI's catalogue. Drive the real dependency where CI can
(`test_identity.py` drives the installed translator), and where it cannot,
record the measurement beside the code rather than asserting it in prose.
