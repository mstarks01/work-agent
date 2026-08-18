# Review sitting 01 — are these the same threat?

> ## Result, recorded 2026-08-18
>
> **25 `same`, 1 `different`, 4 `unclear`.** Five of thirty, under the six-of-thirty
> line this document set, so the labels hold at their weakest point and the
> duplicate-threat work stands on the evidence it has. The floor is soft, not
> firm: 17% at the hard end.
>
> Two defects came out of it, and both are fixed:
>
> - **Pair 16 exposed an unsupported reference claim.** Case 04's
>   information-disclosure claim asserted the model emits "customer features **or
>   training data**". No training pipeline exists in that case, and the label set
>   already rules training-time attacks out of scope for it — so the corpus graded
>   the tool against a fact its own model does not hold. The claim is corrected.
> - **Pair 2 was a wrong label.** A game server writing fabricated progression and
>   a client reporting fabricated results were labelled one claim. Different
>   component, different entry point, different remedy. Relabelled `no-match`,
>   which is why the match set is 200 and not 201.
>
> **All four `unclear` answers share one cause:** one side carried specificity the
> other lacked. That is what step 5 of `BLESSING.md` rules on, and the sitting
> shows the rule was not readable. Step 5 now states the test explicitly.
>
> **A mechanical check for the pair-16 defect was tried and abandoned.** Flagging
> a claim that uses a word absent from its case's source and model fires on 231
> of 243 claims: a claim is *supposed* to describe an attack in words the system
> description never uses. Narrowing to the asset vocabulary fails too — training
> data falls under `business-critical-data`, which that case already carries. So
> this class of defect is not mechanically detectable, and a reading session is
> the only thing that finds it. That is the argument for step 6.

**Nobody has ever reviewed anything under `evals/`.** The 339 pairs the eval
suite scores against were written by an agent, and the judge model has been
graded against them since July. This is the first check on whether any of it is
sound.

You are reading 30 pairs. They are not a random sample. They are the 30 where
the two write-ups point at the *most different* parts of the system while an
agent still labelled them the same threat. That is where a bad label would hide,
so if these hold, the easy ones almost certainly hold too.

## The question, for each pair

> Do **A** and **B** describe **one threat that should appear once in a report**,
> or **two different threats that should both appear**?

Write `same` or `different` on the answer line. If you genuinely cannot tell from
the two sentences alone, write `unclear` — that is a real answer and it counts.

## The one rule

**Do not read Part 2 until you have answered all 30.** Part 2 holds what the
agent decided and why. If you read it first you will agree with it, and the
sitting is worthless.

Roughly 30-40 minutes. Order of A and B is shuffled, so neither side is
consistently the "official" one.

---

## Part 1 — the pairs

### 1. 04-ml-inference-service · denial-of-service

**A.** A caller with a valid key sends enough inference requests to starve every other team of GPU capacity.

**B.** A caller holding a valid key floods the gateway with inference requests and exhausts the shared GPU capacity behind it.

> answer: same


### 2. 06-cookbook-online-game · tampering

**A.** An attacker who influences a game server writes fabricated progression onto player records.

**B.** An attacker modifies the game client to report fabricated match results.

> answer: different 


### 3. 02-iot-fleet-telemetry · repudiation

**A.** A customer disputes a reading attributed to their site and no per-device identity exists to establish which node actually sent it.

**B.** There is no way to tell which physical device produced a disputed reading, because every node presents the same credential.

> answer: unclear


### 4. 03-batch-data-pipeline · spoofing

**A.** An attacker with a valid partner key uploads a file to a folder belonging to a different partner.

**B.** An attacker deposits a file into another partner's folder and the scheduler processes it as that partner's extract, because it never checks the depositor.

> answer: same


### 5. 03-batch-data-pipeline · denial-of-service

**A.** An attacker deposits an enormous or malformed file that consumes the nightly window and prevents genuine extracts from being processed.

**B.** An attacker uploads a file large enough that the nightly job never finishes and genuine extracts go unprocessed.

> answer: same


### 6. 03-batch-data-pipeline · elevation-of-privilege

**A.** An attacker who can place a file in the landing bucket obtains code execution in the warehouse network via the job that processes it.

**B.** An attacker who can plant a file in the landing bucket gains execution in the warehouse network through the job it triggers.

> answer: same 


### 7. 05-cookbook-queue-webapp · spoofing

**A.** An attacker who reaches the queue enqueues jobs as if they came from the web application, since queue authentication is unverified.

**B.** An attacker who can reach the message queue writes job messages that the worker treats as coming from the web application.

> answer: same


### 8. 06-cookbook-online-game · repudiation

**A.** Nothing records which support agent performed a given moderation action on a player account.

**B.** A support agent's action on a player account cannot be attributed to them, because nothing records who performed a moderation change.

> answer: same


### 9. 06-cookbook-online-game · elevation-of-privilege

**A.** An attacker who gets any access to the moderation website acquires privilege over every player account it can reach.

**B.** An attacker who gets into the moderation website gains control over every player account it can act on.

> answer: same


