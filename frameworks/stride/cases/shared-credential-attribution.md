# The Log That Names the Conduit

## Pattern

Several operator workstations and one automation job all reach an administrative API using the same API key. The API writes an audit record for every privileged action.

## Considered

Whether an audit record naming the key is enough to attribute an action.

## Ruling

Accepted: a repudiation finding, despite the audit log existing.

## Why

The presence of logging is what makes this easy to miss. Every action is recorded, the records are complete, and nothing about them looks wrong — but each names the credential, which four people and one job share. When an action is disputed, the record narrows the actor to a set of five and no further, and every one of them can truthfully say it was not necessarily them.

The finding needs a *disputable* action to be worth writing. A shared read-only credential on a metrics endpoint is a weaker version of the same fact and rarely rises to a threat. Privileged actions — a permission change, a data export, a refund, a production deploy — are where the inability to say who is a real consequence.

The identity weakness underneath belongs to the spoofing lane, and filing it here as well produces a duplicate. This lane's finding is about what the record can prove after the fact.

## What decided it

The credential's own words on the flow — "the same API key", "shared by every operator" — and the graded asset on the element it acts upon. The sharing is usually stated once, on one flow, rather than shown by two flows repeating a value: a submitter writes "they all use the same key" and never lists the users. Read the attribute, not the repetition.
