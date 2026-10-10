# Bootstrap → blessed corrections: 16-library-lending-interview

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:entity:member>process:lending-api>borrow-renew-and-reserve.authentication` | `a token the app gets at sign-in` | `unknown`, hedge kept in `notes` | The bootstrap recorded the hedge as the value. Priya says "I think it checks a token the app gets at sign-in, but I'd have to look" and then "Honestly I could not tell you today what it accepts". That is a speaker who talks about a fact and does not state it. The confident value suppresses the spoofing must-find, which rests on the unknown. |
| 2 | `flow:process:self-service-kiosk>store:loans-database>write-loans` | present | removed | Invented from the inside of a question. Dan asked "Do the kiosks still write straight into the loans database, the way the old ones did?" and Priya sent the question to Dev's team. A question states no fact, so the model keeps the unanswered question in `process:self-service-kiosk.notes` and holds no flow. |
| 3 | `store:reservations-database` | present | removed | Invented from the half-sentence Priya corrected in the same turn: "It writes to the two databases— actually, no. We merged those in the spring. It's one loans database now." The later statement stands, and the retracted one produces no element. |
| 4 | `entity:regional-interlibrary-scheme` and `flow:process:lending-api>entity:regional-interlibrary-scheme>share-loans` | present | removed | Invented from a hypothetical: "If we ever join the regional interlibrary scheme, we'd have to stand something up for the other libraries, but nothing like that exists today." A hypothetical produces no element. |
| 5 | `store:loans-database.data_classification` | `unknown` | `confidential` | The bootstrap left the field empty although Priya lists what the store holds: "Names, email addresses, home addresses and every member's full borrowing history live in the loans database." That is personal data, and the scheme in `prompts/extract.md` files personal data as `confidential`. Neither source states a local tier, so the value is the scheme word and no more. |
| 6 | `flow:entity:desk-staff>process:lending-api>change-loans-by-hand.authentication` | `one login shared by all desk staff in every branch; the password is on a card taped under the desk` | `password authentication using` the same words | The bootstrap described the account and did not name the mechanism, and rule 5 of `prompts/extract.md` names the mechanism first. Priya states both a shared login and "The password is on a card taped under the desk." Ruled with the reference facts on 2026-10-10. |

## Signal

Corrections 1 to 4 are each a failure of a conversational rule in
`prompts/extract.md`, which is what this case was written to grade: a hedge
became a value, a question became a flow, a retracted half-sentence became a
store, and a hypothetical became an entity and a flow. Corrections 5 and 6 are
not conversational rules: the bootstrap left a scored attribute `unknown`
against a list of what the store holds, and it described a shared account
without naming its mechanism.

The bootstrap read exposure correctly, so it carries no correction. The note
says the lending API is internal-only, and in the transcript Priya calls that
line wrong and says when it changed. The note describes the service before the
change, so the two sources make one claim about the service today, and rule 6
does not apply. The value is `internet-facing`.

Three of the corrections are element-level (2, 3, 4), so the extraction score
sees an invented flow, store or entity as an element that the blessed model
does not hold. The attribute-level corrections (1, 5, 6) are on scored
attributes, so the extraction score sees them on any element both models
carry. Correction 1 is also graded end-to-end by a must-find reference threat
that rests on the unknown: the spoofing claim on the member flow. A value of
`internal` for exposure, taken from the note, removes the internet caller from
the elevation-of-privilege claim on the lending API.
