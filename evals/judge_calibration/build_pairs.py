"""Assemble the judge-calibration fixtures from hand-labelled tuples.

``LABELS`` is the hand-labelling itself (wayfinder ticket 022, ticket 009
decision 13): one tuple per candidate pair, each label decided by a human
reading the pair, each carrying the rationale that decided it. Reference claims
are pulled verbatim from each case's ``threats.json`` by index, so a reworded
reference cannot silently detach a fixture from the claim it was labelled
against — ``verify_corpus.py`` fails when it does.

Editing fixtures means editing ``LABELS`` and re-running this; ``pairs.json`` is
generated and should never be hand-edited.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "corpus"
OUT = HERE / "pairs.json"

# (case, reference index, candidate claim, label, note)
LABELS = [
    # ---------------------------------------------------------------- case 01
    ("01-payments-checkout", 0, "An attacker steals a shopper's session cookie and uses it to submit orders on that shopper's account.", "match", "Pure paraphrase: same action (replaying a stolen session), same target."),
    ("01-payments-checkout", 0, "An attacker guesses a shopper's password because no MFA is enforced on shopper accounts.", "no-match", "Same element and same weakness area, different attacker action: credential guessing is not session replay."),
    ("01-payments-checkout", 1, "An attacker sends a fake payment-settlement callback so an unpaid order is recorded as paid.", "match", "Same action against the same target; the candidate omits 'impersonating the processor' but that is how the action is performed."),
    ("01-payments-checkout", 1, "An attacker replays a genuine settlement webhook the processor sent earlier.", "no-match", "Replay of an authentic message is a distinct action from forging one; the corpus files replay under tampering."),
    ("01-payments-checkout", 2, "A compromised workload in the internal network calls the order service's gRPC endpoint, which accepts it without authentication.", "match", "Same action and target; the candidate cites the process where the reference cites the flow, which is an element-agreement difference, not a claim difference."),
    ("01-payments-checkout", 3, "An attacker reads the database password out of the order service's environment and logs in to orders-db.", "no-match", "The reference is about using a held credential; this candidate's action is recovering it. The corpus files recovery separately under information-disclosure."),
    ("01-payments-checkout", 4, "An attacker intercepts the gRPC call between the storefront API and the order service and rewrites the order before it is captured.", "match", "Same action, same flow."),
    ("01-payments-checkout", 5, "An attacker with database access rewrites order prices directly in orders-db.", "match", "Narrower instance of the same action against the same target; still the same finding."),
    ("01-payments-checkout", 5, "An attacker deletes order rows from orders-db to destroy records of a purchase.", "no-match", "Destruction rather than alteration, and the corpus does not carry it; a judge that matches this inflates recall."),
    ("01-payments-checkout", 8, "A shopper claims they never placed an order and the system cannot prove otherwise, because receipts identify only the writing service.", "match", "Paraphrase with the same mechanism."),
    ("01-payments-checkout", 8, "An attacker deletes receipts from the archive so an order cannot be proven.", "no-match", "Deleting evidence is tampering with the archive, a different action from the absence of shopper identity in it."),
    ("01-payments-checkout", 11, "An attacker who gets hold of a database backup reads shopper addresses and card last-four, since at-rest protection is unverified.", "match", "Backups are named in the reference; same action, same target."),
    ("01-payments-checkout", 12, "An attacker sniffing internal traffic reads customer identifiers from the storefront-to-order-service call.", "match", "Same read action on the same flow."),
    ("01-payments-checkout", 12, "An attacker sniffing the shopper's HTTPS connection reads their card details.", "no-match", "Same lane and a plausible finding, but a different flow and a different target; the corpus does not list it."),
    ("01-payments-checkout", 15, "An attacker sends a high volume of requests to the webhook endpoint until the storefront API becomes unresponsive.", "match", "Same action, same target."),
    ("01-payments-checkout", 18, "An attacker who lands anywhere in the storefront tier can write orders in the core zone, because the order service trusts network position.", "match", "Same escalation, same mechanism."),
    ("01-payments-checkout", 18, "An attacker compromises the storefront API and moves into the core network.", "match", "Coarser phrasing of the same escalation; the corpus also carries a separate pivot entry, so bipartite assignment decides which one it consumes."),
    # ---------------------------------------------------------------- case 02
    ("02-iot-fleet-telemetry", 0, "An attacker recovers the shared key from one device and publishes telemetry impersonating any other node.", "match", "Same action, same mechanism."),
    ("02-iot-fleet-telemetry", 0, "An attacker brute-forces the fleet pre-shared key against the broker.", "no-match", "Recovering the key by physical extraction and guessing it are different attacker actions."),
    ("02-iot-fleet-telemetry", 1, "Someone with physical access to a node uses the serial console, whose authentication the model does not establish.", "match", "Same action and target; hedged phrasing does not change the claim."),
    ("02-iot-fleet-telemetry", 3, "An attacker uploads a malicious firmware image to the bucket and every node that polls installs it.", "match", "Same action, same target."),
    ("02-iot-fleet-telemetry", 3, "An attacker modifies firmware in transit between the bucket and a node.", "no-match", "In-transit modification is a distinct action from planting the artifact at rest."),
    ("02-iot-fleet-telemetry", 4, "An attacker with the fleet key sends bogus sensor readings that end up stored as genuine customer data.", "match", "Same action, same target."),
    ("02-iot-fleet-telemetry", 4, "An attacker with the fleet key publishes readings as another device.", "no-match", "This is the spoofing claim, not the data-integrity one; keeping them distinct is what the two lanes are for."),
    ("02-iot-fleet-telemetry", 6, "There is no way to tell which physical device produced a disputed reading, because every node presents the same credential.", "match", "Same claim, stated as the condition rather than the dispute."),
    ("02-iot-fleet-telemetry", 8, "An attacker reading the telemetry lake learns when customer sites are unoccupied.", "match", "Same read action against the same target; the candidate names the consequence the reference's notes give."),
    ("02-iot-fleet-telemetry", 9, "An attacker on the network path between a node and the broker captures the pre-shared key from an unencrypted MQTT session.", "match", "Same action, same flow."),
    ("02-iot-fleet-telemetry", 10, "Anyone on the internet downloads the firmware images because the bucket allows unauthenticated reads.", "match", "Same action, same target; the reference adds the reverse-engineering motive."),
    ("02-iot-fleet-telemetry", 10, "An attacker writes a new firmware image to the public bucket.", "no-match", "Read and write are different actions, and the write is a separate corpus entry."),
    ("02-iot-fleet-telemetry", 12, "An attacker opens huge numbers of MQTT connections to the gateway so real devices cannot connect.", "match", "Same action, same target."),
    ("02-iot-fleet-telemetry", 13, "An attacker publishes firmware that permanently disables the nodes that install it.", "match", "Same action, same target."),
    ("02-iot-fleet-telemetry", 15, "An attacker who can write firmware runs their own code on every device in the fleet.", "match", "Same escalation."),
    ("02-iot-fleet-telemetry", 16, "An attacker who physically takes one node extracts its credential and then acts with the authority of the entire fleet.", "match", "Same escalation, same mechanism."),
    ("02-iot-fleet-telemetry", 17, "A fleet operator's SSO account is phished and the attacker queries the telemetry lake.", "no-match", "The reference is about an authorized operator's over-broad grant; account takeover is a different action the corpus does not list."),
    # ---------------------------------------------------------------- case 03
    ("03-batch-data-pipeline", 0, "An attacker who steals a partner's SFTP key uploads extracts in that partner's name.", "match", "Same action, same target."),
    ("03-batch-data-pipeline", 1, "An attacker drops a file into a partner folder and the nightly run ingests it as that partner's data, since the depositor is never checked.", "match", "Same action, same mechanism."),
    ("03-batch-data-pipeline", 1, "An attacker with a valid partner key uploads a file to a folder belonging to a different partner.", "match", "Same action against the same target; the candidate only specifies how the attacker got write access."),
    ("03-batch-data-pipeline", 3, "An attacker edits claim records sitting in the landing bucket before the 02:00 run picks them up.", "match", "Same action, same target and the same timing."),
    ("03-batch-data-pipeline", 3, "An attacker injects SQL through a claim record field that the transform does not sanitize.", "no-match", "Injection through unvalidated content is a distinct action the corpus does not carry; a judge that matches it hides a real gap."),
    ("03-batch-data-pipeline", 4, "An attacker changes an Airflow connection string so the pipeline reads from or writes to a system they control.", "match", "Same action, same target."),
    ("03-batch-data-pipeline", 6, "Nothing ties an uploaded file to the key that uploaded it, so a partner can disown an extract.", "match", "Same claim stated cause-first."),
    ("03-batch-data-pipeline", 8, "An analyst with dataset-wide access views member names and dates of birth they have no need for.", "match", "Same action, same target."),
    ("03-batch-data-pipeline", 8, "An analyst exports member records out of the warehouse to an unmanaged device.", "no-match", "Exfiltration by an authorized user is a different action from over-broad read access; the corpus does not carry it."),
    ("03-batch-data-pipeline", 9, "An attacker who reaches Airflow's metadata database reads every partner key and connection string stored in it.", "match", "Same action, same target."),
    ("03-batch-data-pipeline", 9, "An attacker reads the partner keys out of the landing bucket.", "no-match", "The keys are not in the bucket; the candidate asserts a fact the model does not support and should be adjudicated ungrounded, not matched."),
    ("03-batch-data-pipeline", 12, "An attacker uploads a file large enough that the nightly job never finishes and genuine extracts go unprocessed.", "match", "Same action, same target."),
    ("03-batch-data-pipeline", 13, "An attacker crafts an extract that crashes the Spark job on every retry, so the warehouse silently stops being updated.", "match", "Same action, same consequence."),
    ("03-batch-data-pipeline", 14, "An attacker in the landing network reads Airflow's metadata database and obtains credentials for the systems downstream of it.", "match", "Same escalation; distinguished from the disclosure entry by what the attacker does with the credentials."),
    ("03-batch-data-pipeline", 15, "An attacker who can place a file in the landing bucket obtains code execution in the warehouse network via the job that processes it.", "match", "Same escalation, same mechanism."),
    ("03-batch-data-pipeline", 16, "An analyst queries claims belonging to partners outside their assigned remit.", "match", "Same action, same target."),
    ("03-batch-data-pipeline", 16, "An analyst escalates to a warehouse administrator role.", "no-match", "Role escalation is a different action from using an over-broad grant, and nothing in the model supports it."),
    # ---------------------------------------------------------------- case 04
    ("04-ml-inference-service", 0, "An attacker with a leaked API key keeps calling the inference gateway indefinitely, since keys are never expired.", "match", "Same action, same target."),
    ("04-ml-inference-service", 0, "An attacker brute-forces a valid API key against the gateway.", "no-match", "Guessing a key and using a leaked one are different actions."),
    ("04-ml-inference-service", 1, "Anything already inside the model network can send requests straight to the model server, which authenticates nothing.", "match", "Same action, same target."),
    ("04-ml-inference-service", 3, "An attacker replaces the model artifact in the registry and the model server loads the substituted model without checking it.", "match", "Same action, same target."),
    ("04-ml-inference-service", 3, "An attacker poisons the training data so the published model behaves incorrectly.", "no-match", "Training-time poisoning is outside this model entirely — no training pipeline exists in it — so this is ungrounded, not a match."),
    ("04-ml-inference-service", 4, "An attacker with access to the model network writes new values into Redis and changes the features the model scores on.", "match", "Same action, same target."),
    ("04-ml-inference-service", 4, "An attacker reads customer features out of the unauthenticated Redis store.", "no-match", "Reading is the disclosure entry; writing is this one. Same element, different action."),
    ("04-ml-inference-service", 6, "Because artifacts are published from a shared account, there is no record of which individual published a given model.", "match", "Same claim."),
    ("04-ml-inference-service", 8, "An attacker who reaches the BigQuery inference log reads the raw prompts end users typed.", "match", "Same action, same target."),
    ("04-ml-inference-service", 8, "An attacker reads model responses out of the inference log.", "match", "Same action, same target; requests and responses are stored together and the reference covers the store."),
    ("04-ml-inference-service", 10, "A caller crafts an input that makes the model reveal another tenant's customer features in its output.", "match", "Same action, same target."),
    ("04-ml-inference-service", 10, "A caller sends a prompt that makes the model ignore its instructions and produce disallowed output.", "no-match", "Instruction-following failure is not disclosure of another tenant's data; the corpus does not carry it."),
    ("04-ml-inference-service", 12, "A caller with a valid key sends enough inference requests to starve every other team of GPU capacity.", "match", "Same action, same target."),
    ("04-ml-inference-service", 13, "An attacker with model-network access issues a Redis command that wipes the feature store, stalling inference.", "match", "Same action, same target."),
    ("04-ml-inference-service", 15, "An attacker who can publish to the registry achieves code execution on the GPU nodes when the artifact is loaded.", "match", "Same escalation."),
    ("04-ml-inference-service", 16, "An attacker who takes over the gateway reaches the model server, the feature store and the registry, none of which authenticate it.", "match", "Same escalation; the candidate enumerates what the reference states generally."),
    ("04-ml-inference-service", 17, "A calling team invokes a model endpoint their key was never meant to reach.", "match", "Same action, same target."),
    # ---------------------------------------------------------------- case 05
    ("05-cookbook-queue-webapp", 1, "An attacker who can reach the message queue writes job messages that the worker treats as coming from the web application.", "match", "Same action, same target."),
    ("05-cookbook-queue-webapp", 3, "An attacker puts a crafted message on the queue and the background worker processes it as if it were legitimate work.", "match", "Same action, same target."),
    ("05-cookbook-queue-webapp", 3, "An attacker enqueues jobs as the web application, since the queue does not authenticate producers.", "no-match", "This is the spoofing claim about producer identity; the reference is about the content the worker acts on."),
    ("05-cookbook-queue-webapp", 4, "An attacker who can write the web application's config points it at a queue they control.", "match", "Same action, same target; the candidate names one instance of 'changes the endpoint or credentials'."),
    ("05-cookbook-queue-webapp", 6, "An attacker who controls the worker deletes the log entries recording their activity, because those logs sit in the database the worker writes.", "match", "Same action, same mechanism."),
    ("05-cookbook-queue-webapp", 6, "An attacker floods the database so logging stops.", "no-match", "Availability against the log store is a different action from erasing entries, and the corpus files it under denial-of-service."),
    ("05-cookbook-queue-webapp", 8, "An attacker who compromises the background worker recovers the database credentials held in its config.", "match", "Same action, same target."),
    ("05-cookbook-queue-webapp", 9, "An attacker who compromises the web application recovers the message-queue credentials from its config store.", "match", "Same action, same target."),
    ("05-cookbook-queue-webapp", 9, "An attacker who compromises the web application recovers the database credentials from its config store.", "no-match", "The web application's config holds queue credentials, not database ones; asserting otherwise is ungrounded in the model."),
    ("05-cookbook-queue-webapp", 10, "An attacker with access to the database's storage reads the records it holds, since nothing states it is encrypted at rest.", "match", "Same action, same target."),
    ("05-cookbook-queue-webapp", 11, "An attacker on the internal network reads job messages moving between the application, the queue and the worker.", "match", "Same action; the reference cites two flows and the candidate covers both."),
    ("05-cookbook-queue-webapp", 12, "An attacker enqueues far more jobs than the worker can process, so queued work stops completing.", "match", "Same action, same target."),
    ("05-cookbook-queue-webapp", 12, "An attacker deletes messages from the queue so submitted work is silently dropped.", "no-match", "Deletion is a distinct action the corpus does not carry; it is grounded, so it belongs in the valid-unlisted bucket rather than matched."),
    ("05-cookbook-queue-webapp", 15, "An attacker who compromises the web application uses its queue credentials to act inside the backend tier.", "match", "Same escalation, same route."),
    ("05-cookbook-queue-webapp", 16, "An attacker who achieves execution in the worker gets whatever database rights the worker holds, which are not stated to be restricted.", "match", "Same escalation."),
    ("05-cookbook-queue-webapp", 16, "An attacker who achieves execution in the worker reads its config file.", "no-match", "Reading the config is the disclosure entry; inheriting database privilege is this one."),
    ("05-cookbook-queue-webapp", 0, "An attacker accesses the application as a signed-in user because the model does not establish how users are authenticated.", "match", "Same action, same target, same unverified-control basis."),
    # ---------------------------------------------------------------- case 06
    ("06-cookbook-online-game", 0, "An attacker connects to the lobby's exposed port and is accepted as another player, since client authentication is unverified.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 1, "An attacker connects straight to a game server without going through matchmaking and joins a match they were not assigned to.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 1, "An attacker floods a game server's port so the match cannot proceed.", "no-match", "Same element, availability action rather than identity; the corpus files it under denial-of-service."),
    ("06-cookbook-online-game", 3, "A player patches their local game client and sends gameplay messages the server accepts as valid.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 3, "A player uses their modified client to see other players' positions through walls.", "no-match", "Disclosure through the client is a separate corpus entry; modifying outbound actions and observing extra state are different actions."),
    ("06-cookbook-online-game", 4, "An attacker rewrites entries in the stats database to inflate their ranking.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 6, "Nothing records which support agent performed a given moderation action on a player account.", "match", "Same claim."),
    ("06-cookbook-online-game", 6, "A support agent bans a player account without justification.", "no-match", "Misuse of legitimate privilege is a different claim from the absence of attribution; the corpus's elevation entry covers scope, not this."),
    ("06-cookbook-online-game", 8, "An attacker who reaches the player database reads player account records, since nothing states how the store is protected.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 9, "An attacker capturing traffic on the client's connections reads player identifiers, because neither client link is stated to be encrypted.", "match", "Same action, same flows."),
    ("06-cookbook-online-game", 10, "A player reads state from their own client that the server sent but should not have exposed, such as opponents' locations.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 11, "A support agent browses player account data unrelated to any moderation case they are working.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 12, "An attacker overwhelms the lobby with connections and players can no longer get into matches.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 13, "An attacker floods a game server mid-match and disrupts play for everyone in that match.", "match", "Same action, same target."),
    ("06-cookbook-online-game", 15, "An attacker who takes over a game server writes to player records across production.", "match", "Same escalation."),
    ("06-cookbook-online-game", 15, "An attacker who takes over a game server reads match statistics.", "no-match", "Reading stats is neither the escalation claimed nor the target claimed; the reference is about write access to player records."),
    ("06-cookbook-online-game", 16, "An attacker who gets into the moderation website gains control over every player account it can act on.", "match", "Same escalation, same target."),
    ("06-cookbook-online-game", 17, "A player manipulates their client to obtain a match placement or account state they should not have.", "match", "Same escalation, same target."),
    # ------------------------------------------------ additional hard negatives
    # Deliberately weighted toward the errors that cost most: a judge that says
    # "match" too readily inflates recall silently, so the fixture set carries
    # roughly one hard negative for every two positives.
    ("01-payments-checkout", 0, "An attacker fixes a shopper's session identifier before they log in and then reuses it.", "no-match", "Session fixation is a distinct action from replaying a stolen cookie."),
    ("01-payments-checkout", 2, "An attacker sends malformed gRPC messages that crash the order service.", "no-match", "Same flow, availability action rather than identity."),
    ("01-payments-checkout", 11, "An attacker with database access reads shopper addresses and card last-four by querying orders-db.", "no-match", "The reference is about protection at rest of the stored data; querying with a valid credential is the elevation/spoofing path, not this one."),
    ("01-payments-checkout", 16, "An attacker causes the order service to leak memory until it restarts.", "no-match", "Resource exhaustion inside the process is not the database-connection exhaustion the reference claims, and nothing in the model supports it."),
    ("01-payments-checkout", 20, "An attacker compromises the card processor and uses its access to reach the core zone.", "no-match", "Third-party compromise as an escalation route is not the DMZ-to-core pivot the reference states."),
    ("02-iot-fleet-telemetry", 2, "An attacker registers a device that was never manufactured by the operator.", "no-match", "Device enrolment is not described anywhere in the model; ungrounded rather than matched."),
    ("02-iot-fleet-telemetry", 5, "An attacker deletes a device's entry from the registry so it can no longer connect.", "no-match", "Deletion for availability is a different action from reassigning a node's customer."),
    ("02-iot-fleet-telemetry", 11, "An attacker reads customer occupancy patterns from the device registry.", "no-match", "Occupancy data lives in the lake, not the registry; the candidate asserts a fact the model does not support."),
    ("02-iot-fleet-telemetry", 14, "An attacker disables the Pub/Sub topic so readings never reach the normalizer.", "no-match", "Destroying the transport is a different action from flooding it, and the model does not describe who can administer it."),
    ("03-batch-data-pipeline", 2, "An attacker cancels a scheduled Airflow run so the extract is never processed.", "no-match", "Availability against the scheduler, not impersonation of it."),
    ("03-batch-data-pipeline", 5, "An attacker modifies the Spark job's code so every future run transforms records incorrectly.", "no-match", "Job code deployment is not modelled; grounded findings must attach to elements that exist."),
    ("03-batch-data-pipeline", 7, "An analyst cannot reproduce a warehouse figure because the transform logic is undocumented.", "no-match", "A documentation gap is not an attacker action at all; a judge that matches this is matching on topic, not on claim."),
    ("03-batch-data-pipeline", 10, "An attacker intercepts partner uploads and reads claim records in transit over SFTP.", "no-match", "The reference is about data at rest in the bucket; the flow is also stated to carry SSH transport encryption."),
    ("04-ml-inference-service", 2, "An attacker steals an ML engineer's laptop and publishes an artifact from it.", "no-match", "Device theft is not described in the model; the reference is about the shared account behind publication."),
    ("04-ml-inference-service", 5, "An attacker replays a previously captured inference request against the model server.", "no-match", "Replay is a distinct action from altering a payload in flight."),
    ("04-ml-inference-service", 9, "An attacker reads customer features from the inference log.", "no-match", "Features are held in Redis; the log holds prompts and responses. Wrong target, and unsupported by the model."),
    ("04-ml-inference-service", 14, "An attacker corrupts the feature store so the model server cannot start.", "no-match", "The startup dependency in the reference is the registry artifact, not the feature store."),
    ("04-ml-inference-service", 17, "A calling team exceeds the request quota assigned to its key.", "no-match", "No quota is described in the model, and exceeding one is not reaching an unentitled capability."),
    ("05-cookbook-queue-webapp", 2, "An attacker who reaches the database queries it directly without any credential.", "no-match", "Asserts an absent control the model never states; the reference is about using the worker's credentials."),
    ("05-cookbook-queue-webapp", 5, "An attacker alters the message queue's contents after the worker has read them.", "no-match", "Incoherent against the model's flows and not the database-record tampering the reference claims."),
    ("05-cookbook-queue-webapp", 13, "An attacker exploits a vulnerability in the web application framework to crash it.", "no-match", "The model names no framework; the reference's action is flooding, not exploiting."),
    ("05-cookbook-queue-webapp", 15, "An attacker who compromises the web application reads the database directly.", "no-match", "The web application has no path to the database in this model; the escalation route the reference states is the queue."),
    ("06-cookbook-online-game", 0, "An attacker takes over a player's account by resetting its password.", "no-match", "No account-recovery path exists in the model; ungrounded rather than a match."),
    ("06-cookbook-online-game", 5, "An attacker modifies the game client to report fabricated match results.", "match", "Same action and target as the reference's fabricated progression, reached through the client the model already marks untrusted."),
    ("06-cookbook-online-game", 7, "A player disputes a moderation decision and no record shows which agent made it.", "no-match", "That is the support-attribution reference; this one is about which writer changed a player record."),
    ("06-cookbook-online-game", 14, "An attacker corrupts the player database so matchmaking returns wrong results.", "no-match", "Integrity action, not the resource exhaustion the reference claims."),
]


def main() -> None:
    threats_by_case = {
        case_dir.name: json.loads((case_dir / "threats.json").read_text())
        for case_dir in sorted(CORPUS.iterdir())
        if case_dir.is_dir()
    }
    pairs = []
    for case, index, candidate, label, note in LABELS:
        reference = threats_by_case[case][index]
        pairs.append(
            {
                "case": case,
                "category": reference["category"],
                "reference_claim": reference["claim"],
                "candidate_claim": candidate,
                "label": label,
                "note": note,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(pairs, indent=2) + "\n")
    matches = sum(1 for pair in pairs if pair["label"] == "match")
    print(f"{len(pairs)} pairs: {matches} match, {len(pairs) - matches} no-match")


if __name__ == "__main__":
    main()
