# Bootstrap → blessed corrections: 14-retail-loyalty-interview

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:customer-to-points-api:collect-and-redeem-points.authentication` | `a token the app gets at sign-in` | `unknown`, hedge kept in `notes` | The bootstrap recorded the hedge as the value. Priya says "I think it checks a token the app gets at sign-in, but I'd have to look" and then "Honestly I could not tell you today what it accepts" — a speaker talking about a fact, not stating one. The confident value suppresses the spoofing must-find, which rests on the unknown. |
| 2 | `flow:store-kiosk-to-points-db:write-points` | present | removed | Invented from the inside of a question. Dan asked "Do the kiosks still write straight into the points database, the way the old fleet did?" and Priya deflected to Dev's team. A fact stated inside a question is not a fact; the probed gap is kept in `process:store-kiosk.notes` instead of as a flow. |
| 3 | `store:offers-db` | present | removed | Invented from the half-sentence Priya corrected mid-turn: "It writes to the two databases— actually, no. We merged those in the spring. It's one points database now." The corrected statement stands; the retracted one produces no element. |
| 4 | `entity:coffee-chain` and `flow:points-api-to-coffee-chain:redeem-points` | present | removed | Invented from a hypothetical: "If we ever let the coffee chain redeem points in their app, we'd have to stand something up for them, but nothing like that exists today." A hypothetical produces no element. |
| 5 | `process:points-api.exposure` | `internet-facing` | `unknown`, both quotes in `notes` | The bootstrap believed the transcript alone. The note states "The points API is internal-only" and Priya states "The phones talk straight to the points API over the internet." Two sources carry equal weight, so the disagreement is recorded rather than adjudicated: the value flattens to `unknown` and both claims are quoted beside their labels. Recency is stated in the note and is still not adjudication. |

## Signal

Every correction is a conversational-rule failure, which is what this case was
authored to grade. Corrections 1 through 4 are the transcript rules from #53:
a hedge became a value, a question's premise became a flow, a retracted
half-sentence became a store, and a hypothetical became an entity and a flow.
Correction 5 is the two-source conflict rule from #55, on `exposure` — the one
attribute the audit there named flattenable — and it is the only correction
where both sources are right about what they say and the model still must not
pick one.

Three of the five are element-level (2, 3, 4), which is deliberate:
`score_extraction` is attribute-blind, so an invented flow, store or entity is
the only shape of this failure the extraction score can see. The two
attribute-level corrections (1, 5) are graded end-to-end instead, each by a
must-find reference threat that rests on the flattened unknown.