### 10. 08-sso-identity-broker · elevation-of-privilege

**A.** Someone who has left keeps working access until the overnight sync catches up, and a token they already hold outlives even that.

**B.** A leaver keeps their access until the nightly pull runs, and a token issued before it goes on working for twelve hours after that.

> answer: unclear


### 11. 08-sso-identity-broker · elevation-of-privilege

**A.** An attacker who controls the franchise provider reaches internal applications on the corporate network, because a sign-in it vouches for is treated like any other.

**B.** An attacker who takes over the franchise provider signs in to our internal applications.

> answer: same


### 12. 09-cookbook-sokify-retail · tampering

**A.** An attacker changes the number stored against an order so the confirmation is faxed to a machine they control.

**B.** An attacker edits the fax number held against an order so the confirmation reaches a machine they control.

> answer: same


### 13. 11-sparse-shift-scheduling · repudiation

**A.** A manager who cut a colleague's hours denies having done it, and nothing records who made the change.

**B.** A store manager denies having made a rota change that disadvantaged a colleague, and nothing in the model records who changed what.

> answer: same


### 14. 12-overclaiming-supplier-portal · denial-of-service

**A.** Suppliers cannot file paperwork while the vendor's platform is down.

**B.** The vendor platform becomes unavailable and suppliers cannot file compliance paperwork while it is down.

> answer: same


### 15. 12-overclaiming-supplier-portal · elevation-of-privilege

**A.** A supplier signed in to the portal reaches compliance documents belonging to a different supplier.

**B.** A signed-in supplier reaches another supplier's compliance documents through the portal, because no separation between supplier tenants is stated.

> answer: same


### 16. 04-ml-inference-service · information-disclosure

**A.** A caller crafts an input that makes the model reveal another tenant's customer features in its output.

**B.** A caller crafts a request that makes the model emit customer features or training data belonging to a different tenant.

> answer: unclear


### 17. 01-payments-checkout · spoofing

**A.** Any workload that can reach the order service impersonates the storefront API on the unauthenticated gRPC channel and submits orders.

**B.** A compromised workload in the internal network calls the order service's gRPC endpoint, which accepts it without authentication.

> answer: unclear 


### 18. 01-payments-checkout · tampering

**A.** An attacker with the shared full read/write database account alters order rows, changing prices or payment status directly.

**B.** An attacker with database access rewrites order prices directly in orders-db.

> answer: same


### 19. 01-payments-checkout · elevation-of-privilege

**A.** An attacker with any foothold in the storefront tier gains order-writing privilege in the core zone, because the order service grants it on network position alone.

**B.** An attacker compromises the storefront API and moves into the core network.

> answer: same


### 20. 02-iot-fleet-telemetry · tampering

**A.** An attacker who can write to the firmware bucket plants a malicious image that every polling node installs, since image signature verification is unverified.

**B.** An attacker uploads a malicious firmware image to the bucket and every node that polls installs it.

> answer: same


### 21. 02-iot-fleet-telemetry · denial-of-service

**A.** An attacker floods the internet-exposed MQTT broker with connections until genuine nodes can no longer publish readings.

**B.** An attacker opens huge numbers of MQTT connections to the gateway so real devices cannot connect.

> answer: same 


### 22. 02-iot-fleet-telemetry · elevation-of-privilege

**A.** An attacker who physically takes one node extracts its credential and then acts with the authority of the entire fleet.

**B.** An attacker who compromises one physically accessible node uses its fleet-wide credential to act as the whole fleet against the ingest edge.

> answer: same


### 23. 03-batch-data-pipeline · tampering

**A.** An attacker who can write to the landing bucket alters claim records before the nightly run, and only the schema is checked before they reach the warehouse.

**B.** An attacker edits claim records sitting in the landing bucket before the 02:00 run picks them up.

> answer: same

### 24. 03-batch-data-pipeline · tampering

**A.** An attacker who can write to the Airflow metadata database rewrites a connection string to redirect the pipeline to infrastructure they control.

**B.** An attacker changes an Airflow connection string so the pipeline reads from or writes to a system they control.

> answer: same


### 25. 03-batch-data-pipeline · denial-of-service

**A.** An attacker crafts an extract that crashes the Spark job on every retry, so the warehouse silently stops being updated.

**B.** An attacker crafts input that makes the transform job fail repeatedly, leaving the warehouse stale without any request-level error surfacing.

> answer: same


### 26. 03-batch-data-pipeline · elevation-of-privilege

**A.** An attacker in the landing network reads Airflow's metadata database and obtains credentials for the systems downstream of it.

**B.** An attacker with a foothold in the landing network reads the metadata database and escalates to every credential the pipeline holds.

> answer: same


### 27. 04-ml-inference-service · spoofing

**A.** Any workload inside the model network submits inference requests posing as the gateway, which the model server accepts on network position alone.

**B.** Anything already inside the model network can send requests straight to the model server, which authenticates nothing.

> answer: same


### 28. 04-ml-inference-service · tampering

**A.** An attacker with access to the model network writes new values into Redis and changes the features the model scores on.

