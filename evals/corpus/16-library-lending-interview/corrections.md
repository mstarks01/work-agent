# Bootstrap → blessed corrections: 16-library-lending-interview

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:entity:member>process:lending-api>borrow-renew-and-reserve.authentication` | `a token the app gets at sign-in` | `unknown`, hedge kept in `notes` | The bootstrap recorded the hedge as the value. Priya says "I think it checks a token the app gets at sign-in, but I'd have to look" and then "Honestly I could not tell you today what it accepts". That is a speaker who talks about a fact and does not state it. The confident value suppresses the spoofing must-find, which rests on the unknown. |
| 2 | `flow:process:self-service-kiosk>store:loans-database>write-loans` | present | removed | Invented from the inside of a question. Dan asked "Do the kiosks still write straight into the loans database, the way the old ones did?" and Priya sent the question to Dev's team. A question states no fact, so the model keeps the unanswered question in `process:self-service-kiosk.notes` and holds no flow. |
| 3 | `store:reservations-database` | present | removed | Invented from the half-sentence Priya corrected in the same turn: "It writes to the two databases— actually, no. We merged those in the spring. It's one loans database now." The later statement stands, and the retracted one produces no element. |
| 4 | `entity:regional-interlibrary-scheme` and `flow:process:lending-api>entity:regional-interlibrary-scheme>share-loans` | present | removed | Invented from a hypothetical: "If we ever join the regional interlibrary scheme, we'd have to stand something up for the other libraries, but nothing like that exists today." A hypothetical produces no element. |
| 5 | `process:lending-api.exposure` | `internet-facing` | `unknown`, both claims in `notes` | The bootstrap believed the transcript alone. The note states "The lending API is internal-only" and Priya states "The phones talk straight to the lending API over the internet." Two positive claims disagree, so the value is `unknown` and both claims are quoted beside their labels. The note states that the transcript is more recent, and recency still does not settle the disagreement. |

## Signal

Every correction is a failure of a conversational rule in `prompts/extract.md`,
which is what this case was written to grade. Corrections 1 to 4 are rules 1
to 4: a hedge became a value, a question became a flow, a retracted
half-sentence became a store, and a hypothetical became an entity and a flow.
Correction 5 is rule 6, on `exposure`, an attribute that can hold `unknown`.
It is the only correction where both sources are right about what they say and
the model still must not pick one.

Three of the five are element-level (2, 3, 4), so the extraction score sees an
invented flow, store or entity as an element that the blessed model does not
hold. The two attribute-level corrections (1, 5) are on scored attributes, so
the extraction score sees them on any element both models carry. Each is also
graded end-to-end by a must-find reference threat that rests on the unknown:
the spoofing claim on the member flow, and the elevation-of-privilege claim on
the lending API. A value of `internal` for exposure, taken from the note, is
the reading that suppresses the second one.
