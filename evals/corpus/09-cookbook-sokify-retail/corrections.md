# Corrections: 09-cookbook-sokify-retail

Bootstrap → blessed diff, per `BLESSING.md` step 3. The candidate came from an
agent stand-in running `prompts/extract.md` (no Vertex credentials here — see
`case.json`'s `bootstrap` field and wayfinder ticket 030), so what follows is
signal about the prompt, not about the pinned Flash model.

The candidate came back at **21 elements**, one over the band, and mechanically
**invalid** — 14 `id-mismatch` errors. Neither is a recall failure; both are
covered under Signal below.

## Corrections

### 1. The operator modelled beside the tool

- **Path:** `entity:marketing`,
  `flow:marketing-to-catalogue-spreadsheet:catalogue-maintenance`
- **Bootstrap:** marketing staff as a human external entity in the office zone,
  with a flow into the spreadsheet they maintain
- **Blessed:** both dropped; "Marketing keep the catalogue in a spreadsheet"
  survives in `process:catalogue-spreadsheet`'s description
- **Source reason:** the source names both, so this is not the class/instance
  duplication case 08 hit. It is the operator/tool pair, and the test is which
  one carries stated facts. Every security-relevant fact in that paragraph
  attaches to the spreadsheet — the macros, the SQL, the stopgap, the laptop,
  the office. The staff's only described interaction is *using* it, and the flow
  modelling that use carried no stated attribute: protocol, authentication and
  encryption were all `unknown` because the source says nothing about them.

The 2-element saving is what brought the model into the band, but the drop is
not a concession to the band: an element whose every attribute is unknown and
whose only interaction is "a person uses their own tool" adds a spoofing and a
tampering target that no sentence in the source supports.

### 2. Payment capture invented on the customer path

- **Path:** `flow:customer-to-mobile-app:browse-and-order`,
  `flow:mobile-app-to-web-api:api-traffic`, `process:mobile-app`
- **Bootstrap:** `data_description` of "Browsing activity, order details, and
  payment card details" and "including customer details and the card paid with";
  the app tagged `financial`
- **Blessed:** "Browsing activity and order details" and "Order placement
  traffic carrying customer details"; the app tagged `pii` only
- **Source reason:** the source states exactly one thing about cards — the
  *database* holds "the card they paid with". It never says the app collects
  them, never says they cross the HTTP link, and never says where payment
  happens at all. The candidate's reading is plausible and may well be true of
  the real system, which is exactly what makes it the damaging kind of error:
  an analyst reading `financial` on the app files a card-interception finding on
  the plain-HTTP leg, and nothing in the user's own words backs it.

`pii` stays on the app and on both flows, on narrower grounds recorded in the
element's `notes`: the source rules out a website, so the app is the only stated
route by which a customer's name and address can reach the API.

### 3. Asset tags on the person

- **Path:** `entity:customer`
- **Bootstrap:** `assets: ["pii", "financial"]`
- **Blessed:** `assets: []`
- **Source reason:** the third instance of the pattern cases 07 and 08 recorded.
  Asset tags mark what an element *carries*, and a customer is not a data
  store — the corpus's other human entities carry no tags. The customer's name
  and address are protected where they rest and where they move, which is the
  database, the flat file, the fax and the flows, all of which are tagged.

### 4. A stated absence widened into a general one

- **Path:** `flow:fax-gateway-to-customer:confirmation-fax`
- **Bootstrap:** `authentication: "none — nobody checks it arrived at the right
  place"`
- **Blessed:** `authentication: "the dialled destination is never verified;
  nobody checks the fax arrived at the right place"`
- **Source reason:** the candidate got the important half right — the fact
  reached the attribute an analyst reads rather than being stranded in an
  excerpt, which is the failure mode this corpus most often records. What it got
  wrong is the scope. The source states that the *destination* is unverified. It
  does not state that the leg has no authentication of any kind, and
  `authentication: none` is the invented absence `BLESSING.md` step 3 calls
  worse than an invented control, because a confident "no authentication" reads
  as a verified fact.

The fix is precision, not deletion. The blessed value keeps the stated absence
and drops the generalisation.

### 5. Fourteen ID mismatches

- **Path:** every flow, three processes, two boundaries
- **Bootstrap:** IDs such as `flow:mobile-app-to-web-api:api-traffic` beside
  `name: "Mobile app to web API"`, and `process:sims` beside
  `name: "SIMS stock and inventory system"`
- **Blessed:** names shortened to the label the ID already carried
  (`"API traffic"`, `"SIMS"`), so `make_element_id` and `make_flow_id` reproduce
  each ID exactly
- **Source reason:** none — this is mechanical, not a reading of the source. The
  candidate chose sensible IDs and sensible names, and the two disagreed. See
  Signal.

## Signal

**The invented fact arrived through `data_description`, not through an
attribute.** Every one of the five security-relevant attributes the prompt names
was handled correctly on the customer path: protocol, authentication and
encryption stayed `unknown` where the source is silent, and the HTTP link's
missing TLS reached `encryption_in_transit` intact. The card details walked in
through the one free-text field the prompt's `unknown` rule does not govern, and
then propagated into an asset tag. This is worth stating because the prompt's
whole defence is built around the enumerated attributes: `data_description` and
`assets` are downstream of the same failure and have no equivalent guard.

**Both stated absences landed in the attribute.** "Over HTTP — not HTTPS" and
"nobody checks it arrived at the right place" each reached the attribute an
analyst reads rather than being stranded in an excerpt — the second one
over-widened, but present. That is the failure case 07 recorded not recurring
here. It is *not* the confirmation of case 08's in-sentence hypothesis it looks
like: case 10, blessed in this same session, has a qualifier that sits
in-sentence and still fails to land, and its Signal section reworks the
hypothesis around what the qualifier is phrased about rather than where it sits.

**`id-mismatch` is now three candidates out of three.** Case 08's candidate had
2, this one has 14, and case 10's has 5 — the prompt describes the ID rule
correctly and the model applies it to a *shorter* name than the one it then
emits. Nothing about the reading of the source is wrong when this fires, and one
repair pass is currently spent on it. The mechanical fix proposed on ticket 029
last session — derive the ID in code from the emitted name rather than asking
the model to keep two fields in agreement — would have removed 21 of the 21
errors seen across the three candidates. That is a `prompts/extract.md` change
avoided, not a prompt change deferred.