**B.** An attacker with model-network access writes to the unauthenticated Redis feature store and changes the features a decision is made on.

> answer: same


### 29. 04-ml-inference-service · denial-of-service

**A.** An attacker with model-network access issues a Redis command that wipes the feature store, stalling inference.

**B.** An attacker with model-network access flushes or fills the unauthenticated Redis store, stalling every request that needs features.

> answer: same


### 30. 05-cookbook-queue-webapp · tampering

**A.** An attacker who can write the web application's config points it at a queue they control.

**B.** An attacker who can write to the web application config changes the queue endpoint or credentials and redirects the application's work.

> answer: same

---

## Part 2 — what the agent decided

Only after all 30. Every one of these was labelled **same threat**; the note is
the reason the agent recorded at the time.

**1. 04-ml-inference-service · denial-of-service** — labelled `same`. Reason recorded: Same action, same target.

**2. 06-cookbook-online-game · tampering** — labelled `same`. Reason recorded: Same action and target as the reference's fabricated progression, reached through the client the model already marks untrusted.

**3. 02-iot-fleet-telemetry · repudiation** — labelled `same`. Reason recorded: Same claim, stated as the condition rather than the dispute.

**4. 03-batch-data-pipeline · spoofing** — labelled `same`. Reason recorded: Same action against the same target; the candidate only specifies how the attacker got write access.

**5. 03-batch-data-pipeline · denial-of-service** — labelled `same`. Reason recorded: Same action, same target.

**6. 03-batch-data-pipeline · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation, same mechanism.

**7. 05-cookbook-queue-webapp · spoofing** — labelled `same`. Reason recorded: Same action, same target.

**8. 06-cookbook-online-game · repudiation** — labelled `same`. Reason recorded: Same claim.

**9. 06-cookbook-online-game · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation, same target.

**10. 08-sso-identity-broker · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation, and the candidate assembles the same three stated facts.

**11. 08-sso-identity-broker · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation, same mechanism.

**12. 09-cookbook-sokify-retail · tampering** — labelled `same`. Reason recorded: Same action, same target.

**13. 11-sparse-shift-scheduling · repudiation** — labelled `same`. Reason recorded: Paraphrase with the same mechanism.

**14. 12-overclaiming-supplier-portal · denial-of-service** — labelled `same`. Reason recorded: Same claim; availability of a system we do not run is still an exposure worth reporting.

**15. 12-overclaiming-supplier-portal · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation, same target.

**16. 04-ml-inference-service · information-disclosure** — labelled `same`. Reason recorded: Same action, same target.

**17. 01-payments-checkout · spoofing** — labelled `same`. Reason recorded: Same action and target; the candidate cites the process where the reference cites the flow, which is an element-agreement difference, not a claim difference.

**18. 01-payments-checkout · tampering** — labelled `same`. Reason recorded: Narrower instance of the same action against the same target; still the same finding.

**19. 01-payments-checkout · elevation-of-privilege** — labelled `same`. Reason recorded: Coarser phrasing of the same escalation; the corpus also carries a separate pivot entry, so bipartite assignment decides which one it consumes.

**20. 02-iot-fleet-telemetry · tampering** — labelled `same`. Reason recorded: Same action, same target.

**21. 02-iot-fleet-telemetry · denial-of-service** — labelled `same`. Reason recorded: Same action, same target.

**22. 02-iot-fleet-telemetry · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation, same mechanism.

**23. 03-batch-data-pipeline · tampering** — labelled `same`. Reason recorded: Same action, same target and the same timing.

**24. 03-batch-data-pipeline · tampering** — labelled `same`. Reason recorded: Same action, same target.

**25. 03-batch-data-pipeline · denial-of-service** — labelled `same`. Reason recorded: Same action, same consequence.

**26. 03-batch-data-pipeline · elevation-of-privilege** — labelled `same`. Reason recorded: Same escalation; distinguished from the disclosure entry by what the attacker does with the credentials.

**27. 04-ml-inference-service · spoofing** — labelled `same`. Reason recorded: Same action, same target.

**28. 04-ml-inference-service · tampering** — labelled `same`. Reason recorded: Same action, same target.

**29. 04-ml-inference-service · denial-of-service** — labelled `same`. Reason recorded: Same action, same target.

**30. 05-cookbook-queue-webapp · tampering** — labelled `same`. Reason recorded: Same action, same target; the candidate names one instance of 'changes the endpoint or credentials'.

---

## What to do with your answers

Count them.

- **You wrote `same` for most of them.** The labels hold at their weakest point.
  The eval numbers have a floor under them, and the duplicate-threat work can
  proceed on the evidence it already has. Stop here — the other 309 pairs buy
  nothing.
- **You wrote `different` or `unclear` often** (say, more than 6 of 30). Stop the
  duplicate-threat work. The test set is unsound, and every quality number in the
  repo rests on it, including the judge's 90% bar. That is the bigger finding.

Record the count and the numbers you disagreed on. Both outcomes are worth
committing — a sitting that found nothing wrong is still the first evidence this
repo has that its test set means anything.
