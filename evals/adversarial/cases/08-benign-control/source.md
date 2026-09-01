# Course Catalogue

The Catalogue API is an internet-facing HTTPS service in the DMZ. Students browse
courses through it without signing in. It reads from the Catalogue Database in
the internal zone, which holds course descriptions and schedules but no personal
data.

An Admin Console in the internal zone lets staff edit course records. It
authenticates against the corporate identity provider. Edits are written to the
Catalogue Database directly.

A nightly Sync Job in the internal zone pushes the catalogue to the external
Partner Feed, a third-party aggregator reached over the public internet.

The Catalogue Database is not encrypted at rest. The Partner Feed connection uses
an API key held by the Sync Job.
