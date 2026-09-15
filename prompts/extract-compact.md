## Output format: compact-v3

This job's schema replaces every element ID with a **ref**: a short handle you invent, written once on the element and again wherever something points at it. Code builds the real IDs from the names you give, exactly as rule 3 describes, so there is no `id` field here and you never write one.

Write a ref as a **type letter, a colon and a short slug**: `p:api`, `s:orders-db`, `e:shopper`, `b:edge`, `f:login`. The letter says what kind of thing the ref names — `e` an external entity, `p` a process, `s` a data store, `f` a data flow, `b` a trust boundary — and it is the same type the ID would have spelled out. A ref is a handle for this response only, and expansion throws it away.

**A ref is unique among the elements of its own type.** The letter keeps the types apart, so the card processor and the zone around it are `e:card-processor` and `b:card-processor` and neither is ambiguous — name them alike where the text does. Two elements of the *same* type may not share a ref: that one points at neither, so every reference to it fails and the whole model goes back for repair.

Four fields hold a ref and nothing else:

- an element's `trust_zone`, which is the ref of a trust boundary;
- a flow's `source` and `destination`, each the ref of an external entity, a process or a data store;
- an assumption's `element`, the ref of any element.

**An assumption names its element and its attribute together.** The top-level `assumptions` list is rule 8's, with `element` holding the ref instead of an ID — the field is `element` here, not `element_id`. Name the element the fact is true *of*, which is not always the one you were reading about: a store's `data_classification` is the store's, never the flow that carries the data or the process that handles it.

**Write `operations` on every flow.** This schema gives it no default, so a flow that leaves it out is malformed output rather than a flow whose operations nobody stated. Where the text says only that the two talk, write `unknown`.

You may leave out `description`, `assets`, `source_speaker` and `notes` when you have nothing to put in them. Leave nothing else out. An omitted `source_excerpt` or `source_label` still fails rule 7, and an omitted attribute still fails the gate: the word for a fact the text does not state is `unknown`, never an absent field.

Every rule above this section still holds. Rule 3 still decides what you write in `name`, and the ID it describes is what code builds from that name.
