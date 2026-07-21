# Corrections: 10-cookbook-generic-cms

Bootstrap → blessed diff, per `BLESSING.md` step 3. The candidate came from an
agent stand-in running `prompts/extract.md` (no Vertex credentials here — see
`case.json`'s `bootstrap` field and wayfinder ticket 030), so what follows is
signal about the prompt, not about the pinned Flash model.

The candidate came back at 14 elements, inside the band, and mechanically
invalid on 5 `id-mismatch` errors. Its structural reading was right where this
case is hardest: it typed the CDN as an external system and its bucket as a
separate data store in the CDN's own zone, which is what makes the asset-publish
push a boundary crossing rather than an internal write.

## Corrections

### 1. The stated control stranded in `data_description`

- **Path:** `flow:reader-to-web-server:page-requests`
- **Bootstrap:** `authentication: "unknown"`, with "sign-in state for some
  readers" in `data_description`
- **Blessed:** `authentication: "sign-in exists for some readers; the mechanism,
  its strength and how sessions are handled are not stated"`, and
  `data_description` narrowed to "Page requests and rendered site content"
- **Source reason:** "some of them signed in" states that an authentication
  mechanism exists on this link. `unknown` says the opposite thing to an
  analyst — it says the control is unverified — and it loses the one fact the
  source offers about how readers are identified. The blessed value keeps both
  halves: sign-in exists, everything about it is unstated.

This is checklist item 4 in the direction the corpus has not recorded before.
See Signal.

### 2. Credentials tagged where the source says accounts

- **Path:** `store:mysql-database`, `entity:admin`
- **Bootstrap:** the database tagged `["pii", "credentials",
  "business-critical-data"]`, the admin tagged `["credentials"]`
- **Blessed:** `["pii", "business-critical-data"]` and `[]`
- **Source reason:** the source says the database holds "pages, accounts and
  comments". What an account record contains is never stated, and sign-in
  existing does not establish where its secrets rest. The admin tag is the
  clearer error: the source says nothing whatever about a credential the admin
  holds, and an external entity is not where data rests. Both are the pattern
  cases 07, 08 and 09 record — the tag follows what the element is called.

Dropping the tag does not weaken the claims that need it. The direct-admin path
is grounded by the flow, not by a tag on the person at the end of it.

### 3. Flows carrying account data tagged empty

- **Path:** `flow:web-server-to-mysql-database:cms-data`,
  `flow:admin-to-mysql-database:direct-administration`
- **Bootstrap:** `assets: []` on every flow in the model
- **Blessed:** `["pii", "business-critical-data"]` on both database flows
- **Source reason:** the same pattern from the other side, and the one cases 07
  and 08 found in both directions. The candidate tagged the store that holds
  accounts and comments but left every flow that carries them empty, including
  the unencrypted one — which is the flow an analyst most needs to see tagged,
  because it is the only place the account data is stated to move in clear text.

### 4. Assumptions recorded for facts the source states

- **Path:** `assumptions`
- **Bootstrap:** five entries, including "Readers are outside the hosted
  network" and "The CDN bucket is part of the CDN's zone rather than the hosted
  network"
- **Blessed:** three entries; those two dropped
- **Source reason:** the source says "Public site: readers hit the web server"
  and calls the bucket "the CDN's bucket". Both zone placements are read, not
  inferred, and the prompt reserves `assumptions` for values inferred rather
  than stated. The cost of over-recording is not cosmetic: the assumptions list
  is what a reviewer scans to find the model's soft spots, and padding it with
  restatements of the text buries the entry that matters — that the admin's
  location is genuinely inferred from "from wherever they happen to be".

The three kept entries are all real inferences: the web server's
internet-facing exposure, the admin's zone, and the CDN being separately
operated.

### 5. Five ID mismatches

- **Path:** every flow
- **Bootstrap:** IDs such as `flow:reader-to-web-server:page-requests` beside
  `name: "Reader page requests"`
- **Blessed:** names shortened to the label the ID already carried
  (`"Page requests"`), so `make_flow_id` reproduces each ID exactly
- **Source reason:** none — mechanical. See case 09's Signal section, which
  carries the count across all three candidates.

## Signal

**The `unknown` rule is asymmetric in practice.** Every correction the corpus
has recorded on the five governed attributes until now ran one way: a stated
*absence* — "the runner does not verify signatures", "not HTTPS", "nobody
checks" — either reaching the attribute or being stranded in prose. This case is
the first where the stranded fact is a stated *presence*. The prompt's framing
may be doing it: "`unknown` is the default, not the fallback" is written entirely
around the danger of inventing a control, and the model appears to read a hedged
positive ("some of them signed in") as insufficient to leave `unknown` — while
routing it into `data_description`, where it is invisible to an analyst.

The consequence is the mirror image of the one the prompt guards against, and it
is not harmless: `authentication: unknown` on the site's only reader-facing link
invites a spoofing finding the source partly answers, and it hides the finding
that is actually available — that sign-in exists and nothing about its strength
is known. Worth a prompt experiment: the rule as written tells the model what to
do when the text says nothing, and says nothing about the text saying something
vague.

That refines rather than confirms the in-sentence hypothesis case 08 raised.
"Some of them signed in" sits in the same sentence as the behaviour it
qualifies and still did not land, so proximity is not the variable. What
separates the four observations is whether the qualifier is phrased as a
property of the *link* ("not HTTPS", "no TLS on it" — both landed) or as a
property of the *actor* ("some of them signed in" — did not). Case 07's miss
fits that reading too: "the runner does not verify signatures" is phrased about
the actor at one end, not about the connection.
