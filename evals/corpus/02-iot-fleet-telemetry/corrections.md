# Bootstrap → blessed corrections: 02-iot-fleet-telemetry

Bootstrap provenance: agent stand-in for `extract` (see `../../BLESSING.md`).
Applying these in reverse to `model.json` reconstructs the bootstrap artifact.

| # | Path | Bootstrap value | Blessed value | Why (source text) |
|---|---|---|---|---|
| 1 | `flow:sensor-node-to-device-gateway:publish-readings.encryption_in_transit` | `TLS` | `unknown` | The text says MQTT, never MQTTS or TLS. The bootstrap supplied the transport protection a reader expects on a device fleet. |
| 2 | `flow:sensor-node-to-device-gateway:publish-readings.authentication` | `pre-shared key` | `fleet-wide pre-shared key, shared by every device and never rotated` | The two facts that make this the case's headline threat — fleet-wide, never rotated — were dropped from the attribute even though the element's `source_excerpt` carried them. Analysts read attributes. |
| 3 | `store:firmware-bucket.data_classification` | `internal` | `public` | Stated as public read. The bootstrap classified it by what the operator would want rather than by what the text says. |
| 4 | `entity:field-technician` | (absent) | present, with its flow | The technician was mentioned in a short paragraph away from the main data path and the bootstrap dropped both the actor and the serial-console flow. Elements introduced late in a description are where extraction under-reports. |
| 5 | `flow:sensor-node-to-firmware-bucket:poll-firmware` | source `store:firmware-bucket`, destination `entity:sensor-node` | reversed | Direction is who initiates: the nodes poll. The bootstrap modelled the data's direction of travel instead. |
| 6 | `store:device-registry.assets` | `[]` | `["credentials"]` | The registry holds the key check; the bootstrap tagged only stores whose names sound like data. |
| 7 | `assumptions` | (empty) | two entries | The bootstrap wrote `public` exposure facts and a `pii`-ish classification on the lake without recording either as an inference. |

## Signal

Repeats case 01's pattern — invented transport protection (1), flattened
attribute detail (2) — and adds two new ones worth tracking: **direction
reversal on poll/pull flows** (5) and **whole elements dropped when they appear
outside the main narrative path** (4). Both are extraction-eval failures that an
end-to-end-only fixture would have attributed to the analysts.
