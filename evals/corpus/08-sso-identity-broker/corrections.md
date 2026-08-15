# Corrections: 08-sso-identity-broker

Bootstrap → blessed diff, per `BLESSING.md` step 3. The candidate came from an
agent stand-in running `prompts/extract.md` (no Vertex credentials here — see
`case.json`'s `bootstrap` field and wayfinder ticket 030), so what follows is
signal about the prompt, not about the pinned Flash model.

This is the first case in the corpus whose candidate came back **outside the
8–20 element band** — 23 elements — and the three corrections that brought it to
19 are all modelling errors that would have been worth making anyway. The band
did not force a judgement call; it surfaced one.

## Corrections

### 1. A response modelled as its own flow

- **Path:** `flow:identity-broker-to-colleague:token-issuance`
- **Bootstrap:** a separate flow from the broker back to the colleague, carrying
  the issued token
- **Blessed:** dropped; its facts fold into
  `flow:colleague-to-identity-broker:sign-in`'s `data_description`
- **Source reason:** CONTEXT.md is explicit — one flow per interaction,
  direction is who initiates, and the response rides implicitly. The colleague
  initiates a sign-in and the token comes back on that same interaction. This is
  checklist item 2 in its less obvious form: the candidate got both genuine
  who-initiates traps right (the nightly HR pull, the provider's assertion) and
  then invented a flow out of a response.

The fold is load-bearing rather than cosmetic. "Good for twelve hours" and "no
way to pull one back" lived only on the dropped flow, and they are the stated
blast radius of every spoofing claim in this case, so losing them with the flow
would have quietly removed two references' worth of grounding.

### 2. A nameless generic process duplicating the named one

- **Path:** `process:relying-application`,
  `flow:colleague-to-relying-application:token-presentation`
- **Bootstrap:** a generic relying application alongside
  `process:store-admin-console`, each with its own token-presentation flow
- **Blessed:** both dropped; the public-key fetch retargeted to
  `flow:store-admin-console-to-identity-broker:fetch-public-key`, and the
  collective facts recorded in the console's `notes`
- **Source reason:** the source describes a class ("every application takes that
  token") and names exactly one member of it. Modelling both gives two elements
  for one described behaviour and two flows for one described interaction, which
  double-counts every claim an analyst files against either. The named instance
  is the one carrying stated facts, so it is the one that survives.

### 3. The franchise colleague, and an interaction the source does not describe

- **Path:** `entity:franchise-colleague`,
  `flow:franchise-colleague-to-franchise-identity-provider:sign-in`
- **Bootstrap:** both present, the flow carrying an assumption admitting the
  source does not describe it
- **Blessed:** both dropped, and the fact recorded in the provider's `notes`
- **Source reason:** the interaction is between two parties we do not run, and
  the candidate's own assumption says the text does not describe it. An element
  invented to hold an assumption about a thing outside the system is not an
  extraction. What the source actually states is that the *provider* vouches and
  the broker accepts, which is the flow that survives.

### 4. Trust drawn on a network where the source draws it on a party

- **Path:** `entity:franchise-identity-provider.trust_zone`
- **Bootstrap:** `boundary:outside-corporate-network`, shared with colleagues at
  home and with the HR system
- **Blessed:** a new `boundary:franchise-partner` of kind `tenant`
- **Source reason:** "We also let the franchise stores in" introduces a distinct
  party admitted on distinct terms, and collapsing it into one outside-zone
  makes the franchise crossing indistinguishable from a colleague signing in
  from home. In a case whose whole subject is trust granted to parties rather
  than to network positions, that is the one distinction the zones have to keep.
  The kind follows from the same sentence: a franchise runs its own identity
  provider, so a different party controls the zone, which is what `tenant`
  means. `other` would say the zone resists all three characterizations, and
  this one states its own in the source's own words.

### 5. Two invented asset tags

- **Path:** `flow:...:fetch-public-key.assets`, `store:directory.assets`
- **Bootstrap:** `["secrets"]` on the public-key fetch; `["pii", "credentials"]`
  on the directory
- **Blessed:** `[]` and `["pii"]`
- **Source reason:** the flow carries the *public* half, which the source says so
  in as many words; tagging it `secrets` inverts the one fact stated about it.
  The directory is stated to hold colleagues and the groups they are in — the
  source never puts credentials there, and `credentials` on a directory is the
  kind of tag driven by what an element is *called* rather than by what the text
  says is in it, which is checklist item 6 exactly.

### 6. Two IDs that are not the deterministic slug of their own name

- **Path:** `store:directory` named "Broker directory";
  `boundary:outside-corporate-network` named "Outside the corporate network"
- **Bootstrap:** as above — IDs that do not round-trip through
  `make_element_id`
- **Blessed:** names shortened to "directory" and "outside corporate network",
  keeping the IDs the reference set is written against
- **Source reason:** none — this is the mechanical gate, not a reading of the
  source. Worth its own entry rather than filing under cosmetics: `id-mismatch`
  is one of the four `parse_and_validate` codes, so in production this pair
  would have failed the validity gate and spent the one repair pass on a naming
  slip. It is the first time in this corpus that a candidate has produced a
  model the validator actually rejects.

### 7. Cosmetic: element names and empty keys

Title-cased names lower-cased to match the rest of the corpus, empty `notes`
keys dropped. No source reason; recorded so the diff is complete.

## Considered and not corrected

- **`process:store-admin-console.exposure` stays `unknown`.** A colleague in the
  outside zone has a flow to it, which makes it tempting to write
  `internet-facing`. The source never says where colleagues use the console
  from — only where they sign in from — so the honest value is `unknown`, and
  the derived crossing carries the fact that an outside actor reaches it.
- **The HR system stays outside the corporate network.** The source never places
  it. Grouping it with the other unplaced parties rather than assuming it
  internal is the safer direction, and it is on the record as an assumption.
- **Both genuine who-initiates traps came back right.** The broker *pulls* from
  HR, and the franchise provider *presents* an assertion inward. Neither was
  reversed.
- **Tampering with a token's claims is not a reference.** Tokens are signed, so
  altering claims requires the key, and that is already the spoofing claim. A
  candidate that files "attacker edits the groups in the token" is making a
  claim this model does not support.

## Signal

**The candidate over-produces elements where the source generalizes.** All three
element drops are the same reflex from different angles: a response became a
flow, a class became a process beside its own named instance, and an
out-of-scope actor became an entity to hang an assumption on. None is a missing
fact — the candidate's recall was good — and every one of them adds an element
that no stated fact needs. On a larger system this is the failure mode that puts
a model through the 150-element admission cap for reasons that are not about the
system's size, and it inflates every per-element denominator the scorer computes.

**Asset tags are still driven by names rather than by stated content**, which is
correction 5 here and correction 1/3 in
[case 07](../07-cicd-store-deploy/corrections.md). A key-shaped flow got
`secrets` when the source says it carries the public half; a directory got
`credentials` because directories usually hold them. Two cases in a row is a
pattern worth carrying into any `prompts/extract.md` change.

**And it produced a model the validator rejects.** Correction 6 is the corpus's
first `id-mismatch`, and it is the cheapest possible finding: the ID is a pure
function of the name, so the candidate had everything it needed to get it right
and still emitted a slug of a shorter name than the one it wrote. In production
this consumes the single repair pass — the budget that exists for genuine
extraction failures — on a naming slip. Ticket 030's re-bootstrap should count
how often the real `extract` node does this, because if the rate is material the
fix is mechanical (derive the ID in code from the emitted name) rather than a
prompt change.

**The stranded qualifier did not recur in its usual form** — the console's
stated authorization gap reached the flow's `authentication` attribute intact,
which is the first time in this corpus a stated qualifier of that weight has
landed where an analyst reads it. Worth noting precisely because it is the
corpus's most repeated failure everywhere else: whatever is different about this
source (the qualifier sits in the same sentence as the behaviour it qualifies)
is worth knowing when the re-bootstrap runs.
