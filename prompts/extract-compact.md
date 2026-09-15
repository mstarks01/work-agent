## Output format: compact-v2

This job's schema replaces every element ID with a **ref**: a short handle you invent, written once on the element and again wherever something points at it. Code builds the real IDs from the names you give, exactly as rule 3 describes, so there is no `id` field here and you never write one.

Write a ref in lowercase letters, digits and hyphens — `api`, `orders-db`, `edge` — and keep it short. It is a handle for this response only, and expansion throws it away.

**A ref is unique among the elements a reference could confuse it with.** Two elements of the same kind never share one. A trust boundary and a thing inside it may: `card-processor` for both the external entity and its zone is fine, because a `trust_zone` can only mean the boundary and a flow endpoint can only mean the entity. A process and a data store may not, because a flow endpoint could mean either. A ref two elements claim that a field cannot tell apart points at neither, so every reference to it fails and the whole model goes back for repair.

Three fields hold a ref and nothing else:

- an element's `trust_zone`, which is the ref of a trust boundary;
- a flow's `source` and `destination`, each the ref of an external entity, a process or a data store;

**An assumption goes inside the element it is about.** Each element carries its own `assumptions` list, and an entry names the `attribute` you inferred, the assumption and the basis — rule 8's entry without its element ID, because the element is the one you wrote it in. An element you inferred nothing about carries none.

**Write `operations` on every flow.** This schema gives it no default, so a flow that leaves it out is malformed output rather than a flow whose operations nobody stated. Where the text says only that the two talk, write `unknown`.

You may leave out `description`, `assets`, `assumptions`, `source_speaker` and `notes` when you have nothing to put in them. Leave nothing else out. An omitted `source_excerpt` or `source_label` still fails rule 7, and an omitted attribute still fails the gate: the word for a fact the text does not state is `unknown`, never an absent field.

Every rule above this section still holds. Rule 3 still decides what you write in `name`, and the ID it describes is what code builds from that name.
