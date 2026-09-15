## Output format: compact-v1

This job's schema replaces every element ID with a **ref**: a short handle you invent, written once on the element and again wherever something points at it. Code builds the real IDs from the names you give, exactly as rule 3 describes, so there is no `id` field here and you never write one.

Write a ref in lowercase letters, digits and hyphens — `api`, `orders-db`, `edge` — and keep it short. It is a handle for this response only, and expansion throws it away.

**Give each element its own ref.** A ref that two elements claim points at neither of them, so every reference to it fails and the whole model goes back for repair. Refs are unique across all five lists together, not inside one list.

Four fields hold a ref and nothing else:

- an element's `trust_zone`, which is the ref of a trust boundary;
- a flow's `source` and `destination`, each the ref of an external entity, a process or a data store;
- an assumption's `element`, the ref of any element. The field is `element` here, not `element_id`.

**Write `operations` on every flow.** This schema gives it no default, so a flow that leaves it out is malformed output rather than a flow whose operations nobody stated. Where the text says only that the two talk, write `unknown`.

You may leave out `description`, `assets`, `source_speaker` and `notes` when you have nothing to put in them. Leave nothing else out. An omitted `source_excerpt` or `source_label` still fails rule 7, and an omitted attribute still fails the gate: the word for a fact the text does not state is `unknown`, never an absent field.

Every rule above this section still holds. Rule 3 still decides what you write in `name`, and the ID it describes is what code builds from that name.
